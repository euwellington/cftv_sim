"""Passo 1: fabricante (cards clicaveis com logo)."""
from __future__ import annotations

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWizardPage

from gui.widgets.cards import BrandCard


class BrandPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Fabricante")
        self.setSubTitle("Escolha a marca dos equipamentos que serao simulados.")
        self.choice = ""
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.setSpacing(16)
        self.cards = [
            BrandCard("hikvision", "Hikvision", "ISAPI (XML/JSON), Digest, RTSP /Streaming/Channels", ["hikvision"]),
            BrandCard("intelbras", "Intelbras", "HTTP API CGI (Dahua), Digest, RTSP /cam/realmonitor", ["intelbras"]),
            BrandCard("all", "Todos", "Cria equipamentos das duas marcas ao mesmo tempo, em portas diferentes",
                      ["hikvision", "intelbras"]),
        ]
        for c in self.cards:
            c.clicked.connect(self._pick)
            row.addWidget(c)
        lay.addLayout(row)
        self.hint = QLabel("")
        self.hint.setObjectName("Muted")
        lay.addWidget(self.hint)

    def _pick(self, key: str) -> None:
        self.choice = key
        for c in self.cards:
            c.set_selected(c.key == key)
        self.hint.setText({"hikvision": "Selecionado: Hikvision", "intelbras": "Selecionado: Intelbras",
                           "all": "Selecionado: Hikvision + Intelbras"}[key])
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802
        return bool(self.choice)

    def validatePage(self) -> bool:  # noqa: N802
        self.wizard().data["brand_choice"] = self.choice  # type: ignore[attr-defined]
        return True
