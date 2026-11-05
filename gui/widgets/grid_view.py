"""GridView: grade de cameras com layouts 1, 4 e 5 (1 grande + 4 pequenas)."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import QGridLayout, QWidget

from core.virtual_camera import VirtualCamera
from gui.widgets.video_tile import VideoTile


class GridView(QWidget):
    """Mostra ate 5 cameras. ``selected`` aponta o indice da camera em foco."""

    selection_changed = pyqtSignal(object)  # VirtualCamera | None
    fullscreen_requested = pyqtSignal(object)

    def __init__(self, accent: str = "#3b82f6", fps: int = 15, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.accent = accent
        self.cameras: list[VirtualCamera] = []
        self.layout_mode = 4
        self.selected = 0
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(6)
        self.tiles = [VideoTile(None, "sub", accent) for _ in range(5)]
        for t in self.tiles:
            t.clicked.connect(self._on_click)
            t.double_clicked.connect(lambda tile: self.fullscreen_requested.emit(tile.camera) if tile.camera else None)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(int(1000 / fps))
        self._relayout()

    # ------------------------------------------------------------------ API
    def set_cameras(self, cams: list[VirtualCamera]) -> None:
        self.cameras = list(cams)
        self.selected = min(self.selected, max(0, len(cams) - 1))
        if len(cams) <= 1:
            self.layout_mode = 1
        self._relayout()

    def set_layout_mode(self, mode: int) -> None:
        if mode not in (1, 4, 5):
            raise ValueError("layout deve ser 1, 4 ou 5")
        self.layout_mode = mode
        self._relayout()

    def selected_camera(self) -> Optional[VirtualCamera]:
        return self.cameras[self.selected] if self.cameras else None

    def select(self, index: int) -> None:
        if 0 <= index < len(self.cameras):
            self.selected = index
            self._relayout()
            self.selection_changed.emit(self.selected_camera())

    # ------------------------------------------------------------------ interno
    def _on_click(self, tile: VideoTile) -> None:
        if tile.camera in self.cameras:
            self.select(self.cameras.index(tile.camera))

    def _tick(self) -> None:
        for t in self.tiles:
            if t.isVisible():
                t.refresh()

    def _relayout(self) -> None:
        while self._grid.count():
            self._grid.takeAt(0)
        for i in range(8):
            self._grid.setRowStretch(i, 0)
            self._grid.setColumnStretch(i, 0)
        for t in self.tiles:
            t.hide()
            t.set_camera(None)
        cams = self.cameras
        if not cams:
            return
        n = len(cams)
        mode = self.layout_mode
        if mode == 1 or n == 1:
            order = [(cams[self.selected], "main", (0, 0, 1, 1))]
            self._grid.setRowStretch(0, 1)
            self._grid.setColumnStretch(0, 1)
        elif mode == 4:
            order = [(c, "sub", (i // 2, i % 2, 1, 1)) for i, c in enumerate(cams[:4])]
            for k in range(2):
                self._grid.setRowStretch(k, 1)
                self._grid.setColumnStretch(k, 1)
        else:  # 5: 1 grande + 4 pequenas
            others = [c for i, c in enumerate(cams) if i != self.selected][:4]
            order = [(cams[self.selected], "main", (0, 0, 3, 4))]
            order += [(c, "sub", (3, i, 1, 1)) for i, c in enumerate(others)]
            self._grid.setRowStretch(0, 3)
            self._grid.setRowStretch(3, 1)
            for k in range(4):
                self._grid.setColumnStretch(k, 1)
        for tile, (cam, stream, (r, c, rs, cs)) in zip(self.tiles, order):
            tile.set_camera(cam, stream)
            tile.selected = cam is cams[self.selected]
            self._grid.addWidget(tile, r, c, rs, cs)
            tile.show()
