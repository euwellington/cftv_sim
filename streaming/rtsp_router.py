"""RtspRouter: proxy RTSP leve, uma porta por equipamento, na frente do MediaMTX.

Por que existe (ver docs/ARQUITETURA.md, secao 4): o MediaMTX roteia so pelo *path* e a
Intelbras usa query string (``/cam/realmonitor?channel=1&subtype=0``); alem disso,
reescrever a URL invalida o hash do Digest.  Entao o proxy:

1. termina a autenticacao (Digest e, se habilitado, Basic) com os usuarios do equipamento;
2. reescreve a URL de CADA requisicao (OPTIONS/DESCRIBE/SETUP/PLAY/TEARDOWN...) para o
   path interno e traduz de volta ``Content-Base``/``Content-Location``/``RTP-Info`` nas respostas;
3. fala com o MediaMTX em loopback, sem credenciais;
4. so oferece transporte **RTP/RTSP sobre TCP** (interleaved): UDP exigiria relay de portas.
   Clientes que pedem so UDP recebem ``461`` e (VLC, ffmpeg, iVMS) repetem com TCP;
5. depois do PLAY apenas repassa os bytes (quadros ``$`` intercalados).

Detalhes de clientes especificos (Require/Session) sao tolerados: cabecalhos desconhecidos
passam inalterados.
"""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional
from urllib.parse import urlsplit

from api.digest_auth import DigestAuthenticator
from core.event_bus import EventBus, EventType
from core.models import DeviceSpec
from core.security import LoginLimiter
from streaming import paths

log = logging.getLogger(__name__)
MAX_HEADER = 64 * 1024
CRLF = b"\r\n"


@dataclass
class RtspMessage:
    """Requisicao, resposta ou quadro intercalado (``$``)."""

    kind: str  # req | resp | frame
    start_line: str = ""
    headers: list[tuple[str, str]] = field(default_factory=list)
    body: bytes = b""
    raw: bytes = b""  # so para quadros

    def get(self, name: str) -> Optional[str]:
        n = name.lower()
        for k, v in self.headers:
            if k.lower() == n:
                return v
        return None

    def set(self, name: str, value: str) -> None:
        n = name.lower()
        self.headers = [(k, v) for k, v in self.headers if k.lower() != n]
        self.headers.append((name, value))

    def remove(self, name: str) -> None:
        n = name.lower()
        self.headers = [(k, v) for k, v in self.headers if k.lower() != n]

    def serialize(self) -> bytes:
        if self.kind == "frame":
            return self.raw
        if self.body or self.get("Content-Length") is not None:
            self.set("Content-Length", str(len(self.body)))
        head = self.start_line + "\r\n" + "".join(f"{k}: {v}\r\n" for k, v in self.headers) + "\r\n"
        return head.encode("utf-8") + self.body


async def read_message(reader: asyncio.StreamReader) -> Optional[RtspMessage]:
    """Le uma mensagem RTSP (ou quadro ``$``). ``None`` no fim da conexao."""
    try:
        first = await reader.readexactly(1)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    if first == b"$":
        hdr = await reader.readexactly(3)
        length = int.from_bytes(hdr[1:3], "big")
        payload = await reader.readexactly(length)
        return RtspMessage("frame", raw=b"$" + hdr + payload)
    try:
        rest = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
        return None
    text = (first + rest).decode("utf-8", "replace")
    lines = text.split("\r\n")
    start = lines[0]
    headers: list[tuple[str, str]] = []
    for ln in lines[1:]:
        if not ln:
            continue
        k, _, v = ln.partition(":")
        headers.append((k.strip(), v.strip()))
    kind = "resp" if start.startswith("RTSP/") else "req"
    msg = RtspMessage(kind, start, headers)
    n = int(msg.get("Content-Length") or 0)
    if n:
        if n > MAX_HEADER * 16:
            return None
        msg.body = await reader.readexactly(n)
    return msg


def force_tcp_transport(value: str) -> Optional[str]:
    """Mantem so candidatos TCP do ``Transport``; ``None`` se o cliente so oferece UDP."""
    keep = [t.strip() for t in value.split(",") if "TCP" in t.upper() or "interleaved" in t.lower()]
    return keep[0] if keep else None


@dataclass
class Upstream:
    host: str = "127.0.0.1"
    port: int = 18554


PlaybackStarter = Callable[[DeviceSpec, "paths.Resolved"], Awaitable[str]]


class RtspRouter:
    """Servidor RTSP de um equipamento. ``is_available`` False => recusa a conexao (reboot)."""

    def __init__(self, spec: DeviceSpec, auth: DigestAuthenticator, *, upstream: Upstream | None = None,
                 is_available: Callable[[], bool] = lambda: True, limiter: LoginLimiter | None = None,
                 bus: EventBus | None = None, playback: PlaybackStarter | None = None,
                 stream_ready: Callable[[str], bool] | None = None,
                 require_auth: Callable[[], bool] = lambda: True) -> None:
        self.require_auth = require_auth
        self.spec = spec
        self.auth = auth
        self.upstream = upstream or Upstream()
        self.is_available = is_available
        self.limiter = limiter or LoginLimiter()
        self.bus = bus
        self.playback = playback
        self.stream_ready = stream_ready
        self._server: Optional[asyncio.AbstractServer] = None
        self.connections = 0

    async def start(self, host: str | None = None) -> None:
        self._server = await asyncio.start_server(self._handle, host or self.spec.bind_ip, self.spec.rtsp_port,
                                                  limit=MAX_HEADER)
        log.info("RtspRouter %s escutando em %s:%s", self.spec.id, host or self.spec.bind_ip, self.spec.rtsp_port)

    async def stop(self) -> None:
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ------------------------------------------------------------------ conexao
    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = (writer.get_extra_info("peername") or ("?", 0))[0]
        if not self.is_available():  # equipamento reiniciando: recusa como um aparelho fora do ar
            writer.close()
            return
        self.connections += 1
        conn = _Conn(self, reader, writer, peer)
        try:
            await conn.run()
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        except Exception:  # noqa: BLE001
            log.exception("erro na conexao RTSP de %s", peer)
        finally:
            self.connections -= 1
            await conn.close()


class _Conn:
    def __init__(self, router: RtspRouter, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, peer: str) -> None:
        self.r = router
        self.reader, self.writer, self.peer = reader, writer, peer
        self.up_reader: Optional[asyncio.StreamReader] = None
        self.up_writer: Optional[asyncio.StreamWriter] = None
        self.user = ""
        self.maps: dict[str, str] = {}  # base interna -> base externa (para reescrever respostas)
        self._down_task: Optional[asyncio.Task] = None
        self._playback_internal = ""

    # ------------------------------------------------------------------ util
    async def send_client(self, data: bytes) -> None:
        self.writer.write(data)
        await self.writer.drain()

    async def reply(self, code: int, text: str, cseq: str, extra: list[tuple[str, str]] | None = None) -> None:
        m = RtspMessage("resp", f"RTSP/1.0 {code} {text}", [("CSeq", cseq)] + (extra or []))
        m.set("Server", "Device/1.0")
        log.info("RTSP %s <- %s %s (local)", self.peer, code, text)
        await self.send_client(m.serialize())

    async def close(self) -> None:
        if self._down_task:
            self._down_task.cancel()
        for w in (self.up_writer, self.writer):
            if w:
                try:
                    w.close()
                except Exception:  # noqa: BLE001
                    pass

    async def _connect_upstream(self) -> None:
        up = self.r.upstream
        self.up_reader, self.up_writer = await asyncio.open_connection(up.host, up.port, limit=MAX_HEADER)
        self._down_task = asyncio.create_task(self._pipe_down())

    # ------------------------------------------------------------------ cliente -> upstream
    async def run(self) -> None:
        while True:
            msg = await read_message(self.reader)
            if msg is None:
                return
            if msg.kind == "frame":  # RTCP do cliente
                if self.up_writer:
                    self.up_writer.write(msg.raw)
                    await self.up_writer.drain()
                continue
            if msg.kind != "req":
                continue
            if not self.r.is_available():
                return  # reboot durante a sessao: derruba
            if not await self._process(msg):
                return

    async def _process(self, msg: RtspMessage) -> bool:
        """Trata uma requisicao. Retorna False para encerrar a conexao."""
        method, uri, _ver = (msg.start_line.split(" ", 2) + ["", ""])[:3]
        cseq = msg.get("CSeq") or "0"
        log.info("RTSP %s -> %s %s ua=%s auth=%s", self.peer, method, uri, msg.get("User-Agent"),
                 "sim" if msg.get("Authorization") else "nao")

        if method == "OPTIONS" and uri != "*" and not self.user and self.r.require_auth():
            # OPTIONS sem credenciais: responde localmente (como os aparelhos reais)
            await self.reply(200, "OK", cseq, [("Public", "OPTIONS, DESCRIBE, SETUP, PLAY, PAUSE, TEARDOWN, GET_PARAMETER")])
            return True

        sp = urlsplit(uri)
        target = sp.path + (("?" + sp.query) if sp.query else "")
        auth_hdr = msg.get("Authorization")

        # ---- autenticacao
        if not self.r.require_auth():
            self.user = self.user or "anonymous"
        elif not self.user or auth_hdr:
            key = f"ip:{self.peer}"
            ok_lock, wait = self.r.limiter.check(key)
            if not ok_lock:
                await self.reply(403, "Forbidden", cseq)  # bloqueio por tentativas erradas
                return False
            res = self.r.auth.verify(auth_hdr, method, target, check_uri=False)
            if not res.ok:
                if res.credentials_sent and not res.stale:
                    locked = self.r.limiter.fail(key)
                    self._publish(EventType.LOGIN_LOCKED if locked else EventType.LOGIN_FAIL, user=res.username)
                    if locked:
                        await self.reply(403, "Forbidden", cseq)
                        return False
                await self.reply(401, "Unauthorized", cseq,
                                 [("WWW-Authenticate", c) for c in self.r.auth.challenge(stale=res.stale)])
                return True
            if not self.user:
                self._publish(EventType.LOGIN_OK, user=res.username)
            self.user = res.username
            self.r.limiter.success(key)

        # ---- resolucao da URL
        res_url = paths.resolve(self.r.spec, sp.path, sp.query)
        if res_url is None:
            await self.reply(404, "Not Found", cseq)
            return True
        if res_url.kind == "playback":
            if self.r.playback is None:
                await self.reply(404, "Not Found", cseq)
                return True
            if not self._playback_internal:
                try:
                    self._playback_internal = await self.r.playback(self.r.spec, res_url)
                except Exception as exc:  # noqa: BLE001
                    log.warning("playback indisponivel: %s", exc)
                    await self.reply(404, "Not Found", cseq)
                    return True
            internal = self._playback_internal
        else:
            internal = paths.internal_path(self.r.spec.id, res_url.channel, res_url.stream)

        up = self.r.upstream
        base_int = f"rtsp://{up.host}:{up.port}/{internal}"
        ext_base = uri[: len(uri) - len(res_url.suffix)] if res_url.suffix else uri
        self.maps[base_int] = ext_base.rstrip("/")
        new_uri = base_int + res_url.suffix

        # ---- transporte: so TCP interleaved
        if method == "SETUP":
            tr = msg.get("Transport")
            fixed = force_tcp_transport(tr) if tr else None
            if tr and fixed is None:
                await self.reply(461, "Unsupported Transport", cseq)
                return True
            if fixed:
                msg.set("Transport", fixed)

        msg.start_line = f"{method} {new_uri} RTSP/1.0"
        msg.remove("Authorization")

        if self.up_writer is None:
            try:
                await self._connect_upstream()
            except OSError:
                await self.reply(503, "Service Unavailable", cseq)
                return False
        assert self.up_writer is not None
        # DESCRIBE de playback: o publisher ainda esta subindo; MediaMTX responde 404 ate la.
        self.up_writer.write(msg.serialize())
        await self.up_writer.drain()
        return True

    # ------------------------------------------------------------------ upstream -> cliente
    async def _pipe_down(self) -> None:
        assert self.up_reader is not None
        try:
            while True:
                msg = await read_message(self.up_reader)
                if msg is None:
                    break
                if msg.kind == "resp":
                    log.info("RTSP %s <- %s (upstream)", self.peer, msg.start_line)
                    self._rewrite_response(msg)
                await self.send_client(msg.serialize())
        except (ConnectionError, asyncio.IncompleteReadError, asyncio.CancelledError):
            pass
        finally:
            log.info("RTSP %s upstream fechou; encerrando cliente", self.peer)
            try:
                self.writer.close()
            except Exception:  # noqa: BLE001
                pass

    def _rewrite_response(self, msg: RtspMessage) -> None:
        for name in ("Content-Base", "Content-Location", "RTP-Info"):
            v = msg.get(name)
            if v:
                for internal, ext in self.maps.items():
                    v = v.replace(internal, ext)
                msg.set(name, v)
        if msg.body and (msg.get("Content-Type") or "").lower().startswith("application/sdp"):
            body = msg.body.decode("utf-8", "replace")
            for internal, ext in self.maps.items():
                body = body.replace(internal, ext)
            msg.body = body.encode("utf-8")
        msg.set("Server", "Device/1.0")

    def _publish(self, etype: str, **data) -> None:  # noqa: ANN003
        if self.r.bus:
            self.r.bus.publish(etype, self.r.spec.id, 0, ip=self.peer, proto="rtsp", **data)
