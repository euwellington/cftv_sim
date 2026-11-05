"""TimelineView (QGraphicsView): linha do tempo por canal, colorida por tipo de gravacao.

Azul = continua, amarelo = movimento, vermelho = manual.
Roda do mouse + Ctrl = zoom; arrastar com botao do meio = rolar; clique = buscar.
Todo o desenho e feito em coordenadas de tela (drawForeground), entao texto nao distorce.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QPainter, QPen
from PyQt6.QtWidgets import QGraphicsScene, QGraphicsView

KIND_COLORS = {"continuous": "#3b82f6", "motion": "#f5c518", "manual": "#ef4444"}
KIND_LABELS = {"continuous": "Continua", "motion": "Movimento", "manual": "Manual"}
DAY = 86400.0
LABEL_W = 56
AXIS_H = 24


class TimelineView(QGraphicsView):
    """Emite ``seek(canal, datetime_local)`` ao clicar em uma faixa."""

    seek = pyqtSignal(int, object)

    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(QGraphicsScene(), parent)
        self.setMinimumHeight(110)
        self.setFrameShape(QGraphicsView.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setMouseTracking(True)
        self.day: datetime = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        self.channels: list[int] = [1]
        self.segments: dict[int, list[tuple[float, float, str]]] = {}
        self.playhead: Optional[float] = None
        self.sel: tuple[Optional[float], Optional[float]] = (None, None)
        self.view_start = 0.0
        self.view_len = DAY
        self._hover: Optional[float] = None
        self._pan_from: Optional[tuple[float, float]] = None

    # ------------------------------------------------------------------ dados
    def set_day(self, day: datetime) -> None:
        self.day = day.replace(hour=0, minute=0, second=0, microsecond=0)
        self.view_start, self.view_len = 0.0, DAY
        self.viewport().update()

    def set_channels(self, channels: list[int]) -> None:
        self.channels = list(channels)
        self.setMinimumHeight(AXIS_H + 34 * max(1, len(channels)) + 8)
        self.viewport().update()

    def set_segments(self, channel: int, segs: list[tuple[datetime, datetime, str]]) -> None:
        """Segmentos em horario LOCAL (naive)."""
        out = []
        for a, b, kind in segs:
            sa = (a - self.day).total_seconds()
            sb = (b - self.day).total_seconds()
            if sb > 0 and sa < DAY:
                out.append((max(0.0, sa), min(DAY, sb), kind))
        self.segments[channel] = out
        self.viewport().update()

    def clear_segments(self) -> None:
        self.segments.clear()
        self.viewport().update()

    def set_playhead(self, when: Optional[datetime]) -> None:
        self.playhead = None if when is None else (when - self.day).total_seconds()
        self.viewport().update()

    def set_selection(self, a: Optional[datetime], b: Optional[datetime]) -> None:
        f = lambda d: None if d is None else (d - self.day).total_seconds()  # noqa: E731
        self.sel = (f(a), f(b))
        self.viewport().update()

    # ------------------------------------------------------------------ geometria
    def _x(self, sec: float) -> float:
        w = max(1, self.viewport().width() - LABEL_W)
        return LABEL_W + (sec - self.view_start) / self.view_len * w

    def _sec(self, x: float) -> float:
        w = max(1, self.viewport().width() - LABEL_W)
        return self.view_start + (x - LABEL_W) / w * self.view_len

    def _row_rect(self, i: int) -> QRectF:
        return QRectF(LABEL_W, AXIS_H + 34 * i + 3, self.viewport().width() - LABEL_W, 28)

    # ------------------------------------------------------------------ desenho
    def drawForeground(self, p: QPainter, _rect) -> None:  # noqa: ANN001, N802
        p.resetTransform()
        vw, vh = self.viewport().width(), self.viewport().height()
        p.fillRect(0, 0, vw, vh, QColor("#0f141c"))
        f = QFont(self.font())
        f.setPointSize(8)
        p.setFont(f)

        # eixo de horas (passo adaptativo)
        span_h = self.view_len / 3600.0
        step = 1 if span_h > 12 else 0.5 if span_h > 6 else 0.25 if span_h > 3 else 1 / 12
        t = int(self.view_start / 3600 / step) * step
        p.setPen(QPen(QColor("#262e3d"), 1))
        while t * 3600 <= self.view_start + self.view_len:
            x = self._x(t * 3600)
            if x >= LABEL_W:
                p.drawLine(int(x), AXIS_H - 6, int(x), vh)
                p.setPen(QColor("#8b94a7"))
                hh, mm = int(t), int(round((t - int(t)) * 60))
                p.drawText(QRectF(x + 3, 2, 50, 16), Qt.AlignmentFlag.AlignLeft, f"{hh:02d}:{mm:02d}")
                p.setPen(QPen(QColor("#262e3d"), 1))
            t += step

        for i, ch in enumerate(self.channels):
            r = self._row_rect(i)
            p.fillRect(r, QColor("#161b24"))
            p.setPen(QColor("#8b94a7"))
            p.drawText(QRectF(4, r.y(), LABEL_W - 8, r.height()), Qt.AlignmentFlag.AlignVCenter, f"CH{ch}")
            for a, b, kind in self.segments.get(ch, []):
                x0, x1 = self._x(a), self._x(b)
                if x1 < LABEL_W or x0 > vw:
                    continue
                x0 = max(x0, LABEL_W)
                p.fillRect(QRectF(x0, r.y() + 2, max(1.5, x1 - x0), r.height() - 4),
                           QColor(KIND_COLORS.get(kind, "#3b82f6")))

        a, b = self.sel
        if a is not None and b is not None:
            lo, hi = sorted((a, b))
            p.fillRect(QRectF(self._x(lo), AXIS_H, self._x(hi) - self._x(lo), vh), QColor(255, 255, 255, 40))
        if self.playhead is not None:
            x = self._x(self.playhead)
            if LABEL_W <= x <= vw:
                p.setPen(QPen(QColor("#ffffff"), 2))
                p.drawLine(int(x), AXIS_H - 6, int(x), vh)
        if self._hover is not None:
            x = self._x(self._hover)
            when = (self.day + timedelta(seconds=self._hover)).strftime("%H:%M:%S")
            p.setPen(QColor("#e6e9ef"))
            p.drawText(QRectF(min(x + 6, vw - 64), vh - 18, 64, 16), Qt.AlignmentFlag.AlignLeft, when)

    # ------------------------------------------------------------------ mouse
    def _row_at(self, y: float) -> Optional[int]:
        for i in range(len(self.channels)):
            r = self._row_rect(i)
            if r.top() <= y <= r.bottom() + 3:
                return i
        return None

    def mousePressEvent(self, ev) -> None:  # noqa: ANN001, N802
        pos = ev.position()
        if ev.button() == Qt.MouseButton.MiddleButton:
            self._pan_from = (pos.x(), self.view_start)
            return
        if ev.button() == Qt.MouseButton.LeftButton and pos.x() >= LABEL_W:
            i = self._row_at(pos.y())
            sec = max(0.0, min(DAY - 1, self._sec(pos.x())))
            idx = i if i is not None else 0
            self.seek.emit(self.channels[idx], self.day + timedelta(seconds=sec))

    def mouseMoveEvent(self, ev) -> None:  # noqa: ANN001, N802
        pos = ev.position()
        self._hover = self._sec(pos.x()) if pos.x() >= LABEL_W else None
        if self._pan_from:
            dx = pos.x() - self._pan_from[0]
            w = max(1, self.viewport().width() - LABEL_W)
            self.view_start = max(0.0, min(DAY - self.view_len, self._pan_from[1] - dx / w * self.view_len))
        self.viewport().update()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: ANN001, N802
        self._pan_from = None

    def leaveEvent(self, ev) -> None:  # noqa: ANN001, N802
        self._hover = None
        self.viewport().update()

    def wheelEvent(self, ev) -> None:  # noqa: ANN001, N802
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier or True:
            anchor = self._sec(ev.position().x())
            factor = 0.8 if ev.angleDelta().y() > 0 else 1.25
            self.view_len = max(600.0, min(DAY, self.view_len * factor))
            frac = (ev.position().x() - LABEL_W) / max(1, self.viewport().width() - LABEL_W)
            self.view_start = max(0.0, min(DAY - self.view_len, anchor - frac * self.view_len))
            self.viewport().update()
