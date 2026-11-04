"""Tema escuro com cor de acento da marca ativa (Hikvision vermelho / Intelbras verde)."""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtGui import QColor, QFont
from PyQt6.QtWidgets import QApplication

HIKVISION_RED = "#E60012"
INTELBRAS_GREEN = "#00A335"
NEUTRAL_BLUE = "#3b82f6"

ACCENTS = {"hikvision": HIKVISION_RED, "intelbras": INTELBRAS_GREEN, "all": NEUTRAL_BLUE}

PALETTE = {
    "BG": "#0f1218", "SURFACE": "#161b24", "SURFACE2": "#1d2431", "SIDEBAR": "#11151d", "INPUT": "#0f141c",
    "BORDER": "#262e3d", "TEXT": "#e6e9ef", "MUTED": "#8b94a7",
}


def _lighten(hex_color: str, amount: float) -> str:
    c = QColor(hex_color)
    h, s, l, a = c.getHslF()
    c.setHslF(h, s, min(1.0, l + amount), a)
    return c.name()


def build_qss(accent: str) -> str:
    """Le theme.qss e troca os tokens pelas cores."""
    qss = (Path(__file__).with_name("theme.qss")).read_text(encoding="utf-8")
    c = QColor(accent)
    tokens = dict(PALETTE)
    tokens["ACCENT"] = accent
    tokens["ACCENT_HOVER"] = _lighten(accent, 0.07)
    tokens["ACCENT_BG"] = f"rgba({c.red()},{c.green()},{c.blue()},0.16)"
    for k, v in tokens.items():
        qss = qss.replace(f"@{k}@", v)
    return qss


def apply_theme(app: QApplication, brand: str = "all") -> str:
    """Aplica o tema ao aplicativo. Retorna a cor de acento usada."""
    accent = ACCENTS.get(brand, NEUTRAL_BLUE)
    app.setStyle("Fusion")
    font = QFont()
    font.setPointSize(10)
    app.setFont(font)
    app.setStyleSheet(build_qss(accent))
    return accent


def set_accent(app: QApplication, accent: str) -> None:
    app.setStyleSheet(build_qss(accent))


def repolish(widget) -> None:  # noqa: ANN001
    """Reaplica o estilo depois de mudar uma propriedade dinamica (ex.: primary, level)."""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
