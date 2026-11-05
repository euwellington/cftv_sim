"""Servico de streaming: MediaMTX + publishers FFmpeg + RtspRouter por equipamento."""
from __future__ import annotations

import asyncio
import logging
import threading
from typing import Optional

from api.digest_auth import DigestAuthenticator
from core.security import LoginLimiter

from . import paths
from .ffmpeg_publisher import FFmpegPublisher
from .mediamtx_manager import MediaMtxManager
from .playback import PlaybackService
from .rtsp_router import RtspRouter, Upstream

log = logging.getLogger(__name__)


class StreamingService:
    name = "streaming"

    def __init__(self, ctx) -> None:  # noqa: ANN001
        self.ctx = ctx
        ports = ctx.settings.get("ports", {})
        self.mtx = MediaMtxManager(ports)
        self.publishers: list[FFmpegPublisher] = []
        self.routers: list[RtspRouter] = []
        self.playback = PlaybackService(ctx.db, self.mtx.rtsp, self.mtx.api)
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self.ready = threading.Event()

    def start(self) -> None:
        if not self.mtx.start():
            raise RuntimeError(self.mtx.error)
        for dev in self.ctx.manager.list():
            for ch, cam in dev.cameras.items():
                for s, stream in ((0, "main"), (1, "sub")):
                    out = f"rtsp://127.0.0.1:{self.mtx.rtsp}/{paths.internal_path(dev.id, ch, s)}"
                    pub = FFmpegPublisher(cam, stream, out, is_available=lambda d=dev: d.is_available)
                    self.publishers.append(pub)
                    pub.start()
        self._thread = threading.Thread(target=self._serve, daemon=True, name="rtsp-routers")
        self._thread.start()
        self.ready.wait(10)

    def _serve(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        sec = self.ctx.settings.get("security", {})
        limiter = LoginLimiter(sec.get("lockout_attempts", 5), sec.get("lockout_window_s", 900),
                               sec.get("lockout_duration_s", 1800))
        for dev in self.ctx.manager.list():
            spec = dev.spec
            auth = DigestAuthenticator(spec.realm, dev.users.digest_ha1, allow_basic=spec.allow_basic,
                                       basic_verify=lambda u, p, d=dev: d.users.verify(u, p) is not None)
            router = RtspRouter(
                spec, auth, upstream=Upstream("127.0.0.1", self.mtx.rtsp), is_available=lambda d=dev: d.is_available,
                limiter=limiter, bus=self.ctx.bus, playback=self.playback.start,
                require_auth=lambda: self.ctx.profile.rtsp_auth)
            self.routers.append(router)
            try:
                self.loop.run_until_complete(router.start())
            except OSError as exc:
                log.error("RTSP %s porta %s: %s", spec.id, spec.rtsp_port, exc)
        self.ready.set()
        self.loop.run_forever()

    def stop(self) -> None:
        for p in self.publishers:
            p.stop()
        self.playback.stop_all()
        if self.loop:
            for r in self.routers:
                asyncio.run_coroutine_threadsafe(r.stop(), self.loop).result(3)
            self.loop.call_soon_threadsafe(self.loop.stop)
        self.mtx.stop()


def create_service(ctx) -> StreamingService:  # noqa: ANN001
    return StreamingService(ctx)
