"""Splash animado (fade) com nome e logos das marcas."""
from __future__ import annotations

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from gui.widgets.logo_widget import LogoWidget


class SplashScreen(QWidget):
    """Janela sem moldura que aparece com fade, mostra mensagens de progresso e some."""

    finished = pyqtSignal()

    def __init__(self) -> None:
        super().__init__(None, Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(560, 340)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 36, 40, 28)
        lay.setSpacing(6)
        title = QLabel("CFTV Simulator")
        title.setStyleSheet("font-size: 34px; font-weight: 800; color: white; background: transparent;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        sub = QLabel("Simulador de equipamentos Hikvision e Intelbras - uso em laboratorio")
        sub.setStyleSheet("font-size: 12px; color: #8b94a7; background: transparent;")
        sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logos = QHBoxLayout()
        logos.addStretch(1)
        logos.addWidget(LogoWidget("hikvision", 44))
        logos.addSpacing(18)
        logos.addWidget(LogoWidget("intelbras", 44))
        logos.addStretch(1)
        self.status = QLabel("Iniciando...")
        self.status.setStyleSheet("font-size: 12px; color: #8b94a7; background: transparent;")
        self.status.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addStretch(1)
        lay.addWidget(title)
        lay.addWidget(sub)
        lay.addSpacing(22)
        lay.addLayout(logos)
        lay.addStretch(1)
        lay.addWidget(self.status)
        self.setWindowOpacity(0.0)
        self._anim = QPropertyAnimation(self, b"windowOpacity", self)

    def paintEvent(self, _ev) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        g = QLinearGradient(0, 0, self.width(), self.height())
        g.setColorAt(0, QColor("#141a26"))
        g.setColorAt(1, QColor("#0b0e14"))
        path = QPainterPath()
        path.addRoundedRect(self.rect().adjusted(1, 1, -1, -1).toRectF(), 22, 22)
        p.fillPath(path, g)
        p.setPen(QColor("#262e3d"))
        p.drawPath(path)
        # faixa de acento: vermelho + verde (as duas marcas)
        p.fillRect(40, self.height() - 14, 120, 3, QColor("#E60012"))
        p.fillRect(160, self.height() - 14, 120, 3, QColor("#00A335"))

    def show_for(self, msec: int = 1800, messages: list[str] | None = None) -> None:
        """Fade-in, mensagens e fade-out. Emite ``finished`` ao terminar."""
        screen = QApplication.primaryScreen().availableGeometry()
        self.move(screen.center().x() - self.width() // 2, screen.center().y() - self.height() // 2)
        self.show()
        self._fade(0.0, 1.0, 450)
        msgs = messages or ["Carregando nucleo...", "Preparando interface...", "Pronto"]
        step = max(200, msec // max(1, len(msgs)))
        for i, m in enumerate(msgs):
            QTimer.singleShot(i * step, lambda t=m: self.status.setText(t))
        QTimer.singleShot(msec, self._close)

    def _fade(self, a: float, b: float, ms: int) -> None:
        self._anim.stop()
        self._anim.setDuration(ms)
        self._anim.setStartValue(a)
        self._anim.setEndValue(b)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._anim.start()

    def _close(self) -> None:
        self._fade(1.0, 0.0, 400)
        QTimer.singleShot(420, self._done)

    def _done(self) -> None:
        self.close()
        self.finished.emit()
