"""EventBus thread-safe.

Qualquer thread chama ``publish``.  Os assinantes podem ser:

* callback sincrono (executa na thread de quem publicou: deve ser rapido);
* ``queue.Queue`` (para threads comuns);
* ``asyncio.Queue`` vinculada a um loop (entrega via ``call_soon_threadsafe``),
  usada pelo alertStream, eventManager attach e WebSocket.

A GUI converte eventos em sinais Qt (``QtEventBridge``, etapa 5).
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import queue
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

log = logging.getLogger(__name__)


class EventType:
    """Nomes de eventos (strings, para serem serializaveis)."""

    MOTION_START = "motion.start"
    MOTION_STOP = "motion.stop"
    VIDEO_LOSS = "video.loss"
    VIDEO_RESTORED = "video.restored"
    CAMERA_REBOOTING = "camera.rebooting"
    CAMERA_ONLINE = "camera.online"
    SYSTEM_REBOOTING = "system.rebooting"
    SYSTEM_ONLINE = "system.online"
    RECORDING_START = "recording.start"
    RECORDING_STOP = "recording.stop"
    LOGIN_OK = "login.ok"
    LOGIN_FAIL = "login.fail"
    LOGIN_LOCKED = "login.locked"
    CONFIG_CHANGED = "config.changed"
    STORAGE_STATUS = "storage.status"
    STREAM_STATS = "stream.stats"
    TUNNEL_STATUS = "tunnel.status"
    TUNNEL_LOG = "tunnel.log"
    LOG = "log"


def utcnow() -> datetime:
    """Agora em UTC, com tzinfo."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class Event:
    """Um evento do sistema. ``channel`` 0 = evento do equipamento inteiro."""

    type: str
    device_id: str = ""
    channel: int = 0
    data: dict[str, Any] = field(default_factory=dict)
    ts: datetime = field(default_factory=utcnow)
    seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Versao JSON-friendly (para WebSocket e API)."""
        return {
            "seq": self.seq,
            "type": self.type,
            "device_id": self.device_id,
            "channel": self.channel,
            "data": self.data,
            "ts": self.ts.isoformat(),
        }


def _match(patterns: Iterable[str] | None, etype: str) -> bool:
    """``None`` casa tudo; ``"motion.*"`` casa por prefixo."""
    if patterns is None:
        return True
    for p in patterns:
        if p == etype or (p.endswith(".*") and etype.startswith(p[:-1])) or p == "*":
            return True
    return False


class EventBus:
    """Barramento de eventos publish/subscribe thread-safe."""

    def __init__(self, history: int = 500) -> None:
        self._lock = threading.RLock()
        self._subs: dict[int, tuple[Iterable[str] | None, Callable[[Event], None]]] = {}
        self._ids = itertools.count(1)
        self._seq = itertools.count(1)
        self._history: deque[Event] = deque(maxlen=history)

    # ------------------------------------------------------------------ publicar
    def publish(self, type: str, device_id: str = "", channel: int = 0, **data: Any) -> Event:
        """Cria e publica um evento."""
        ev = Event(type=type, device_id=device_id, channel=channel, data=data, seq=next(self._seq))
        self.publish_event(ev)
        return ev

    def publish_event(self, ev: Event) -> None:
        with self._lock:
            self._history.append(ev)
            subs = list(self._subs.items())
        for sid, (types, cb) in subs:
            if not _match(types, ev.type):
                continue
            try:
                cb(ev)
            except _Unsubscribe:
                self._unsubscribe(sid)
            except Exception:  # noqa: BLE001 - um assinante ruim nao derruba os outros
                log.exception("assinante do EventBus falhou em %s", ev.type)

    # ------------------------------------------------------------------ assinar
    def subscribe(self, callback: Callable[[Event], None], types: Iterable[str] | None = None) -> Callable[[], None]:
        """Registra um callback sincrono. Retorna a funcao que cancela a assinatura."""
        sid = next(self._ids)
        with self._lock:
            self._subs[sid] = (tuple(types) if types is not None else None, callback)
        return lambda: self._unsubscribe(sid)

    def subscribe_queue(
        self, types: Iterable[str] | None = None, maxsize: int = 1000
    ) -> tuple["queue.Queue[Event]", Callable[[], None]]:
        """Assina com uma ``queue.Queue``. Se encher, descarta o evento mais antigo."""
        q: "queue.Queue[Event]" = queue.Queue(maxsize=maxsize)

        def put(ev: Event) -> None:
            try:
                q.put_nowait(ev)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    pass

        return q, self.subscribe(put, types)

    def subscribe_async(
        self, loop: asyncio.AbstractEventLoop, types: Iterable[str] | None = None, maxsize: int = 1000
    ) -> tuple["asyncio.Queue[Event]", Callable[[], None]]:
        """Assina com uma ``asyncio.Queue`` do ``loop`` informado."""
        q: "asyncio.Queue[Event]" = asyncio.Queue(maxsize=maxsize)

        def put_in_loop(ev: Event) -> None:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass

        def deliver(ev: Event) -> None:
            try:
                loop.call_soon_threadsafe(put_in_loop, ev)
            except RuntimeError:  # loop fechado
                raise _Unsubscribe from None

        return q, self.subscribe(deliver, types)

    def _unsubscribe(self, sid: int) -> None:
        with self._lock:
            self._subs.pop(sid, None)

    # ------------------------------------------------------------------ consulta
    def history(self, limit: int = 100, types: Iterable[str] | None = None) -> list[Event]:
        """Ultimos eventos (mais novos por ultimo)."""
        with self._lock:
            items = [e for e in self._history if _match(types, e.type)]
        return items[-limit:]

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subs)


class _Unsubscribe(Exception):
    """Sinaliza que o assinante deve ser removido (ex.: loop fechado)."""
