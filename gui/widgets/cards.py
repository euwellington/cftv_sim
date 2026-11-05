"""Cards reutilizaveis: BrandCard (clicavel), StatCard (numero grande) e helpers."""
from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from gui.theme import repolish
from gui.widgets.logo_widget import LogoWidget


class BrandCard(QFrame):
    """Card clicavel com logo(s) e descricao. Exclusivo dentro de um grupo (ver ``select``)."""

    clicked = pyqtSignal(str)

    def __init__(self, key: str, title: str, subtitle: str, brands: list[str], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.key = key
        self.setObjectName("Card")
        self.setProperty("clickable", True)
        self.setProperty("selected", False)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumSize(230, 190)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(18, 18, 18, 16)
        lay.setSpacing(10)
        lay.addStretch(1)
        logos = QHBoxLayout()
        logos.setSpacing(8)
        logos.addStretch(1)
        for b in brands:
            logos.addWidget(LogoWidget(b, height=34 if len(brands) > 1 else 46))
        logos.addStretch(1)
        lay.addLayout(logos)
        lay.addStretch(1)
        t = QLabel(title)
        t.setObjectName("H2")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        s = QLabel(subtitle)
        s.setObjectName("Muted")
        s.setWordWrap(True)
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        lay.addWidget(s)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", on)
        repolish(self)

    def mousePressEvent(self, ev) -> None:  # noqa: ANN001, N802
        if ev.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.key)
        super().mousePressEvent(ev)


class StatCard(QFrame):
    """Numero grande com rotulo e linha de apoio."""

    def __init__(self, title: str, value: str = "-", hint: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatCard")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(16, 12, 16, 12)
        lay.setSpacing(2)
        self._title = QLabel(title)
        self._title.setObjectName("Muted")
        self._value = QLabel(value)
        self._value.setObjectName("StatValue")
        self._hint = QLabel(hint)
        self._hint.setObjectName("Muted")
        lay.addWidget(self._title)
        lay.addWidget(self._value)
        lay.addWidget(self._hint)

    def set(self, value: str, hint: str = "") -> None:
        self._value.setText(value)
        self._hint.setText(hint)


class UsageBar(QProgressBar):
    """Barra de uso que muda de cor (ok / warn / bad) conforme o percentual."""

    def set_percent(self, pct: float) -> None:
        self.setRange(0, 100)
        self.setValue(int(max(0, min(100, pct))))
        self.setFormat(f"{pct:.0f}%")
        level = "bad" if pct >= 90 else "warn" if pct >= 75 else "ok"
        if self.property("level") != level:
            self.setProperty("level", level)
            repolish(self)


def panel(title: str = "") -> tuple[QFrame, QVBoxLayout]:
    """Painel arredondado com titulo opcional. Retorna (frame, layout)."""
    f = QFrame()
    f.setObjectName("Panel")
    lay = QVBoxLayout(f)
    lay.setContentsMargins(16, 14, 16, 14)
    lay.setSpacing(10)
    if title:
        h = QLabel(title)
        h.setObjectName("H2")
        lay.addWidget(h)
    return f, lay
