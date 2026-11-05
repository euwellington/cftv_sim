"""MotionRegionEditor: desenha regioes de deteccao de movimento sobre o video ao vivo."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget

from core.virtual_camera import VirtualCamera
from gui.widgets.video_tile import frame_to_qimage


class MotionRegionEditor(QWidget):
    """Arraste com o mouse para adicionar uma regiao; vazio = quadro inteiro."""

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.camera: Optional[VirtualCamera] = None
        self._regions: list[tuple[float, float, float, float]] = []
        self._drag_start: Optional[QPointF] = None
        self._drag_now: Optional[QPointF] = None
        self._frame = None
        self.setMinimumSize(320, 180)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(100)

    def set_camera(self, cam: Optional[VirtualCamera]) -> None:
        self.camera = cam

    def regions(self) -> list[tuple[float, float, float, float]]:
        return list(self._regions)

    def set_regions(self, regions) -> None:  # noqa: ANN001
        self._regions = [tuple(r) for r in regions]  # type: ignore[misc]
        self.update()

    def clear(self) -> None:
        self._regions.clear()
        self.update()
        self.changed.emit()

    def _tick(self) -> None:
        if self.camera is not None and self.isVisible():
            self._frame = self.camera.get_frame("sub")
            self.update()

    def _video_rect(self) -> QRectF:
        w, h = (self._frame.shape[1], self._frame.shape[0]) if self._frame is not None else (16, 9)
        s = min(self.width() / w, self.height() / h)
        vw, vh = w * s, h * s
        return QRectF((self.width() - vw) / 2, (self.height() - vh) / 2, vw, vh)

    def paintEvent(self, _ev) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#05070a"))
        vr = self._video_rect()
        if self._frame is not None:
            p.drawImage(vr, frame_to_qimage(self._frame))
        p.setPen(QPen(QColor("#f5c518"), 2))
        p.setBrush(QColor(245, 197, 24, 60))
        for x, y, w, h in self._regions:
            p.drawRect(QRectF(vr.x() + x * vr.width(), vr.y() + y * vr.height(), w * vr.width(), h * vr.height()))
        if self._drag_start and self._drag_now:
            p.setBrush(QColor(59, 130, 246, 70))
            p.setPen(QPen(QColor("#3b82f6"), 2, Qt.PenStyle.DashLine))
            p.drawRect(QRectF(self._drag_start, self._drag_now).normalized())
        if not self._regions:
            p.setPen(QColor("#e6e9ef"))
            p.drawText(self.rect().adjusted(8, 6, 0, 0), Qt.AlignmentFlag.AlignTop, "Quadro inteiro monitorado. Arraste para limitar a area.")
        p.end()

    def mousePressEvent(self, ev) -> None:  # noqa: ANN001, N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self._drag_start = self._drag_now = ev.position()

    def mouseMoveEvent(self, ev) -> None:  # noqa: ANN001, N802
        if self._drag_start:
            self._drag_now = ev.position()
            self.update()

    def mouseReleaseEvent(self, ev) -> None:  # noqa: ANN001, N802
        if self._drag_start and self._drag_now:
            vr = self._video_rect()
            r = QRectF(self._drag_start, ev.position()).normalized().intersected(vr)
            if r.width() > 8 and r.height() > 8 and vr.width() > 0:
                self._regions.append(((r.x() - vr.x()) / vr.width(), (r.y() - vr.y()) / vr.height(),
                                      r.width() / vr.width(), r.height() / vr.height()))
                self.changed.emit()
        self._drag_start = self._drag_now = None
        self.update()
