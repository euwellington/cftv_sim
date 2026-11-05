"""Logs: Sistema / Operacao / Alarme / Excecao, com busca e exportacao CSV."""
from __future__ import annotations

import csv

from PyQt6.QtCore import QDateTime, QTimer
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateTimeEdit, QFileDialog, QHBoxLayout,
                             QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QTableWidget,
                             QTableWidgetItem)

from core.fmt import local_to_utc, utc_to_local
from gui.pages.base import BasePage
from gui.workers import run_async

CATS = [("Todas", None), ("Sistema", "system"), ("Operacao", "operation"), ("Alarme", "alarm"), ("Excecao", "exception")]
CAT_LABEL = {k: v for v, k in CATS if k}


class LogsPage(BasePage):
    title = "Logs"
    subtitle = "Sistema, operacao, alarme e excecao"

    def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
        super().__init__(*a, **kw)
        bar = QHBoxLayout()
        self.cat = QComboBox()
        for label, key in CATS:
            self.cat.addItem(label, key)
        self.dev = QComboBox()
        self.dev.addItem("Todos os equipamentos", None)
        for d in self.ctx.profile.devices:
            self.dev.addItem(d.id, d.id)
        self.text = QLineEdit()
        self.text.setPlaceholderText("Buscar no texto...")
        self.use_range = QCheckBox("Periodo")
        now = QDateTime.currentDateTime()
        self.t0 = QDateTimeEdit(now.addDays(-1))
        self.t1 = QDateTimeEdit(now.addDays(1))
        for t in (self.t0, self.t1):
            t.setCalendarPopup(True)
            t.setDisplayFormat("dd/MM/yyyy HH:mm")
            t.setEnabled(False)
        self.use_range.toggled.connect(lambda on: (self.t0.setEnabled(on), self.t1.setEnabled(on), self.load()))
        self.live = QCheckBox("Atualizar sozinho")
        self.live.setChecked(True)
        go = QPushButton("Buscar")
        go.clicked.connect(self.load)
        exp = QPushButton("Exportar CSV")
        exp.clicked.connect(self._export)
        self.text.returnPressed.connect(self.load)
        self.cat.currentIndexChanged.connect(self.load)
        self.dev.currentIndexChanged.connect(self.load)
        for w in (self.cat, self.dev, self.text, self.use_range, self.t0, self.t1, go, exp, self.live):
            bar.addWidget(w)
        self.root.addLayout(bar)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Hora", "Categoria", "Equipamento", "Usuario", "IP", "Mensagem"])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        hh.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        self.root.addWidget(self.table, 1)
        self.count = QLabel("")
        self.count.setObjectName("Muted")
        self.root.addWidget(self.count)
        self._rows: list = []
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.isVisible() and self.live.isChecked() and self.load())
        self._timer.start(3000)
        self.load()

    def on_show(self) -> None:
        self.load()

    def load(self) -> None:
        db, cat, dev, text = self.ctx.db, self.cat.currentData(), self.dev.currentData(), self.text.text().strip() or None
        a = b = None
        if self.use_range.isChecked():
            a = local_to_utc(self.t0.dateTime().toPyDateTime())
            b = local_to_utc(self.t1.dateTime().toPyDateTime())
        run_async(lambda: db.logs(device_id=dev, category=cat, text=text, start=a, end=b, limit=2000), on_done=self._fill)

    def _fill(self, rows) -> None:  # noqa: ANN001
        self._rows = rows
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            vals = [utc_to_local(r.ts).strftime("%d/%m/%Y %H:%M:%S"), CAT_LABEL.get(r.category, r.category),
                    r.device_id, r.user, r.ip, r.message]
            for j, v in enumerate(vals):
                self.table.setItem(i, j, QTableWidgetItem(v))
        self.table.setUpdatesEnabled(True)
        self.count.setText(f"{len(rows)} registro(s)")

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Exportar logs", "logs.csv", "CSV (*.csv)")
        if not path:
            return
        rows = list(self._rows)

        def write() -> int:
            with open(path, "w", newline="", encoding="utf-8-sig") as fh:  # utf-8-sig: abre acentos no Excel
                w = csv.writer(fh, delimiter=";")
                w.writerow(["hora", "categoria", "equipamento", "usuario", "ip", "mensagem"])
                for r in rows:
                    w.writerow([utc_to_local(r.ts).isoformat(sep=" ", timespec="seconds"), r.category, r.device_id,
                                r.user, r.ip, r.message])
            return len(rows)

        run_async(write, on_done=lambda n: QMessageBox.information(self, "Exportar", f"{n} registros salvos em\n{path}"),
                  on_error=lambda m: QMessageBox.warning(self, "Exportar", m))
