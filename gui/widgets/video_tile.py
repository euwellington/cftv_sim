"""VideoTile: desenha o quadro de uma camera virtual; FullscreenViewer: tela cheia com PTZ por teclado."""
from __future__ import annotations

from typing import Optional

import numpy as np
from PyQt6.QtCore import QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPen
from PyQt6.QtWidgets import QVBoxLayout, QWidget

from core.virtual_camera import CameraState, VirtualCamera
from gui.theme import ACCENTS


def frame_to_qimage(frame: np.ndarray) -> QImage:
    """BGR (numpy) -> QImage sem copia. O chamador deve manter ``frame`` vivo."""
    h, w = frame.shape[:2]
    return QImage(frame.data, w, h, frame.strides[0], QImage.Format.Format_BGR888)


class VideoTile(QWidget):
    """Um quadro de video com nome, estado e destaque de movimento/selecao."""

    clicked = pyqtSignal(object)
    double_clicked = pyqtSignal(object)

    def __init__(self, camera: Optional[VirtualCamera] = None, stream: str = "sub", accent: str = "#3b82f6",
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.camera = camera
        self.stream = stream
        self.accent = accent
        self.selected = False
        self.show_label = True
        self._frame: Optional[np.ndarray] = None
        self._image: Optional[QImage] = None
        self._seq = -1
        self.setMinimumSize(160, 90)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    def set_camera(self, cam: Optional[VirtualCamera], stream: Optional[str] = None) -> None:
        self.camera = cam
        if stream:
            self.stream = stream
        self._seq = -1
        self._frame = self._image = None
        self.update()

    def refresh(self) -> None:
        """Chamado pelo timer do grid: puxa o quadro novo e repinta se mudou."""
        cam = self.camera
        if cam is None:
            return
        seq = cam.stats.get("frames", 0)
        changed = seq != self._seq or cam.state != CameraState.ONLINE
        if not changed:
            return
        self._seq = seq
        fr = cam.get_frame(self.stream)
        if fr is not None:
            self._frame = fr
            self._image = frame_to_qimage(fr)
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#05070a"))
        p.setRenderHints(QPainter.RenderHint.SmoothPixmapTransform | QPainter.RenderHint.Antialiasing
                         | QPainter.RenderHint.TextAntialiasing)
        r = self.rect()
        if self._image is not None and not self._image.isNull():
            iw, ih = self._image.width(), self._image.height()
            scale = min(r.width() / iw, r.height() / ih)
            w, h = iw * scale, ih * scale
            target = QRectF((r.width() - w) / 2, (r.height() - h) / 2, w, h)
            p.drawImage(target, self._image)
        else:
            p.setPen(QColor("#596073"))
            p.drawText(r, Qt.AlignmentFlag.AlignCenter, "sem sinal" if self.camera else "vazio")

        cam = self.camera
        motion = bool(cam and cam.motion_tracker.active)
        border = QColor("#f5c518") if motion else QColor(self.accent) if self.selected else QColor("#262e3d")
        p.setPen(QPen(border, 3 if (motion or self.selected) else 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(r.adjusted(1, 1, -2, -2))

        if cam and self.show_label:
            f = QFont(self.font())
            f.setPointSize(8)
            f.setBold(True)
            p.setFont(f)
            bits = [f"CH{cam.channel}"]
            if cam.state == CameraState.REBOOTING:
                bits.append(f"REBOOT {int(cam.reboot_remaining()) + 1}s")
            elif cam.state != CameraState.ONLINE:
                bits.append("OFFLINE")
            if motion:
                bits.append("MOV")
            txt = "  ".join(bits)
            fm = p.fontMetrics()
            tw = fm.horizontalAdvance(txt) + 14
            p.fillRect(QRectF(6, 6, tw, 20), QColor(0, 0, 0, 150))
            p.setPen(QColor("#ffffff"))
            p.drawText(QRectF(6, 6, tw, 20), Qt.AlignmentFlag.AlignCenter, txt)
            if cam.recording:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor("#ef4444"))
                p.drawEllipse(QRectF(r.width() - 20, 10, 10, 10))
        p.end()

    def mousePressEvent(self, ev) -> None:  # noqa: ANN001, N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self)

    def mouseDoubleClickEvent(self, ev) -> None:  # noqa: ANN001, N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit(self)


class FullscreenViewer(QWidget):
    """Tela cheia de uma camera (stream principal). Esc fecha; setas = PTZ; +/- = zoom."""

    closed = pyqtSignal()

    def __init__(self, camera: VirtualCamera, accent: str = "#3b82f6") -> None:
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle(f"{camera.name} - tela cheia")
        self.camera = camera
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.tile = VideoTile(camera, "main", accent)
        self.tile.double_clicked.connect(lambda _t: self.close())
        lay.addWidget(self.tile)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.tile.refresh)
        self.timer.start(40)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def keyPressEvent(self, ev) -> None:  # noqa: ANN001, N802
        k = ev.key()
        c = self.camera
        if k == Qt.Key.Key_Escape:
            self.close()
        elif k == Qt.Key.Key_Left:
            c.ptz_continuous(-1, 0, 0)
        elif k == Qt.Key.Key_Right:
            c.ptz_continuous(1, 0, 0)
        elif k == Qt.Key.Key_Up:
            c.ptz_continuous(0, -1, 0)
        elif k == Qt.Key.Key_Down:
            c.ptz_continuous(0, 1, 0)
        elif k in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            c.ptz_continuous(0, 0, 1)
        elif k == Qt.Key.Key_Minus:
            c.ptz_continuous(0, 0, -1)

    def keyReleaseEvent(self, ev) -> None:  # noqa: ANN001, N802
        if not ev.isAutoRepeat():
            self.camera.ptz_stop()

    def closeEvent(self, ev) -> None:  # noqa: ANN001, N802
        self.timer.stop()
        self.camera.ptz_stop()
        self.closed.emit()
        super().closeEvent(ev)
