"""LogoWidget: carrega assets/logos/<marca>.png|svg ou desenha um placeholder com QPainter.

O projeto NAO embute logos protegidas. Se o arquivo do usuario nao existir,
desenha-se um wordmark estilizado (HIKVISION vermelho / intelbras verde).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QRectF, Qt, QSize
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QLinearGradient, QPainter, QPainterPath, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QSizePolicy, QWidget

from core.config import ASSETS_DIR

BRAND_COLORS = {"hikvision": "#E60012", "intelbras": "#00A335"}
BRAND_TEXT = {"hikvision": "HIKVISION", "intelbras": "intelbras"}


def find_logo(brand: str) -> Optional[Path]:
    for ext in ("svg", "png", "jpg", "jpeg", "webp"):
        p = ASSETS_DIR / "logos" / f"{brand}.{ext}"
        if p.exists():
            return p
    return None


class LogoWidget(QWidget):
    """Logo da marca (arquivo do usuario ou placeholder). ``brand``: hikvision | intelbras."""

    def __init__(self, brand: str, height: int = 64, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.brand = brand
        self._h = height
        self._pix: Optional[QPixmap] = None
        self._svg: Optional[QSvgRenderer] = None
        self._load()
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setFixedSize(self.sizeHint())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

    @property
    def has_user_logo(self) -> bool:
        return self._pix is not None or self._svg is not None

    def _load(self) -> None:
        p = find_logo(self.brand)
        if not p:
            return
        if p.suffix.lower() == ".svg":
            r = QSvgRenderer(str(p))
            self._svg = r if r.isValid() else None
        else:
            pm = QPixmap(str(p))
            self._pix = pm if not pm.isNull() else None

    def _aspect(self) -> float:
        if self._svg:
            s = self._svg.defaultSize()
            return s.width() / max(1, s.height())
        if self._pix:
            return self._pix.width() / max(1, self._pix.height())
        return 3.6 if self.brand == "hikvision" else 3.4

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(int(self._h * self._aspect()), self._h)

    def paintEvent(self, _ev) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.SmoothPixmapTransform
                         | QPainter.RenderHint.TextAntialiasing)
        rect = QRectF(self.rect())
        if self._svg:
            self._svg.render(p, rect)
        elif self._pix:
            p.drawPixmap(self.rect(), self._pix)
        else:
            self._paint_placeholder(p, rect)
        p.end()

    def _paint_placeholder(self, p: QPainter, rect: QRectF) -> None:
        base = QColor(BRAND_COLORS.get(self.brand, "#3b82f6"))
        grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
        grad.setColorAt(0.0, base.lighter(118))
        grad.setColorAt(1.0, base.darker(120))
        path = QPainterPath()
        path.addRoundedRect(rect.adjusted(1, 1, -1, -1), rect.height() * 0.22, rect.height() * 0.22)
        p.fillPath(path, grad)
        text = BRAND_TEXT.get(self.brand, self.brand)
        font = QFont("Segoe UI")
        font.setBold(True)
        font.setPixelSize(int(rect.height() * 0.46))
        if self.brand == "hikvision":
            font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 108)
        p.setFont(font)
        p.setPen(QColor("white"))
        fm = QFontMetricsF(font)
        while fm.horizontalAdvance(text) > rect.width() * 0.88 and font.pixelSize() > 6:
            font.setPixelSize(font.pixelSize() - 1)
            p.setFont(font)
            fm = QFontMetricsF(font)
        p.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
