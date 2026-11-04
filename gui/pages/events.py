"""Eventos em tempo real (movimento, perda de video, login, reboot) com filtros e miniaturas."""
from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (QAbstractItemView, QComboBox, QHBoxLayout, QHeaderView, QLabel, QPushButton,
                             QTableWidget, QTableWidgetItem)

from core.event_bus import Event
from core.fmt import utc_to_local
from gui.pages.base import BasePage
from gui.workers import run_async

TYPES = [("Todos", None), ("Movimento (VMD)", "VMD"), ("Perda de video", "videoloss"), ("Reboot", "reboot"),
         ("Login", "login"), ("Gravacao", "recording")]


class EventsPage(BasePage):
    title = "Eventos"
    subtitle = "Atualiza em tempo real"

    def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
        super().__init__(*a, **kw)
        bar = QHBoxLayout()
        self.dev = QComboBox()
        self.dev.addItem("Todos os equipamentos", None)
        for d in self.ctx.profile.devices:
            self.dev.addItem(f"{d.brand.value.capitalize()} - {d.model}", d.id)
        self.typ = QComboBox()
        for label, key in TYPES:
            self.typ.addItem(label, key)
        self.chan = QComboBox()
        self.chan.addItem("Todos os canais", 0)
        for c in range(1, 6):
            self.chan.addItem(f"Canal {c}", c)
        self.count = QLabel("")
        self.count.setObjectName("Muted")
        btn = QPushButton("Atualizar")
        btn.clicked.connect(self.load)
        for w in (self.dev, self.typ, self.chan):
            w.currentIndexChanged.connect(self.load)
            bar.addWidget(w)
        bar.addWidget(btn)
        bar.addStretch(1)
        bar.addWidget(self.count)
        self.root.addLayout(bar)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Hora", "Equipamento", "Canal", "Tipo", "Estado", "Descricao", "Miniatura"])
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(58)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setIconSize(QSize(96, 54))
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        hh.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(6, 110)
        self.root.addWidget(self.table, 1)

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(600)  # espera o escritor do DeviceManager gravar no banco
        self._debounce.timeout.connect(self.load)
        self.bridge.event.connect(self._on_event)
        self.load()

    def on_show(self) -> None:
        self.load()

    def _on_event(self, ev: Event) -> None:
        if ev.type != "stream.stats" and ev.type != "log":
            self._debounce.start()

    def load(self) -> None:
        did, typ, ch = self.dev.currentData(), self.typ.currentData(), self.chan.currentData()
        db = self.ctx.db
        run_async(lambda: db.events(device_id=did, channel=ch or None, types=[typ] if typ else None, limit=300),
                  on_done=self._fill)

    def _fill(self, rows) -> None:  # noqa: ANN001
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [utc_to_local(r.ts).strftime("%d/%m/%Y %H:%M:%S"), r.device_id, str(r.channel or "-"), r.type,
                    r.state, r.description]
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(v))
            it = QTableWidgetItem()
            if r.thumb:
                pm = QPixmap(r.thumb)
                if not pm.isNull():
                    it.setIcon(QIcon(pm.scaled(96, 54, Qt.AspectRatioMode.KeepAspectRatio)))
            self.table.setItem(i, 6, it)
        self.count.setText(f"{len(rows)} evento(s)")
