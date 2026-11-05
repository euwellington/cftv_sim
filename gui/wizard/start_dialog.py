"""Tela inicial: novo perfil (assistente) ou reabrir um perfil salvo."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QDialog, QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton, QVBoxLayout)

from core import config
from core.device_manager import list_profiles, load_profile
from gui.widgets.logo_widget import LogoWidget


class StartDialog(QDialog):
    """``choice``: ``("new", None)`` ou ``("open", nome)``."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("CFTV Simulator")
        self.setMinimumWidth(520)
        self.choice: Optional[tuple[str, Optional[str]]] = None
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 24)
        lay.setSpacing(12)
        logos = QHBoxLayout()
        logos.addStretch(1)
        logos.addWidget(LogoWidget("hikvision", 36))
        logos.addSpacing(12)
        logos.addWidget(LogoWidget("intelbras", 36))
        logos.addStretch(1)
        lay.addLayout(logos)
        t = QLabel("Bem-vindo ao CFTV Simulator")
        t.setObjectName("Title")
        t.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(t)
        s = QLabel("Crie um novo ambiente simulado ou reabra um perfil salvo.")
        s.setObjectName("Subtitle")
        s.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay.addWidget(s)
        new = QPushButton("Novo perfil (assistente de configuracao)")
        new.setProperty("primary", True)
        new.setMinimumHeight(44)
        new.clicked.connect(self._new)
        lay.addWidget(new)
        names = list_profiles()
        if names:
            lay.addWidget(QLabel("Perfis salvos:"))
            self.list = QListWidget()
            for n in names:
                try:
                    p = load_profile(n)
                    label = f"{n}  -  " + " + ".join(f"{d.brand.value} {d.model}" for d in p.devices)
                except Exception:  # noqa: BLE001
                    label = f"{n}  (arquivo invalido)"
                self.list.addItem(label)
                self.list.item(self.list.count() - 1).setData(Qt.ItemDataRole.UserRole, n)
            self.list.setMaximumHeight(160)
            self.list.itemDoubleClicked.connect(lambda _i: self._open())
            lay.addWidget(self.list)
            row = QHBoxLayout()
            op = QPushButton("Abrir selecionado")
            op.clicked.connect(self._open)
            dele = QPushButton("Excluir")
            dele.setProperty("danger", True)
            dele.clicked.connect(self._delete)
            row.addWidget(op)
            row.addWidget(dele)
            lay.addLayout(row)
            self.list.setCurrentRow(0)
        else:
            self.list = None  # type: ignore[assignment]

    def _new(self) -> None:
        self.choice = ("new", None)
        self.accept()

    def _selected(self) -> Optional[str]:
        it = self.list.currentItem() if self.list else None
        return it.data(Qt.ItemDataRole.UserRole) if it else None

    def _open(self) -> None:
        n = self._selected()
        if n:
            self.choice = ("open", n)
            self.accept()

    def _delete(self) -> None:
        n = self._selected()
        if not n:
            return
        if QMessageBox.question(self, "Excluir perfil", f"Excluir o perfil '{n}'?") == QMessageBox.StandardButton.Yes:
            (config.PROFILES_DIR / f"{n}.json").unlink(missing_ok=True)
            self.list.takeItem(self.list.currentRow())
