"""Base das paginas do painel: cabecalho com titulo/subtitulo e gancho ``on_show``."""
from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from core.app_context import AppContext
from gui.qt_bridge import QtEventBridge


class BasePage(QWidget):
    """Todas as paginas recebem o contexto e a ponte de eventos."""

    title = ""
    subtitle = ""

    def __init__(self, ctx: AppContext, bridge: QtEventBridge, accent: str = "#3b82f6") -> None:
        super().__init__()
        self.ctx = ctx
        self.bridge = bridge
        self.accent = accent
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(24, 20, 24, 20)
        self.root.setSpacing(14)
        head = QVBoxLayout()
        head.setSpacing(2)
        t = QLabel(self.title)
        t.setObjectName("Title")
        s = QLabel(self.subtitle)
        s.setObjectName("Subtitle")
        head.addWidget(t)
        head.addWidget(s)
        self.header_row = QHBoxLayout()
        self.header_row.addLayout(head, 1)
        self.root.addLayout(self.header_row)

    def on_show(self) -> None:
        """Chamado quando a pagina e aberta (atualize dados aqui)."""

    def device_ids(self) -> list[str]:
        return [d.id for d in self.ctx.profile.devices]
