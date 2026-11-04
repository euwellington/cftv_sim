"""Passo 2: modo (camera IP unica ou DVR/NVR com 1 a 5 cameras) e tipo de gravador."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QComboBox, QFormLayout, QGroupBox, QHBoxLayout, QLabel, QSlider, QSpinBox, QVBoxLayout,
                             QWizardPage)

from gui.widgets.cards import BrandCard


class ModePage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Modo")
        self.setSubTitle("Camera IP unica ou sistema DVR/NVR com 1 a 5 cameras (todas derivadas da webcam).")
        self.mode = ""
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.setSpacing(16)
        self.cards = [
            BrandCard("camera", "Camera IP unica", "Uma camera com stream principal e extra", []),
            BrandCard("recorder", "Sistema DVR/NVR", "Gravador com ate 5 canais, HDD virtual e busca de gravacoes", []),
        ]
        for c in self.cards:
            c.clicked.connect(self._pick)
            c.setMinimumHeight(120)
            row.addWidget(c)
        lay.addLayout(row)

        self.box = QGroupBox("Gravador")
        form = QFormLayout(self.box)
        cnt = QHBoxLayout()
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(1, 5)
        self.slider.setValue(4)
        self.spin = QSpinBox()
        self.spin.setRange(1, 5)
        self.spin.setValue(4)
        self.slider.valueChanged.connect(self.spin.setValue)
        self.spin.valueChanged.connect(self.slider.setValue)
        cnt.addWidget(self.slider, 1)
        cnt.addWidget(self.spin)
        form.addRow("Numero de cameras", cnt)
        self.hik_type = QComboBox()
        self.hik_type.addItem("NVR - DS-76xxNI (cameras IP)", "NVR")
        self.hik_type.addItem("DVR - DS-72xx (analogico/HD)", "DVR")
        self.int_type = QComboBox()
        self.int_type.addItem("NVD - NVR IP", "NVD")
        self.int_type.addItem("MHDX - DVR multi-HD", "MHDX")
        self.int_type.addItem("iMHDX - DVR/XVR multi-HD", "iMHDX")
        self.hik_label = QLabel("Hikvision")
        self.int_label = QLabel("Intelbras")
        form.addRow(self.hik_label, self.hik_type)
        form.addRow(self.int_label, self.int_type)
        lay.addWidget(self.box)
        lay.addStretch(1)
        self.box.setEnabled(False)

    def initializePage(self) -> None:  # noqa: N802
        brand = self.wizard().data.get("brand_choice", "hikvision")  # type: ignore[attr-defined]
        show_h, show_i = brand in ("hikvision", "all"), brand in ("intelbras", "all")
        self.hik_label.setVisible(show_h)
        self.hik_type.setVisible(show_h)
        self.int_label.setVisible(show_i)
        self.int_type.setVisible(show_i)

    def _pick(self, key: str) -> None:
        self.mode = key
        for c in self.cards:
            c.set_selected(c.key == key)
        self.box.setEnabled(key == "recorder")
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802
        return bool(self.mode)

    def validatePage(self) -> bool:  # noqa: N802
        d = self.wizard().data  # type: ignore[attr-defined]
        d["mode"] = self.mode
        d["camera_count"] = self.spin.value() if self.mode == "recorder" else 1
        d["hik_recorder"] = self.hik_type.currentData()
        d["intelbras_recorder"] = self.int_type.currentData()
        return True
