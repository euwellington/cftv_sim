"""Medidor de forca de senha com lista de pendencias."""
from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QProgressBar, QVBoxLayout, QWidget

from core.security import PasswordReport, password_strength
from gui.theme import repolish


class StrengthMeter(QWidget):
    """Barra + rotulo + motivos de recusa. Chame ``update_for(senha, usuario)``."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(8)
        self.label = QLabel("")
        self.label.setObjectName("Muted")
        self.issues = QLabel("")
        self.issues.setObjectName("Warn")
        self.issues.setWordWrap(True)
        lay.addWidget(self.bar)
        lay.addWidget(self.label)
        lay.addWidget(self.issues)
        self.report: PasswordReport | None = None

    def update_for(self, password: str, username: str = "") -> PasswordReport:
        rep = password_strength(password, username)
        self.report = rep
        self.bar.setValue(rep.score)
        level = "ok" if rep.ok and rep.score >= 60 else "warn" if rep.score >= 35 else "bad"
        self.bar.setProperty("level", level)
        repolish(self.bar)
        self.label.setText(f"Forca: {rep.label}" if password else "")
        self.issues.setText("\n".join("• " + i for i in rep.issues) if password else "")
        return rep
