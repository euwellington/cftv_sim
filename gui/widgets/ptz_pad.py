"""PtzPad: direcionais + zoom + home. Emite movimento continuo enquanto o botao esta pressionado."""
from __future__ import annotations

import qtawesome as qta
from PyQt6.QtCore import QSize, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QToolButton, QWidget


class PtzPad(QWidget):
    """Sinais: ``move(vx, vy, vz)`` ao pressionar, ``stopped()`` ao soltar, ``home()``."""

    move = pyqtSignal(float, float, float)
    stopped = pyqtSignal()
    home = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        g = QGridLayout(self)
        g.setContentsMargins(0, 0, 0, 0)
        g.setSpacing(4)

        def btn(icon: str, vec: tuple[float, float, float], pos: tuple[int, int], tip: str) -> QToolButton:
            b = QToolButton()
            b.setIcon(qta.icon(icon, color="#e6e9ef"))
            b.setIconSize(QSize(18, 18))
            b.setFixedSize(40, 40)
            b.setToolTip(tip)
            b.pressed.connect(lambda v=vec: self.move.emit(*v))
            b.released.connect(self.stopped.emit)
            g.addWidget(b, *pos)
            return b

        btn("fa5s.chevron-up", (0, -1, 0), (0, 1), "Cima")
        btn("fa5s.chevron-left", (-1, 0, 0), (1, 0), "Esquerda")
        btn("fa5s.chevron-right", (1, 0, 0), (1, 2), "Direita")
        btn("fa5s.chevron-down", (0, 1, 0), (2, 1), "Baixo")
        h = QToolButton()
        h.setIcon(qta.icon("fa5s.home", color="#e6e9ef"))
        h.setFixedSize(40, 40)
        h.setToolTip("Posicao inicial")
        h.clicked.connect(self.home.emit)
        g.addWidget(h, 1, 1)
        btn("fa5s.search-plus", (0, 0, 1), (0, 3), "Zoom +")
        btn("fa5s.search-minus", (0, 0, -1), (2, 3), "Zoom -")
