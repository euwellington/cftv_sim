"""Servico de gravacao: continua, por movimento (ring buffer) e manual, com indexacao e retencao."""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

from core import config
from core.event_bus import Event, EventType
from core.virtual_camera import VirtualCamera

from . import retention
from .continuous import RawFfmpeg, ensure_day_dirs, segment_writer
from .event_recorder import EventClip
from .indexer import Indexer
from .ring_buffer import RingBuffer

log = logging.getLogger(__name__)
REC_FPS = 15


class ChannelRecorder(threading.Thread):
    """Uma thread por canal: le quadros, alimenta os escritores conforme modo/estado."""

    def __init__(self, svc: "RecordingService", cam: VirtualCamera) -> None:
        super().__init__(daemon=True, name=f"rec-{cam.key}")
        self.svc, self.cam = svc, cam
        self.spec = svc.ctx.manager.get(cam.device_id).spec
        self.stop_ev = threading.Event()
        self.manual = False
        self.cont: Optional[RawFfmpeg] = None
        self.manual_w: Optional[RawFfmpeg] = None
        self.ring: Optional[RingBuffer] = None
        self.clip: Optional[EventClip] = None
        self.motion_active = False
        self._size = (0, 0)
        self._dirs_t = 0.0

    # ---- estado desejado
    def _mode(self) -> str:
        if self.cam.spec.record_mode == "off":
            return "off"
        if self.cam.spec.record_mode == "manual":
            return "off"
        return self.spec.recording.mode  # continuous | motion | off

    def _channel_dir(self) -> Path:
        return self.svc.ctx.storage.channel_dir(self.spec.id, self.cam.channel)

    def _writable(self) -> bool:
        return self.svc.ctx.storage.can_record(self.spec.id) and self.cam.is_online and \
            self.svc.ctx.manager.get(self.spec.id).is_available

    def _close_all(self) -> None:
        for w in (self.cont, self.manual_w):
            if w:
                w.close()
        self.cont = self.manual_w = None
        if self.ring:
            if self.clip:
                self._finish_clip()
            self.ring.close()
            self.ring = None
        self._update_flag()

    def _update_flag(self) -> None:
        active = bool(self.cont or self.manual_w or self.clip)
        self.cam.recording = active
        if active:
            self.cam.recording_kind = "manual" if self.manual_w else ("motion" if self.clip else "continuous")
        elif not self.manual:
            self.cam.recording_kind = ""

    def _finish_clip(self) -> None:
        clip, self.clip = self.clip, None
        if clip:
            threading.Thread(target=clip.finish, daemon=True).start()

    def on_event(self, ev: Event) -> None:
        if ev.device_id != self.spec.id or ev.channel != self.cam.channel:
            return
        if ev.type == EventType.MOTION_START:
            self.motion_active = True
            if self.clip:
                self.clip.motion_again()
            elif self.ring and self._mode() == "motion" and self._writable():
                self.clip = EventClip(self.ring, self._channel_dir() / ".work" / f"{int(time.time())}",
                                      self._channel_dir(), self.spec.recording.pre_event_s,
                                      self.spec.recording.post_event_s)
                self.svc.ctx.bus.publish(EventType.RECORDING_START, self.spec.id, self.cam.channel, kind="motion")
        elif ev.type == EventType.MOTION_STOP:
            self.motion_active = False
            if self.clip:
                self.clip.motion_ended()

    # ---- laco
    def run(self) -> None:
        fps = REC_FPS
        next_t = time.monotonic()
        while not self.stop_ev.is_set():
            mode = self._mode()
            if not self._writable():
                if self.cont or self.manual_w or self.ring:
                    self._close_all()
                time.sleep(0.5)
                continue
            frame = self.cam.get_frame("main")
            if frame is None:
                time.sleep(0.1)
                continue
            h, w = frame.shape[:2]
            if (w, h) != self._size:
                self._close_all()
                self._size = (w, h)
            try:
                self._ensure(mode, w, h, fps)
            except FileNotFoundError:
                log.error("ffmpeg ausente: gravacao desativada")
                return
            for wr in (self.cont, self.manual_w, self.ring):
                if wr is not None:
                    wr.feed(frame)
            if self.ring and self.clip:
                self.clip.tick()
                if self.clip.due():
                    self._finish_clip()
                    self.svc.ctx.bus.publish(EventType.RECORDING_STOP, self.spec.id, self.cam.channel, kind="motion")
            if self.ring:
                self.ring.prune()
            if time.monotonic() - self._dirs_t > 60:
                ensure_day_dirs(self._channel_dir())
                self._dirs_t = time.monotonic()
            self._update_flag()
            next_t += 1.0 / fps
            d = next_t - time.monotonic()
            if d > 0:
                time.sleep(d)
            elif d < -1:
                next_t = time.monotonic()
        self._close_all()

    def _ensure(self, mode: str, w: int, h: int, fps: int) -> None:
        seg_s = self.spec.recording.segment_s
        cdir = self._channel_dir()
        if mode == "continuous" and (self.cont is None or not self.cont.alive):
            self.cont = segment_writer(cdir, w, h, fps, seg_s)
        elif mode != "continuous" and self.cont:
            self.cont.close()
            self.cont = None
        if mode == "motion" and self.ring is None:
            self.ring = RingBuffer(cdir / ".ring", w, h, fps)
        elif mode != "motion" and self.ring:
            self._close_all_ring()
        if self.manual and (self.manual_w is None or not self.manual_w.alive):
            self.manual_w = segment_writer(cdir, w, h, fps, seg_s, suffix="_manual")
        elif not self.manual and self.manual_w:
            self.manual_w.close()
            self.manual_w = None

    def _close_all_ring(self) -> None:
        if self.clip:
            self._finish_clip()
        if self.ring:
            self.ring.close()
            self.ring = None


class RecordingService:
    name = "recording"

    def __init__(self, ctx) -> None:  # noqa: ANN001
        self.ctx = ctx
        self.recorders: dict[tuple[str, int], ChannelRecorder] = {}
        self.indexer = Indexer(ctx.db, ctx.storage.channel_dir, ctx.profile.devices)
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._unsub = None

    def start(self) -> None:
        if not config.bin_path("ffmpeg"):
            raise RuntimeError("ffmpeg nao encontrado: gravacao indisponivel (rode tools/fetch_binaries.py)")
        for dev in self.ctx.manager.list():
            for ch, cam in dev.cameras.items():
                rec = ChannelRecorder(self, cam)
                self.recorders[(dev.id, ch)] = rec
                rec.start()
        self._unsub = self.ctx.bus.subscribe(self._on_event, [EventType.MOTION_START, EventType.MOTION_STOP])
        self._stop.clear()
        self._thread = threading.Thread(target=self._maint, daemon=True, name="rec-maint")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._unsub:
            self._unsub()
        for r in self.recorders.values():
            r.stop_ev.set()
        for r in self.recorders.values():
            r.join(timeout=8)
        try:
            self.indexer.scan()
        except Exception:  # noqa: BLE001
            pass

    def _on_event(self, ev: Event) -> None:
        r = self.recorders.get((ev.device_id, ev.channel))
        if r:
            r.on_event(ev)

    def set_manual(self, device_id: str, channel: int, on: bool) -> None:
        r = self.recorders.get((device_id, channel))
        if r:
            r.manual = on

    def _maint(self) -> None:
        n = 0
        while not self._stop.wait(2.0):
            try:
                self.indexer.scan()
                n += 1
                if n % 10 == 0:
                    retention.apply(self.ctx.storage)
            except Exception:  # noqa: BLE001
                log.exception("manutencao da gravacao")


def create_service(ctx) -> RecordingService:  # noqa: ANN001
    return RecordingService(ctx)
