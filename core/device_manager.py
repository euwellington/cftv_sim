"""DeviceManager: monta equipamentos a partir do perfil, controla reboot e persiste eventos.

* ``build_profile`` e a fabrica usada pelo assistente (wizard) da GUI.
* ``DeviceRuntime`` agrupa as cameras virtuais de um equipamento e simula o
  reboot do sistema (API responde 503 / recusa conexao enquanto reinicia).
* O DeviceManager escuta o EventBus e grava eventos/logs no SQLite em uma thread
  propria, para nunca bloquear as threads de video.
"""
from __future__ import annotations

import json
import logging
import queue
import random
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

import cv2

from . import config
from .capture import CaptureService
from .db import Database
from .event_bus import Event, EventBus, EventType
from .models import (CHANNEL_VARIATIONS, Brand, ChannelSpec, DeviceKind, DeviceSpec, Profile, PublicAccess,
                     default_realm, make_device_id, make_mac, make_serial, pick_model)
from .ports import PortSet, suggest_port_plan
from .security import UserStore, password_strength
from .virtual_camera import CameraState, VirtualCamera

log = logging.getLogger(__name__)


class ProfileError(ValueError):
    """Erro de validacao ao montar um perfil (ex.: senha fraca)."""


# ============================================================================= fabrica de perfil
def build_profile(*, name: str = "padrao", brand_choice: str = "hikvision", mode: str = "camera",
                  camera_count: int = 1, hik_recorder: str = "NVR", intelbras_recorder: str = "NVD",
                  bind_ip: str = "0.0.0.0", admin_user: str = "admin", admin_password: str,
                  ports: Optional[list[PortSet]] = None, public: Optional[PublicAccess] = None,
                  legacy_firmware: bool = False, allow_basic: bool = False, onvif_enabled: bool = True,
                  discovery_enabled: bool = False, web_port: Optional[int] = None,
                  settings: Optional[dict[str, Any]] = None) -> Profile:
    """Cria o perfil completo (equipamentos, canais, usuarios) a partir das respostas do wizard.

    * ``brand_choice``: ``hikvision`` | ``intelbras`` | ``all``;
    * ``mode``: ``camera`` (IP unica) | ``recorder`` (NVR/DVR com 1 a 5 cameras);
    * ``hik_recorder``: ``NVR`` | ``DVR``; ``intelbras_recorder``: ``NVD`` | ``MHDX`` | ``iMHDX``.

    Levanta ``ProfileError`` se a senha violar a politica (senha padrao bloqueada).
    """
    rep = password_strength(admin_password, admin_user)
    if not rep.ok:
        raise ProfileError("Senha recusada: " + "; ".join(rep.issues))
    if brand_choice not in ("hikvision", "intelbras", "all"):
        raise ProfileError(f"fabricante invalido: {brand_choice}")
    if mode not in ("camera", "recorder"):
        raise ProfileError(f"modo invalido: {mode}")
    n_ch = 1 if mode == "camera" else max(1, min(5, int(camera_count)))

    settings = settings or {}
    cfg = config.load_settings(settings)
    brands = [Brand.HIKVISION, Brand.INTELBRAS] if brand_choice == "all" else [Brand(brand_choice)]
    plan = ports or suggest_port_plan(
        [b.value for b in brands], host=bind_ip, prefer_standard=cfg["ports"]["prefer_standard"],
        hik_http_start=cfg["ports"]["hikvision_http_start"],
        intelbras_http_start=cfg["ports"]["intelbras_http_start"], rtsp_start=cfg["ports"]["rtsp_start"],
        reserved=[web_port or cfg["ports"]["web_portal"]])
    if len(plan) < len(brands):
        raise ProfileError("plano de portas insuficiente para os equipamentos")

    devices: list[DeviceSpec] = []
    for i, brand in enumerate(brands):
        if mode == "camera":
            kind, family = DeviceKind.IPCAM, ""
        elif brand is Brand.HIKVISION:
            kind, family = (DeviceKind.DVR if hik_recorder.upper() == "DVR" else DeviceKind.NVR), ""
        else:
            kind, family = DeviceKind.NVR, intelbras_recorder
        fam, model, _cap, fw, build = pick_model(brand, kind, n_ch, family)
        dev_id = make_device_id(brand, kind, 1)
        seed = f"{name}-{dev_id}"
        serial = make_serial(brand, model, seed, 1)
        mac = make_mac(brand, seed)

        channels: list[ChannelSpec] = []
        for c in range(1, n_ch + 1):
            label = f"Camera {c:02d}" if brand is Brand.HIKVISION else f"CAM {c}"
            channels.append(ChannelSpec(index=c, name=label, **CHANNEL_VARIATIONS.get(c, {})))

        spec = DeviceSpec(
            id=dev_id, brand=brand, kind=kind, family=fam, model=model,
            name=(("Network Video Recorder" if kind is DeviceKind.NVR else "Digital Video Recorder")
                  if kind is not DeviceKind.IPCAM and brand is Brand.HIKVISION else model),
            serial=serial, mac=mac, firmware=fw, firmware_build=build, bind_ip=bind_ip,
            http_port=plan[i].http, rtsp_port=plan[i].rtsp, sdk_port=plan[i].sdk, onvif_port=plan[i].onvif,
            onvif_enabled=onvif_enabled, discovery_enabled=discovery_enabled,
            legacy_firmware=legacy_firmware and brand is Brand.HIKVISION, allow_basic=allow_basic,
            channels=channels,
        )
        store = UserStore(spec.users, realms=[default_realm(spec)])
        store.set_password(admin_user, admin_password, role="admin")
        devices.append(spec)

    return Profile(name=name, brand_choice=brand_choice, mode=mode, devices=devices,  # type: ignore[arg-type]
                   public=public or PublicAccess(), web_port=web_port or cfg["ports"]["web_portal"],
                   settings=settings)


# ============================================================================= perfis em disco
def profile_path(name: str) -> Path:
    return config.PROFILES_DIR / f"{Profile(name=name).name}.json"


def save_profile(profile: Profile) -> Path:
    config.PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    path = profile_path(profile.name)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(profile.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)  # gravacao atomica
    return path


def load_profile(name_or_path: str | Path) -> Profile:
    p = Path(name_or_path)
    if not p.suffix:
        p = profile_path(str(name_or_path))
    return Profile.model_validate_json(p.read_text(encoding="utf-8"))


def list_profiles() -> list[str]:
    if not config.PROFILES_DIR.exists():
        return []
    return sorted(p.stem for p in config.PROFILES_DIR.glob("*.json"))


# ============================================================================= equipamento em execucao
class DeviceRuntime:
    """Equipamento em execucao: cameras virtuais + estado do sistema."""

    def __init__(self, spec: DeviceSpec, cameras: dict[int, VirtualCamera], bus: EventBus,
                 reboot_range: tuple[float, float]) -> None:
        self.spec = spec
        self.cameras = cameras
        self.bus = bus
        self.reboot_range = reboot_range
        self.state = "stopped"  # stopped | online | rebooting
        self.boot_time = time.monotonic()
        self.brand_api: Any = None  # preenchido pelas camadas brands/ (etapas 3 e 4)
        self.users = UserStore(spec.users, realms=[default_realm(spec)])
        self._reboot_until = 0.0
        self._timer: Optional[threading.Timer] = None
        self._rng = random.Random()

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def is_available(self) -> bool:
        """False durante o reboot: a API deve responder 503 / recusar conexao."""
        return self.state == "online"

    def uptime(self) -> float:
        return max(0.0, time.monotonic() - self.boot_time) if self.state != "stopped" else 0.0

    def reboot_remaining(self) -> float:
        return max(0.0, self._reboot_until - time.monotonic()) if self.state == "rebooting" else 0.0

    def start(self) -> None:
        self.boot_time = time.monotonic()
        for cam in self.cameras.values():
            cam.start()
        self.state = "online"

    def stop(self) -> None:
        if self._timer:
            self._timer.cancel()
        for cam in self.cameras.values():
            cam.stop()
        self.state = "stopped"

    def reboot_system(self, seconds: Optional[float] = None) -> float:
        """Reinicia todos os canais e o proprio equipamento. Retorna a duracao."""
        if self.state != "online":
            return self.reboot_remaining()
        dur = seconds if seconds is not None else self._rng.uniform(*self.reboot_range)
        self._reboot_until = time.monotonic() + dur
        self.state = "rebooting"
        self.bus.publish(EventType.SYSTEM_REBOOTING, self.id, 0, seconds=round(dur, 1))
        for cam in self.cameras.values():
            cam.reboot(dur)
        self._timer = threading.Timer(dur, self._finish_reboot)
        self._timer.daemon = True
        self._timer.start()
        return dur

    def reboot_camera(self, channel: int, seconds: Optional[float] = None) -> float:
        """Reinicia um canal. Em camera IP unica, equivale a reiniciar o sistema."""
        if not self.spec.is_recorder:
            return self.reboot_system(seconds)
        return self.cameras[channel].reboot(seconds)

    def _finish_reboot(self) -> None:
        # aguarda as cameras voltarem (elas controlam o proprio prazo)
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline and any(c.state == CameraState.REBOOTING for c in self.cameras.values()):
            time.sleep(0.1)
        self.boot_time = time.monotonic()
        self.state = "online"
        self.bus.publish(EventType.SYSTEM_ONLINE, self.id, 0)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id, "brand": self.spec.brand.value, "kind": self.spec.kind.value, "model": self.spec.model,
            "name": self.spec.name, "state": self.state, "uptime_s": round(self.uptime()),
            "http_port": self.spec.http_port, "rtsp_port": self.spec.rtsp_port,
            "channels": [{"channel": c.channel, "name": c.name, "state": c.state.value, "fps": c.stats["fps"],
                          "recording": c.recording} for c in self.cameras.values()],
        }


# ============================================================================= gerenciador
_EVENT_LABELS = {
    EventType.MOTION_START: ("VMD", "start", "Motion alarm", "alarm"),
    EventType.MOTION_STOP: ("VMD", "stop", "Motion alarm ended", "alarm"),
    EventType.VIDEO_LOSS: ("videoloss", "start", "Video loss", "alarm"),
    EventType.VIDEO_RESTORED: ("videoloss", "stop", "Video restored", "alarm"),
    EventType.CAMERA_REBOOTING: ("reboot", "start", "Camera rebooting", "operation"),
    EventType.CAMERA_ONLINE: ("reboot", "stop", "Camera online", "system"),
    EventType.SYSTEM_REBOOTING: ("reboot", "start", "System rebooting", "operation"),
    EventType.SYSTEM_ONLINE: ("reboot", "stop", "System online", "system"),
    EventType.RECORDING_START: ("recording", "start", "Recording started", "operation"),
    EventType.RECORDING_STOP: ("recording", "stop", "Recording stopped", "operation"),
    EventType.LOGIN_OK: ("login", "ok", "Login successful", "operation"),
    EventType.LOGIN_FAIL: ("login", "fail", "Login failed", "exception"),
    EventType.LOGIN_LOCKED: ("login", "locked", "Login locked after failures", "exception"),
}


class DeviceManager:
    """Cria, inicia e controla os equipamentos; persiste eventos e logs."""

    def __init__(self, bus: EventBus, db: Database, settings: Optional[dict[str, Any]] = None) -> None:
        self.bus = bus
        self.db = db
        self.settings = settings or config.load_settings()
        cap = self.settings["capture"]
        self.capture = CaptureService(cap["camera_index"], cap["width"], cap["height"], cap["fps"],
                                      cap["force_test_pattern"], cap["reconnect_interval_s"])
        self.profile: Optional[Profile] = None
        self.devices: dict[str, DeviceRuntime] = {}
        self._writer_q: "queue.Queue[Optional[Event]]" = queue.Queue(maxsize=5000)
        self._writer: Optional[threading.Thread] = None
        self._unsub: Optional[Any] = None

    # ------------------------------------------------------------------ carga
    def load(self, profile: Profile) -> None:
        """Monta os equipamentos do perfil (sem iniciar)."""
        self.stop()
        self.profile = profile
        self.settings = config.load_settings(profile.settings)
        sim = self.settings["simulation"]
        rng = (float(sim["reboot_seconds"][0]), float(sim["reboot_seconds"][1]))
        self.devices.clear()
        for spec in profile.devices:
            cams = {
                ch.index: VirtualCamera(spec.id, spec.brand, spec.model, ch, self.capture, self.bus,
                                        reboot_range=rng, motion_hold_s=float(sim["motion_hold_s"]),
                                        motion_warmup_frames=int(sim["motion_warmup_frames"]),
                                        video_loss_after_s=float(sim["video_loss_after_s"]))
                for ch in spec.channels
            }
            self.devices[spec.id] = DeviceRuntime(spec, cams, self.bus, rng)

    def start(self) -> None:
        """Inicia captura, cameras e a thread que persiste eventos."""
        self.capture.start()
        self._start_writer()
        for dev in self.devices.values():
            dev.start()
            self.db.add_log("system", f"{dev.spec.model} iniciado", dev.id)

    def stop(self) -> None:
        for dev in self.devices.values():
            dev.stop()
        self.capture.stop()
        self._stop_writer()

    # ------------------------------------------------------------------ consulta
    def get(self, device_id: str) -> DeviceRuntime:
        return self.devices[device_id]

    def camera(self, device_id: str, channel: int) -> VirtualCamera:
        return self.devices[device_id].cameras[channel]

    def list(self) -> list[DeviceRuntime]:
        return list(self.devices.values())

    def summary(self) -> list[dict[str, Any]]:
        return [d.summary() for d in self.devices.values()]

    def rtsp_urls(self, device_id: str, host: str = "127.0.0.1", user: str = "admin",
                  password: str = "<senha>") -> list[str]:
        """URLs RTSP de ao vivo (principal e extra) para copiar na GUI."""
        d = self.devices[device_id].spec
        urls: list[str] = []
        for ch in d.channels:
            for stream in (0, 1):
                if d.brand is Brand.HIKVISION:
                    path = f"Streaming/Channels/{ch.index * 100 + stream + 1}"
                else:
                    path = f"cam/realmonitor?channel={ch.index}&subtype={stream}"
                urls.append(f"rtsp://{user}:{password}@{host}:{d.rtsp_port}/{path}")
        return urls

    # ------------------------------------------------------------------ persistencia de eventos
    def _start_writer(self) -> None:
        if self._writer and self._writer.is_alive():
            return
        self._unsub = self.bus.subscribe(self._enqueue, list(_EVENT_LABELS))
        self._writer = threading.Thread(target=self._writer_loop, name="DeviceManager-writer", daemon=True)
        self._writer.start()

    def _stop_writer(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None
        if self._writer and self._writer.is_alive():
            self._writer_q.put(None)
            self._writer.join(timeout=3.0)
        self._writer = None

    def _enqueue(self, ev: Event) -> None:
        try:
            self._writer_q.put_nowait(ev)
        except queue.Full:
            log.warning("fila de eventos cheia; descartando %s", ev.type)

    def _thumb(self, ev: Event) -> str:
        """Salva miniatura JPEG do canal no instante do movimento."""
        try:
            cam = self.camera(ev.device_id, ev.channel)
            frame = cam.get_frame("sub")
            if frame is None:
                return ""
            thumbs = config.DATA_DIR / "thumbs"
            thumbs.mkdir(parents=True, exist_ok=True)
            path = thumbs / f"{ev.device_id}_ch{ev.channel}_{ev.ts:%Y%m%dT%H%M%S}.jpg"
            small = cv2.resize(frame, (320, int(320 * frame.shape[0] / frame.shape[1])))
            cv2.imwrite(str(path), small, [cv2.IMWRITE_JPEG_QUALITY, 70])
            return str(path)
        except Exception:  # noqa: BLE001 - miniatura e opcional
            return ""

    def _writer_loop(self) -> None:
        while True:
            ev = self._writer_q.get()
            if ev is None:
                return
            try:
                etype, state, desc, cat = _EVENT_LABELS[ev.type]
                thumb = self._thumb(ev) if ev.type == EventType.MOTION_START else ""
                self.db.add_event(ev.ts, ev.device_id, ev.channel, etype, state, desc, thumb)
                where = f"CH{ev.channel}" if ev.channel else "system"
                user = str(ev.data.get("user", ""))
                self.db.add_log(cat, f"{desc} [{where}]", ev.device_id, user, str(ev.data.get("ip", "")), ev.ts)
            except Exception:  # noqa: BLE001
                log.exception("falha ao gravar evento %s", ev.type)
