"""Ao vivo: grade 1/4/5 (1 grande + 4 pequenas), tela cheia, snapshot, gravar, PTZ fake e reboot."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import qtawesome as qta
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout)

from core import config
from core.virtual_camera import VirtualCamera
from gui.pages.base import BasePage
from gui.widgets.cards import panel
from gui.widgets.grid_view import GridView
from gui.widgets.ptz_pad import PtzPad
from gui.widgets.video_tile import FullscreenViewer


class LivePage(BasePage):
    title = "Ao vivo"
    subtitle = "Clique para selecionar, duplo clique para tela cheia"

    def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
        super().__init__(*a, **kw)
        self._viewer: Optional[FullscreenViewer] = None
        self._last_snap = ""

        bar = QHBoxLayout()
        self.dev = QComboBox()
        for d in self.ctx.profile.devices:
            self.dev.addItem(f"{d.brand.value.capitalize()} - {d.model}", d.id)
        self.dev.currentIndexChanged.connect(self._device_changed)
        bar.addWidget(QLabel("Equipamento:"))
        bar.addWidget(self.dev)
        bar.addSpacing(12)
        self.layout_btns = {}
        for n, tip in ((1, "1 camera"), (4, "4 cameras (2x2)"), (5, "1 grande + 4 pequenas")):
            b = QPushButton(str(n))
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setFixedWidth(44)
            b.clicked.connect(lambda _c, k=n: self._set_layout(k))
            bar.addWidget(b)
            self.layout_btns[n] = b
        bar.addStretch(1)
        self.root.addLayout(bar)

        body = QHBoxLayout()
        body.setSpacing(14)
        self.grid = GridView(self.accent)
        self.grid.selection_changed.connect(self._selection_changed)
        self.grid.fullscreen_requested.connect(self._fullscreen)
        body.addWidget(self.grid, 1)

        side_panel, sl = panel("Controles")
        side_panel.setFixedWidth(230)
        self.sel_lbl = QLabel("-")
        self.sel_lbl.setObjectName("H2")
        self.state_lbl = QLabel("")
        self.state_lbl.setObjectName("Muted")
        sl.addWidget(self.sel_lbl)
        sl.addWidget(self.state_lbl)
        self.btn_snap = QPushButton(qta.icon("fa5s.camera", color="#e6e9ef"), " Snapshot")
        self.btn_snap.clicked.connect(self._snapshot)
        self.btn_rec = QPushButton(qta.icon("fa5s.circle", color="#ef4444"), " Gravar")
        self.btn_rec.setCheckable(True)
        self.btn_rec.clicked.connect(self._toggle_rec)
        self.btn_full = QPushButton(qta.icon("fa5s.expand", color="#e6e9ef"), " Tela cheia")
        self.btn_full.clicked.connect(lambda: self._fullscreen(self.grid.selected_camera()))
        self.btn_reboot = QPushButton(qta.icon("fa5s.sync", color="#ffb020"), " Reiniciar camera")
        self.btn_reboot.clicked.connect(self._reboot)
        for b in (self.btn_snap, self.btn_rec, self.btn_full, self.btn_reboot):
            sl.addWidget(b)
        lbl = QLabel("PTZ (digital)")
        lbl.setObjectName("Muted")
        sl.addWidget(lbl)
        self.ptz = PtzPad()
        self.ptz.move.connect(lambda x, y, z: self._ptz(x, y, z))
        self.ptz.stopped.connect(lambda: self._ptz(0, 0, 0))
        self.ptz.home.connect(self._ptz_home)
        sl.addWidget(self.ptz)
        self.snap_lbl = QLabel("")
        self.snap_lbl.setObjectName("Muted")
        self.snap_lbl.setWordWrap(True)
        sl.addWidget(self.snap_lbl)
        self.open_btn = QPushButton("Abrir pasta de snapshots")
        self.open_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(config.DATA_DIR / "snapshots"))))
        sl.addWidget(self.open_btn)
        sl.addStretch(1)
        body.addWidget(side_panel)
        self.root.addLayout(body, 1)

        self.bridge.event.connect(lambda _e: self._update_controls())
        self._device_changed()

    # ------------------------------------------------------------------ selecao
    def _cams(self) -> list[VirtualCamera]:
        did = self.dev.currentData()
        return list(self.ctx.manager.get(did).cameras.values()) if did else []

    def _device_changed(self) -> None:
        cams = self._cams()
        self.grid.selected = 0
        self.grid.set_cameras(cams)
        mode = 1 if len(cams) == 1 else (5 if len(cams) == 5 else 4)
        self._set_layout(mode)
        self._update_controls()

    def _set_layout(self, n: int) -> None:
        if len(self._cams()) == 1:
            n = 1
        self.grid.set_layout_mode(n)
        for k, b in self.layout_btns.items():
            b.setChecked(k == n)
            b.setEnabled(len(self._cams()) > 1 or k == 1)
        if n == 5 and len(self._cams()) < 5:
            self.layout_btns[5].setEnabled(len(self._cams()) >= 2)

    def _selection_changed(self, _cam) -> None:  # noqa: ANN001
        self._update_controls()

    def _update_controls(self) -> None:
        cam = self.grid.selected_camera()
        has = cam is not None
        for w in (self.btn_snap, self.btn_rec, self.btn_full, self.btn_reboot, self.ptz):
            w.setEnabled(has)
        if not cam:
            self.sel_lbl.setText("-")
            return
        self.sel_lbl.setText(f"CH{cam.channel} - {cam.name}")
        self.state_lbl.setText(f"{cam.state.value}  |  {cam.stats['fps']:.0f} fps")
        self.btn_rec.setChecked(cam.recording)
        self.btn_rec.setText(" Parar gravacao" if cam.recording else " Gravar")
        self.btn_snap.setEnabled(cam.is_online)
        self.btn_rec.setEnabled(cam.is_online or cam.recording)

    # ------------------------------------------------------------------ acoes
    def _snapshot(self) -> None:
        cam = self.grid.selected_camera()
        data = cam.snapshot_jpeg() if cam else None
        if not data or not cam:
            self.snap_lbl.setText("Camera offline: sem snapshot.")
            return
        out = config.DATA_DIR / "snapshots"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"{cam.device_id}_ch{cam.channel}_{datetime.now():%Y%m%d_%H%M%S}.jpg"
        path.write_bytes(data)
        self._last_snap = str(path)
        self.snap_lbl.setText(f"Salvo: {path.name}")
        self.ctx.db.add_log("operation", f"Snapshot CH{cam.channel}", cam.device_id, "GUI")

    def _toggle_rec(self, on: bool) -> None:
        cam = self.grid.selected_camera()
        if cam and not self.ctx.set_recording(cam.device_id, cam.channel, on):
            self.btn_rec.setChecked(False)
        self._update_controls()

    def _fullscreen(self, cam: Optional[VirtualCamera]) -> None:
        if cam is None or self._viewer is not None:
            return
        self._viewer = FullscreenViewer(cam, self.accent)
        self._viewer.closed.connect(lambda: setattr(self, "_viewer", None))
        self._viewer.showFullScreen()

    def _reboot(self) -> None:
        cam = self.grid.selected_camera()
        if not cam:
            return
        if QMessageBox.question(self, "Reiniciar camera", f"Reiniciar {cam.name}? O stream cai por 20 a 40 s.") \
                == QMessageBox.StandardButton.Yes:
            self.ctx.reboot_camera(cam.device_id, cam.channel)

    def _ptz(self, x: float, y: float, z: float) -> None:
        cam = self.grid.selected_camera()
        if cam:
            cam.ptz_continuous(x, y, z)

    def _ptz_home(self) -> None:
        cam = self.grid.selected_camera()
        if cam:
            cam.ptz_home()
