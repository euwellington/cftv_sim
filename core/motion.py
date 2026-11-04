"""Deteccao de movimento (MOG2) com sensibilidade e regiao por camera.

O detector roda em uma copia reduzida em tons de cinza (320 px de largura).
``MotionEventTracker`` transforma o resultado quadro-a-quadro em eventos
``start`` / ``stop`` com histerese (evita piscar).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Sequence

import cv2
import numpy as np

Region = tuple[float, float, float, float]  # x, y, w, h normalizados (0..1)


@dataclass
class MotionResult:
    active: bool = False
    score: float = 0.0  # fracao da area (0..1) em movimento
    boxes: list[Region] = field(default_factory=list)  # caixas normalizadas


class MotionDetector:
    """MOG2 sobre o frame base. ``sensitivity`` 0..100 (maior = mais sensivel)."""

    WIDTH = 320

    def __init__(self, sensitivity: int = 60, regions: Sequence[Region] = (), warmup_frames: int = 20) -> None:
        self.warmup_frames = warmup_frames
        self._regions: list[Region] = list(regions)
        self._mask: Optional[np.ndarray] = None
        self._size: tuple[int, int] = (0, 0)
        self._seen = 0
        self.set_sensitivity(sensitivity)
        self._mog = self._new_mog()

    # ------------------------------------------------------------------ config
    def _new_mog(self) -> "cv2.BackgroundSubtractorMOG2":
        var_threshold = 50 - 38 * self.sensitivity / 100.0  # 50 (pouco) .. 12 (muito sensivel)
        return cv2.createBackgroundSubtractorMOG2(history=200, varThreshold=var_threshold, detectShadows=False)

    def set_sensitivity(self, value: int) -> None:
        self.sensitivity = max(0, min(100, int(value)))
        # area minima em movimento: 5 % (pouco sensivel) .. 0.2 % (muito sensivel)
        self.min_area = 0.05 - (0.05 - 0.002) * self.sensitivity / 100.0
        if hasattr(self, "_mog"):
            self._mog.setVarThreshold(50 - 38 * self.sensitivity / 100.0)

    def set_regions(self, regions: Sequence[Region]) -> None:
        self._regions = list(regions)
        self._mask = None  # recalculada no proximo frame

    def reset(self) -> None:
        """Reinicia o modelo de fundo (apos reboot, PTZ ou troca de cena)."""
        self._mog = self._new_mog()
        self._seen = 0

    # ------------------------------------------------------------------ processamento
    def _build_mask(self, w: int, h: int) -> Optional[np.ndarray]:
        if not self._regions:
            return None
        mask = np.zeros((h, w), np.uint8)
        for rx, ry, rw, rh in self._regions:
            x0, y0 = int(rx * w), int(ry * h)
            x1, y1 = int((rx + rw) * w), int((ry + rh) * h)
            mask[max(0, y0):min(h, y1), max(0, x0):min(w, x1)] = 255
        return mask

    def process(self, frame: np.ndarray) -> MotionResult:
        """Analisa um frame BGR e devolve o resultado."""
        h0, w0 = frame.shape[:2]
        w = self.WIDTH
        h = max(1, int(h0 * w / w0))
        small = cv2.resize(frame, (w, h), interpolation=cv2.INTER_AREA)
        gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)

        if self._size != (w, h) or self._mask is None and self._regions:
            self._size = (w, h)
            self._mask = self._build_mask(w, h)

        fg = self._mog.apply(gray)
        self._seen += 1
        if self._seen <= self.warmup_frames:
            return MotionResult()

        _, fg = cv2.threshold(fg, 200, 255, cv2.THRESH_BINARY)
        if self._mask is not None:
            fg = cv2.bitwise_and(fg, self._mask)
            area_total = max(1, int(cv2.countNonZero(self._mask)))
        else:
            area_total = w * h
        fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        fg = cv2.dilate(fg, np.ones((5, 5), np.uint8), iterations=2)

        score = cv2.countNonZero(fg) / area_total
        boxes: list[Region] = []
        if score >= self.min_area:
            contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for c in contours:
                if cv2.contourArea(c) < self.min_area * area_total * 0.25:
                    continue
                x, y, bw, bh = cv2.boundingRect(c)
                boxes.append((x / w, y / h, bw / w, bh / h))
        return MotionResult(active=score >= self.min_area, score=float(score), boxes=boxes)


class MotionEventTracker:
    """Converte atividade em eventos ``start``/``stop`` com histerese.

    * ``start`` apos ``min_active_frames`` quadros ativos seguidos;
    * ``stop`` apos ``hold_seconds`` sem atividade.
    """

    def __init__(self, hold_seconds: float = 3.0, min_active_frames: int = 2) -> None:
        self.hold_seconds = hold_seconds
        self.min_active_frames = min_active_frames
        self.active = False
        self._streak = 0
        self._last_active = 0.0

    def update(self, active: bool, now: float) -> Optional[str]:
        """Retorna ``"start"``, ``"stop"`` ou ``None``. ``now`` em segundos (monotonico)."""
        if active:
            self._streak += 1
            self._last_active = now
            if not self.active and self._streak >= self.min_active_frames:
                self.active = True
                return "start"
        else:
            self._streak = 0
            if self.active and now - self._last_active >= self.hold_seconds:
                self.active = False
                return "stop"
        return None

    def reset(self) -> Optional[str]:
        """Forca o fim de um movimento em andamento (retorna ``"stop"`` se havia)."""
        was = self.active
        self.active = False
        self._streak = 0
        return "stop" if was else None
