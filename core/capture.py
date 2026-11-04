"""CaptureService: unica leitura da webcam, distribuida para as N cameras virtuais.

* Windows: ``cv2.CAP_DSHOW``; demais SOs: backend padrao.
* Sem webcam (ou ``force_test_pattern``): padrao de teste animado, com um objeto
  em movimento para exercitar a deteccao de movimento.
* Frames sao **imutaveis depois de publicados**: o consumidor pode ler sem
  copiar, mas nao deve modificar o array recebido.
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from typing import Optional

import cv2
import numpy as np

log = logging.getLogger(__name__)


def make_test_pattern(width: int, height: int, t: float) -> np.ndarray:
    """Quadro do padrao de teste animado no instante ``t`` (segundos)."""
    y = np.linspace(0, 1, height, dtype=np.float32)[:, None]
    x = np.linspace(0, 1, width, dtype=np.float32)[None, :]
    phase = t * 0.15
    b = (90 + 70 * np.sin(2 * np.pi * (x + phase))) * np.ones_like(y)
    g = (80 + 60 * np.sin(2 * np.pi * (y + phase * 0.7))) * np.ones_like(x)
    r = (70 + 50 * np.sin(2 * np.pi * (x + y - phase)))
    frame = np.dstack([b, g, r]).clip(0, 255).astype(np.uint8)

    # grade de referencia
    step = max(40, width // 16)
    for gx in range(0, width, step):
        cv2.line(frame, (gx, 0), (gx, height), (255, 255, 255), 1)
    for gy in range(0, height, step):
        cv2.line(frame, (0, gy), (width, gy), (255, 255, 255), 1)

    # objeto em movimento (dispara a deteccao); some por alguns segundos a cada ciclo
    cycle = 14.0
    k = (t % cycle) / cycle
    if k < 0.5:
        px = int((k / 0.5) * width)
        py = int(height * (0.5 + 0.3 * np.sin(t * 1.3)))
        radius = max(18, height // 12)
        cv2.circle(frame, (px, py), radius, (0, 220, 255), -1)
        cv2.circle(frame, (px, py), radius, (0, 0, 0), 3)

    scale = height / 720.0
    cv2.putText(frame, "SEM WEBCAM - PADRAO DE TESTE", (int(30 * scale), int(height * 0.5)),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1 * scale, (255, 255, 255), max(2, int(3 * scale)), cv2.LINE_AA)
    return frame


class CaptureService:
    """Thread unica que le a webcam e guarda o ultimo frame."""

    def __init__(self, camera_index: int = 0, width: int = 1280, height: int = 720, fps: int = 25,
                 force_test_pattern: bool = False, reconnect_interval: float = 10.0) -> None:
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.fps = fps
        self.force_test_pattern = force_test_pattern
        self.reconnect_interval = reconnect_interval

        self._cond = threading.Condition()
        self._frame: Optional[np.ndarray] = None
        self._seq = 0
        self._stamp = 0.0
        self._source = "stopped"  # webcam | test-pattern | stopped
        self._fps_measured = 0.0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------ controle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="CaptureService", daemon=True)
        self._thread.start()
        # espera o primeiro frame (padrao de teste e imediato)
        self.wait_frame(0, timeout=5.0)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3.0)
        with self._cond:
            self._source = "stopped"
            self._cond.notify_all()

    # ------------------------------------------------------------------ leitura
    def latest(self) -> tuple[Optional[np.ndarray], int]:
        """(frame, seq). Nao modifique o frame retornado."""
        with self._cond:
            return self._frame, self._seq

    def wait_frame(self, last_seq: int, timeout: float = 1.0) -> tuple[Optional[np.ndarray], int]:
        """Espera um frame com ``seq`` maior que ``last_seq``."""
        deadline = time.monotonic() + timeout
        with self._cond:
            while self._seq <= last_seq and not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._cond.wait(remaining)
            return self._frame, self._seq

    @property
    def source(self) -> str:
        return self._source

    @property
    def fps_measured(self) -> float:
        return self._fps_measured

    @property
    def last_frame_age(self) -> float:
        """Segundos desde o ultimo frame (grande = perda de video)."""
        return (time.monotonic() - self._stamp) if self._stamp else float("inf")

    # ------------------------------------------------------------------ interno
    def _publish(self, frame: np.ndarray) -> None:
        with self._cond:
            self._frame = frame
            self._seq += 1
            self._stamp = time.monotonic()
            self._cond.notify_all()

    def _open_webcam(self) -> Optional[cv2.VideoCapture]:
        backend = cv2.CAP_DSHOW if sys.platform.startswith("win") else cv2.CAP_ANY
        try:
            cap = cv2.VideoCapture(self.camera_index, backend)
        except Exception:  # noqa: BLE001
            log.exception("falha ao abrir a webcam")
            return None
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        cap.set(cv2.CAP_PROP_FPS, self.fps)
        ok, _ = cap.read()
        if not ok:
            cap.release()
            return None
        return cap

    def _run(self) -> None:
        cap: Optional[cv2.VideoCapture] = None
        t0 = time.monotonic()
        next_retry = 0.0
        period = 1.0 / max(1, self.fps)
        n_fps, t_fps = 0, time.monotonic()
        bad_reads = 0

        while not self._stop.is_set():
            now = time.monotonic()
            if cap is None and not self.force_test_pattern and now >= next_retry:
                cap = self._open_webcam()
                next_retry = now + self.reconnect_interval
                if cap is not None:
                    log.info("webcam %s aberta", self.camera_index)
                    self._source = "webcam"
                    bad_reads = 0

            frame: Optional[np.ndarray] = None
            if cap is not None:
                ok, img = cap.read()
                if ok and img is not None:
                    bad_reads = 0
                    frame = img
                    if img.shape[1] != self.width or img.shape[0] != self.height:
                        frame = cv2.resize(img, (self.width, self.height), interpolation=cv2.INTER_AREA)
                else:
                    bad_reads += 1
                    if bad_reads >= 30:
                        log.warning("webcam parou de responder; usando padrao de teste")
                        cap.release()
                        cap = None
                        next_retry = time.monotonic() + self.reconnect_interval
            if frame is None and cap is None:
                self._source = "test-pattern"
                frame = make_test_pattern(self.width, self.height, time.monotonic() - t0)
                self._stop.wait(period)  # padrao de teste: cadencia propria
            if frame is not None:
                self._publish(frame)
                n_fps += 1
                if time.monotonic() - t_fps >= 1.0:
                    self._fps_measured = n_fps / (time.monotonic() - t_fps)
                    n_fps, t_fps = 0, time.monotonic()

        if cap is not None:
            cap.release()
