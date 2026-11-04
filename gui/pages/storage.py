"""Armazenamento: discos virtuais com cota em GB, livre, status ok/cheio/erro e "Formatar" (simulado)."""
from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (QAbstractItemView, QDoubleSpinBox, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                             QPushButton, QTableWidget, QTableWidgetItem, QWidget)

from core.fmt import human_bytes
from gui.pages.base import BasePage
from gui.theme import repolish
from gui.widgets.cards import UsageBar
from gui.workers import run_async

STATUS = {"ok": ("OK", "ChipOk"), "full": ("Cheio", "ChipWarn"), "error": ("Erro", "ChipBad")}


class StoragePage(BasePage):
    title = "Armazenamento"
    subtitle = "HDD virtual por equipamento: cota em GB, overwrite e formatacao simulada"

    def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
        super().__init__(*a, **kw)
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Equipamento", "Disco", "Capacidade (GB)", "Usado", "Livre", "Uso", "Status", "Acoes"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(50)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(7, QHeaderView.ResizeMode.ResizeToContents)
        self.root.addWidget(self.table)
        self.msg = QLabel("")
        self.msg.setObjectName("Muted")
        self.root.addWidget(self.msg)
        note = QLabel("Overwrite: quando o disco passa de 95%, os segmentos mais antigos sao apagados ate 90%. "
                      "Sem overwrite (aba Configuracoes > Gravacao), o disco fica 'Cheio' e a gravacao para.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        self.root.addWidget(note)
        self.root.addStretch(1)
        self._rows: list[tuple[str, str]] = []
        self._build()
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.isVisible() and self.refresh())
        self._timer.start(2000)

    def _build(self) -> None:
        self._rows = [(d.id, k.id) for d in self.ctx.profile.devices for k in d.disks]
        self.table.setRowCount(len(self._rows))
        for i, (did, kid) in enumerate(self._rows):
            self.table.setItem(i, 0, QTableWidgetItem(did))
            self.table.setItem(i, 1, QTableWidgetItem(kid))
            sp = QDoubleSpinBox()
            sp.setRange(0.1, 4096)
            sp.setDecimals(1)
            sp.setValue(self.ctx.storage.info(did, kid).capacity / 1024 ** 3)
            sp.editingFinished.connect(lambda s=sp, d=did, k=kid: self._set_capacity(d, k, s.value()))
            self.table.setCellWidget(i, 2, sp)
            for c in (3, 4):
                self.table.setItem(i, c, QTableWidgetItem(""))
            bar = UsageBar()
            self.table.setCellWidget(i, 5, bar)
            chip = QLabel("")
            self.table.setCellWidget(i, 6, chip)
            w = QWidget()
            lay = QHBoxLayout(w)
            lay.setContentsMargins(4, 2, 4, 2)
            fmt = QPushButton("Formatar")
            fmt.setProperty("danger", True)
            fmt.clicked.connect(lambda _c, d=did, k=kid: self._format(d, k))
            err = QPushButton("Simular erro")
            err.clicked.connect(lambda _c, d=did, k=kid: self._toggle_error(d, k))
            lay.addWidget(fmt)
            lay.addWidget(err)
            self.table.setCellWidget(i, 7, w)
        self.refresh()

    def on_show(self) -> None:
        self.refresh()

    def refresh(self) -> None:
        for i, (did, kid) in enumerate(self._rows):
            info = self.ctx.storage.info(did, kid)
            self.table.item(i, 3).setText(human_bytes(info.used))
            self.table.item(i, 4).setText(human_bytes(info.free))
            self.table.cellWidget(i, 5).set_percent(info.percent)
            chip = self.table.cellWidget(i, 6)
            txt, obj = STATUS[info.status]
            chip.setText(txt)
            if chip.objectName() != obj:
                chip.setObjectName(obj)
                repolish(chip)

    def _set_capacity(self, did: str, kid: str, gb: float) -> None:
        self.ctx.storage.set_capacity(did, kid, gb)
        self.ctx.save()
        self.ctx.db.add_log("operation", f"Capacidade de {kid} ajustada para {gb:.1f} GB", did, "GUI")
        self.refresh()

    def _format(self, did: str, kid: str) -> None:
        if QMessageBox.question(self, "Formatar disco", f"Apagar TODAS as gravacoes de {did}/{kid}? (simulado)") \
                != QMessageBox.StandardButton.Yes:
            return
        self.msg.setText("Formatando...")
        run_async(self.ctx.storage.format_disk, did, kid,
                  on_done=lambda n: (self.msg.setText(f"{did}/{kid} formatado: {n} arquivo(s) removido(s)."), self.refresh()),
                  on_error=lambda m: self.msg.setText("Falha: " + m))

    def _toggle_error(self, did: str, kid: str) -> None:
        cur = self.ctx.storage.info(did, kid).status
        self.ctx.storage.set_status(did, kid, "ok" if cur == "error" else "error")
        self.ctx.save()
        self.refresh()
