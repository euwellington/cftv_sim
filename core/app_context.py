"""AppContext: junta barramento, banco, equipamentos, armazenamento e servicos opcionais.

A GUI conversa apenas com este objeto.  Servicos das etapas seguintes
(streaming, gravacao, API, tunel) se registram com ``register_service`` e sao
iniciados/parados junto com o contexto, sempre fora da thread da UI.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Protocol

from . import config
from .db import Database
from .device_manager import DeviceManager, save_profile
from .event_bus import EventBus, EventType
from .fmt import primary_ip
from .models import Profile
from recording.storage import StorageManager

log = logging.getLogger(__name__)


class Service(Protocol):
    name: str

    def start(self) -> None: ...
    def stop(self) -> None: ...


class AppContext:
    """Estado compartilhado da aplicacao para um perfil carregado."""

    def __init__(self, profile: Profile, db_path: Optional[str] = None) -> None:
        config.ensure_dirs()
        self.profile = profile
        self.settings = config.load_settings(profile.settings)
        self.bus = EventBus()
        self.db = Database(db_path or (config.DATA_DIR / "cftv.db"))
        self.manager = DeviceManager(self.bus, self.db, self.settings)
        self.manager.load(profile)
        self.storage = StorageManager(self.db, config.storage_root(self.settings), profile.devices, self.bus)
        self.services: list[Any] = []
        self.service_errors: dict[str, str] = {}
        self.tunnel: Any = None  # TunnelProvider (etapa 8)
        self.public_url: str = ""
        self.started = False
        self.tcp_endpoints: dict[int, tuple[str, int]] = {}  # porta RTSP local -> (host, porta) do ngrok TCP
        self.public_ip = ""  # preenchido sob demanda (consulta externa)
        self.admin_user = "admin"
        self.admin_password = ""  # so em memoria (nunca gravada); vazia = desconhecida
        self._load_optional_services()

    # ------------------------------------------------------------------ servicos
    def register_service(self, svc: Service) -> None:
        self.services.append(svc)

    def _load_optional_services(self) -> None:
        """Registra servicos das etapas 2-8 se os modulos existirem."""
        for modname, factory in (
            ("streaming.service", "create_service"),
            ("recording.service", "create_service"),
            ("api.service", "create_service"),
        ):
            try:
                mod = __import__(modname, fromlist=[factory])
            except ModuleNotFoundError as exc:
                if exc.name in (modname, modname.split(".")[0]):
                    continue  # modulo ainda nao existe nesta build
                self.service_errors[modname] = str(exc)
                continue
            except Exception as exc:  # noqa: BLE001
                self.service_errors[modname] = str(exc)
                log.exception("falha ao importar %s", modname)
                continue
            try:
                self.register_service(getattr(mod, factory)(self))
            except Exception as exc:  # noqa: BLE001
                self.service_errors[modname] = str(exc)
                log.exception("falha ao criar servico %s", modname)

    # ------------------------------------------------------------------ ciclo de vida
    def start(self) -> None:
        """Inicia equipamentos e servicos (chame em thread de trabalho)."""
        self.manager.start()
        for svc in self.services:
            try:
                svc.start()
                log.info("servico %s iniciado", getattr(svc, "name", svc))
            except Exception as exc:  # noqa: BLE001
                self.service_errors[getattr(svc, "name", str(svc))] = str(exc)
                log.exception("falha ao iniciar servico %s", svc)
        self.started = True

    def stop(self) -> None:
        for svc in reversed(self.services):
            try:
                svc.stop()
            except Exception:  # noqa: BLE001
                log.exception("falha ao parar servico %s", svc)
        self.manager.stop()
        self.started = False

    # ------------------------------------------------------------------ operacoes (usadas por GUI/portal/APIs)
    def save(self) -> None:
        save_profile(self.profile)

    def set_recording(self, device_id: str, channel: int, on: bool, kind: str = "manual") -> bool:
        """Liga/desliga a gravacao manual. Usa o servico de gravacao, se houver."""
        cam = self.manager.camera(device_id, channel)
        if on and not cam.is_online:
            return False
        rec = next((s for s in self.services if hasattr(s, "set_manual")), None)
        if rec is not None:
            rec.set_manual(device_id, channel, on)
        cam.recording = on
        cam.recording_kind = kind if on else ""
        self.bus.publish(EventType.RECORDING_START if on else EventType.RECORDING_STOP, device_id, channel, kind=kind)
        return True

    def reboot_camera(self, device_id: str, channel: int, user: str = "GUI") -> float:
        dev = self.manager.get(device_id)
        self.db.add_log("operation", f"Reboot do canal {channel} solicitado", device_id, user)
        return dev.reboot_camera(channel)

    def reboot_system(self, device_id: str, user: str = "GUI") -> float:
        self.db.add_log("operation", "Reboot do sistema solicitado", device_id, user)
        return self.manager.get(device_id).reboot_system()

    # ------------------------------------------------------------------ URLs
    def host(self) -> str:
        bind = self.profile.devices[0].bind_ip if self.profile.devices else "0.0.0.0"
        return primary_ip() if bind in ("0.0.0.0", "") else bind

    def web_url(self, host: Optional[str] = None) -> str:
        return f"http://{host or self.host()}:{self.profile.web_port}"

    def set_credentials(self, user: str, password: str) -> None:
        self.admin_user, self.admin_password = user, password

    def _rtsp_cred(self, pw: str) -> tuple[str, str]:
        """Usuario/senha para a URL RTSP; vazios quando a autenticacao RTSP esta desligada."""
        if not self.profile.rtsp_auth:
            return "", ""
        return self.admin_user, pw

    def access_rows(self, host: Optional[str] = None, ngrok_tcp: bool = False) -> list[dict]:
        """Linhas para a barra fixa: por equipamento/canal/stream, URL HTTP e RTSP reais."""
        from . import urls
        h = host or self.host()
        pw = self.admin_password or "<senha>"
        rows = []
        for d in self.profile.devices:
            for ch in d.channels:
                for st, label in ((0, "principal"), (1, "extra")):
                    ep = self.tcp_endpoints.get(d.rtsp_port) if ngrok_tcp else None
                    hp = self.tcp_endpoints.get(d.http_port) if ngrok_tcp else None
                    rows.append({"device": d.id, "name": d.name, "channel": ch.index, "stream": st,
                                 "label": f"{d.name} - CH{ch.index} {label}",
                                 "http": (urls.http_url(d, hp[0], self.admin_user, pw, hp[1]) if hp else urls.http_url(d, h, self.admin_user, pw)),
                                 "rtsp": (urls.rtsp_url(d, ch.index, st, ep[0], *self._rtsp_cred(pw), ep[1]) if ep
                                         else urls.rtsp_url(d, ch.index, st, h, *self._rtsp_cred(pw)))})
        return rows

    def http_urls(self, host: Optional[str] = None) -> dict[str, str]:
        h = host or self.host()
        return {d.id: f"http://{h}:{d.http_port}" for d in self.profile.devices}

    # ------------------------------------------------------------------ tunel (modo publico)
    def start_tunnel(self) -> str:
        """Inicia o tunel configurado no perfil e devolve a URL publica (chame em thread de trabalho)."""
        try:
            from tunnel.factory import create_provider
        except ModuleNotFoundError as exc:
            if exc.name and exc.name.split(".")[0] == "tunnel":
                raise RuntimeError("Pasta 'tunnel/' ausente: copie o projeto atualizado inteiro.") from exc
            raise RuntimeError(f"Dependencia ausente: {exc.name}. Rode: pip install -r requirements.txt") from exc
        if self.tunnel is not None:
            self.stop_tunnel()
        self.tunnel = create_provider(self)
        url = self.tunnel.start()
        self.public_url = url
        return url

    def stop_tunnel(self) -> None:
        if self.tunnel is not None:
            try:
                self.tunnel.stop()
            finally:
                self.tunnel = None
                self.public_url = ""
                self.tcp_endpoints = {}
