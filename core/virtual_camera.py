"""VirtualCamera: deriva uma camera da webcam (frame base compartilhado).

Pipeline por quadro::

    frame base -> espelho -> zoom/PTZ digital (recorte) -> resize p/ main
               -> matiz -> ruido -> [deteccao de movimento] -> OSD -> frame main
                                                                   (frame sub: resize sob demanda)

* O reboot e simulado aqui dentro: estado ``REBOOTING`` por 20-40 s, depois
  ``ONLINE`` com uptime zerado.  Publishers e APIs consultam ``state``.
* Frames publicados sao imutaveis: nao modifique o array retornado.
* Eventos (movimento, reboot, perda de video) vao para o ``EventBus``.
"""
from __future__ import annotations

import logging
import random
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Optional

import cv2
import numpy as np

from .capture import CaptureService
from .event_bus import EventBus, EventType
from .models import Brand, ChannelSpec
from .motion import MotionDetector, MotionEventTracker, MotionResult
from .osd import OsdStyle, render_osd, render_rebooting


class CameraState(str, Enum):
    STOPPED = "stopped"
    ONLINE = "online"
    REBOOTING = "rebooting"


@dataclass
class PtzState:
    """PTZ digital: pan/tilt em -1..1 (deslocam o recorte), zoom 1..4."""

    pan: float = 0.0
    tilt: float = 0.0
    zoom: float = 1.0
    vx: float = 0.0  # velocidades continuas (unidades/s)
    vy: float = 0.0
    vz: float = 0.0
    last_move: float = 0.0  # instante (monotonic) do ultimo movimento


def _crop_zoom(img: np.ndarray, zoom: float, pan: float, tilt: float) -> np.ndarray:
    """Recorte digital centrado, deslocado por pan/tilt. ``zoom`` >= 1."""
    if zoom <= 1.001:
        return img
    h, w = img.shape[:2]
    cw, ch = int(w / zoom), int(h / zoom)
    max_dx, max_dy = (w - cw) // 2, (h - ch) // 2
    cx = w // 2 + int(pan * max_dx)
    cy = h // 2 + int(tilt * max_dy)
    x0 = max(0, min(w - cw, cx - cw // 2))
    y0 = max(0, min(h - ch, cy - ch // 2))
    return img[y0:y0 + ch, x0:x0 + cw]


class VirtualCamera:
    """Uma camera virtual (canal) derivada do ``CaptureService``."""

    def __init__(self, device_id: str, brand: Brand, model: str, channel: ChannelSpec,
                 capture: CaptureService, bus: EventBus, *, reboot_range: tuple[float, float] = (20.0, 40.0),
                 motion_hold_s: float = 3.0, motion_warmup_frames: int = 20,
                 video_loss_after_s: float = 3.0) -> None:
        self.device_id = device_id
        self.brand = brand
        self.model = model
        self.spec = channel
        self.capture = capture
        self.bus = bus
        self.reboot_range = reboot_range
        self.video_loss_after_s = video_loss_after_s

        self.state = CameraState.STOPPED
        self.recording = False  # ligado pelo servico de gravacao (REC piscando)
        self.recording_kind = ""  # continuous | motion | manual
        self.boot_time = time.monotonic()
        self.stats: dict[str, float] = {"fps": 0.0, "bitrate_kbps": 0.0, "frames": 0}
        self.on_state_change: list[Callable[["VirtualCamera", CameraState], None]] = []

        self.ptz = PtzState()
        self.motion = MotionDetector(channel.motion.sensitivity, channel.motion.regions, motion_warmup_frames)
        self.motion_tracker = MotionEventTracker(hold_seconds=motion_hold_s)
        self.last_motion = MotionResult()

        self._cond = threading.Condition()
        self._lock = threading.RLock()
        self._frame: Optional[np.ndarray] = None
        self._sub_cache: tuple[int, Optional[np.ndarray]] = (-1, None)
        self._seq = 0
        self._reboot_until = 0.0
        self._video_lost = False
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._noise_pos: Optional[np.ndarray] = None
        self._noise_neg: Optional[np.ndarray] = None
        self._rng = random.Random(hash((device_id, channel.index)))

    # ------------------------------------------------------------------ propriedades
    @property
    def channel(self) -> int:
        return self.spec.index

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def key(self) -> str:
        return f"{self.device_id}:{self.channel}"

    def uptime(self) -> float:
        """Segundos desde o ultimo boot (zera no reboot)."""
        return max(0.0, time.monotonic() - self.boot_time) if self.state != CameraState.STOPPED else 0.0

    def reboot_remaining(self) -> float:
        return max(0.0, self._reboot_until - time.monotonic()) if self.state == CameraState.REBOOTING else 0.0

    @property
    def is_online(self) -> bool:
        return self.state == CameraState.ONLINE

    # ------------------------------------------------------------------ ciclo de vida
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.boot_time = time.monotonic()
        self._set_state(CameraState.ONLINE, publish=False)
        self._thread = threading.Thread(target=self._run, name=f"cam-{self.key}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        self._set_state(CameraState.STOPPED, publish=False)
        with self._cond:
            self._cond.notify_all()

    def reboot(self, seconds: Optional[float] = None) -> float:
        """Inicia o reboot simulado. Retorna a duracao em segundos."""
        if self.state != CameraState.ONLINE:
            return self.reboot_remaining()
        dur = seconds if seconds is not None else self._rng.uniform(*self.reboot_range)
        with self._lock:
            self._reboot_until = time.monotonic() + dur
            if self.recording:
                self.recording = False
        self._set_state(CameraState.REBOOTING)
        self.bus.publish(EventType.CAMERA_REBOOTING, self.device_id, self.channel, seconds=round(dur, 1))
        return dur

    def _set_state(self, new: CameraState, publish: bool = True) -> None:
        if self.state == new:
            return
        self.state = new
        for cb in list(self.on_state_change):
            try:
                cb(self, new)
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).exception("on_state_change falhou")

    # ------------------------------------------------------------------ configuracao ao vivo
    def apply_spec(self, spec: ChannelSpec) -> None:
        """Aplica mudancas de configuracao (nome, variacoes, movimento) sem reiniciar."""
        with self._lock:
            self.spec = spec
            self.motion.set_sensitivity(spec.motion.sensitivity)
            self.motion.set_regions(spec.motion.regions)
            self._noise_pos = self._noise_neg = None

    # ------------------------------------------------------------------ PTZ fake
    def ptz_continuous(self, vx: float, vy: float, vz: float = 0.0) -> None:
        """Movimento continuo (-1..1 por eixo)."""
        with self._lock:
            self.ptz.vx, self.ptz.vy, self.ptz.vz = (max(-1.0, min(1.0, v)) for v in (vx, vy, vz))

    def ptz_stop(self) -> None:
        self.ptz_continuous(0.0, 0.0, 0.0)

    def ptz_home(self) -> None:
        with self._lock:
            self.ptz = PtzState(last_move=time.monotonic())
        self.motion.reset()

    # ------------------------------------------------------------------ leitura de frames
    def wait_frame(self, last_seq: int = 0, timeout: float = 1.0) -> tuple[Optional[np.ndarray], int]:
        """Espera um frame novo do stream principal."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._seq <= last_seq and not self._stop.is_set():
                rem = deadline - time.monotonic()
                if rem <= 0:
                    break
                self._cond.wait(rem)
            return self._frame, self._seq

    def get_frame(self, stream: str = "main") -> Optional[np.ndarray]:
        """Ultimo frame. ``stream`` = ``main`` ou ``sub``. Nao modifique o resultado."""
        with self._cond:
            frame, seq = self._frame, self._seq
        if frame is None or stream == "main":
            return frame
        cached_seq, cached = self._sub_cache
        if cached_seq == seq and cached is not None:
            return cached
        sub = cv2.resize(frame, (self.spec.sub.width, self.spec.sub.height), interpolation=cv2.INTER_AREA)
        self._sub_cache = (seq, sub)
        return sub

    def snapshot_jpeg(self, stream: str = "main", quality: int = 85) -> Optional[bytes]:
        """JPEG do quadro atual; ``None`` se a camera nao estiver online."""
        if not self.is_online:
            return None
        frame = self.get_frame(stream)
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        return bytes(buf) if ok else None

    # ------------------------------------------------------------------ laco principal
    def _make_noise(self, h: int, w: int) -> None:
        amp = int(self.spec.noise)
        rng = np.random.default_rng(self.channel * 7919)
        # buffers com o dobro da altura: janela aleatoria evita recomputar ruido por quadro
        self._noise_pos = rng.integers(0, amp + 1, (h * 2, w, 3), dtype=np.uint8)
        self._noise_neg = rng.integers(0, amp + 1, (h * 2, w, 3), dtype=np.uint8)

    def _compose(self, base: np.ndarray, dt: float) -> np.ndarray:
        """Aplica variacoes + PTZ + resize ao frame base. Retorna um frame novo (pre-OSD)."""
        with self._lock:
            spec, ptz = self.spec, self.ptz
            if ptz.vx or ptz.vy or ptz.vz:
                ptz.pan = max(-1.0, min(1.0, ptz.pan + ptz.vx * dt * 0.8))
                ptz.tilt = max(-1.0, min(1.0, ptz.tilt + ptz.vy * dt * 0.8))
                ptz.zoom = max(1.0, min(4.0, ptz.zoom + ptz.vz * dt * 1.2))
                ptz.last_move = time.monotonic()
            zoom, pan, tilt = spec.zoom * ptz.zoom, ptz.pan, ptz.tilt

        img = cv2.flip(base, 1) if spec.mirror else base
        img = _crop_zoom(img, zoom, pan, tilt)
        mw, mh = spec.main.width, spec.main.height
        if img.shape[1] != mw or img.shape[0] != mh:
            interp = cv2.INTER_AREA if img.shape[1] > mw else cv2.INTER_LINEAR
            img = cv2.resize(img, (mw, mh), interpolation=interp)
        else:
            img = img.copy()

        if spec.hue_shift:
            hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
            hsv[..., 0] = ((hsv[..., 0].astype(np.int16) + spec.hue_shift // 2) % 180).astype(np.uint8)
            img = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        if spec.noise > 0:
            if self._noise_pos is None or self._noise_pos.shape[1] != mw or self._noise_pos.shape[0] != mh * 2:
                self._make_noise(mh, mw)
            off = self._rng.randrange(0, mh)
            img = cv2.add(img, self._noise_pos[off:off + mh])
            img = cv2.subtract(img, self._noise_neg[off:off + mh])
        return img

    def _handle_motion(self, img: np.ndarray, now_mono: float) -> None:
        spec = self.spec
        if not spec.motion.enabled:
            ended = self.motion_tracker.reset()
            if ended:
                self.bus.publish(EventType.MOTION_STOP, self.device_id, self.channel)
            return
        # PTZ em movimento gera "movimento" falso: suprime e reinicia o fundo
        if self.ptz.last_move and now_mono - self.ptz.last_move < 1.0:
            self.motion.reset()
            return
        res = self.motion.process(img)
        self.last_motion = res
        change = self.motion_tracker.update(res.active, now_mono)
        if change == "start":
            self.bus.publish(EventType.MOTION_START, self.device_id, self.channel,
                             score=round(res.score, 4), boxes=res.boxes[:8])
        elif change == "stop":
            self.bus.publish(EventType.MOTION_STOP, self.device_id, self.channel)

    def _run(self) -> None:
        last_seq = 0
        base: Optional[np.ndarray] = None
        t_prev = time.monotonic()
        n, t_fps = 0, time.monotonic()
        frame_i = 0

        while not self._stop.is_set():
            period = 1.0 / max(1, self.spec.main.fps)
            t_start = time.monotonic()
            dt = t_start - t_prev
            t_prev = t_start

            new, seq = self.capture.latest()
            if new is not None:
                base = new
                last_seq = seq

            # --- perda de video (sem frames da fonte)
            lost = self.capture.last_frame_age > self.video_loss_after_s
            if lost and not self._video_lost:
                self._video_lost = True
                self.bus.publish(EventType.VIDEO_LOSS, self.device_id, self.channel)
            elif not lost and self._video_lost:
                self._video_lost = False
                self.bus.publish(EventType.VIDEO_RESTORED, self.device_id, self.channel)

            if base is None:
                self._stop.wait(0.05)
                continue

            now_dt = datetime.now()
            style = OsdStyle(brand=self.brand.value, channel=self.channel, channel_name=self.spec.name,
                             model=self.model, show_datetime=self.spec.osd_enabled,
                             show_name=self.spec.osd_enabled, show_badge=True)

            if self.state == CameraState.REBOOTING:
                if time.monotonic() >= self._reboot_until:
                    self.boot_time = time.monotonic()
                    self.motion.reset()
                    self.motion_tracker.reset()
                    self._set_state(CameraState.ONLINE)
                    self.bus.publish(EventType.CAMERA_ONLINE, self.device_id, self.channel)
                else:
                    img = np.zeros((self.spec.main.height, self.spec.main.width, 3), np.uint8)
                    render_rebooting(img, self.reboot_remaining(), self.spec.name)
                    self._publish_frame(img)
                    self._stop.wait(0.2)
                    continue

            img = self._compose(base, dt)
            frame_i += 1
            if frame_i % 2 == 0:  # detecta em metade dos quadros (economiza CPU)
                self._handle_motion(img, time.monotonic())
            render_osd(img, style, now_dt, recording=self.recording)
            self._publish_frame(img)

            n += 1
            if time.monotonic() - t_fps >= 1.0:
                self.stats["fps"] = round(n / (time.monotonic() - t_fps), 1)
                n, t_fps = 0, time.monotonic()

            sleep = period - (time.monotonic() - t_start)
            if sleep > 0:
                self._stop.wait(sleep)

    def _publish_frame(self, img: np.ndarray) -> None:
        with self._cond:
            self._frame = img
            self._seq += 1
            self.stats["frames"] = self._seq
            self._cond.notify_all()
