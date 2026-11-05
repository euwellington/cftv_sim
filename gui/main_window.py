"""Janela principal: sidebar com icones + QStackedWidget com as paginas do painel administrativo."""
from __future__ import annotations

from datetime import datetime

import qtawesome as qta
from PyQt6.QtCore import QSize, Qt, QTimer
from PyQt6.QtGui import QAction, QCloseEvent
from PyQt6.QtWidgets import (QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMainWindow, QMessageBox,
                             QPushButton, QStackedWidget, QToolButton, QVBoxLayout, QWidget)

from core.app_context import AppContext
from gui.pages.dashboard import DashboardPage
from gui.pages.devices import DevicesPage
from gui.pages.events import EventsPage
from gui.pages.live import LivePage
from gui.pages.logs import LogsPage
from gui.pages.recordings import RecordingsPage
from gui.pages.remote import RemotePage
from gui.pages.settings import SettingsPage
from gui.pages.storage import StoragePage
from gui.qt_bridge import QtEventBridge
from gui.theme import ACCENTS, set_accent
from gui.widgets.logo_widget import LogoWidget

PAGES = [
    ("dashboard", "Dashboard", "fa5s.tachometer-alt", DashboardPage),
    ("live", "Ao vivo", "fa5s.video", LivePage),
    ("recordings", "Gravacoes", "fa5s.film", RecordingsPage),
    ("events", "Eventos", "fa5s.bell", EventsPage),
    ("logs", "Logs", "fa5s.file-alt", LogsPage),
    ("settings", "Configuracoes", "fa5s.cog", SettingsPage),
    ("storage", "Armazenamento", "fa5s.hdd", StoragePage),
    ("devices", "Dispositivos", "fa5s.server", DevicesPage),
    ("remote", "Acesso remoto", "fa5s.globe", RemotePage),
]


class MainWindow(QMainWindow):
    """Painel administrativo. ``restart`` indica que o usuario pediu outro perfil/assistente."""

    def __init__(self, ctx: AppContext, accent: str) -> None:
        super().__init__()
        self.ctx = ctx
        self.accent = accent
        self.restart: str = ""  # "" | "new" | "open"
        self.setWindowTitle(f"CFTV Simulator - {ctx.profile.name}")
        self.resize(1360, 840)
        self.bridge = QtEventBridge(ctx.bus, self)

        root = QWidget()
        self.setCentralWidget(root)
        outer = QHBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ---------------- sidebar
        side = QFrame()
        side.setObjectName("Sidebar")
        side.setFixedWidth(210)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 16, 12, 12)
        sl.setSpacing(4)
        brand = QLabel("CFTV Simulator")
        brand.setStyleSheet("font-size: 16px; font-weight: 800;")
        sl.addWidget(brand)
        logos = QHBoxLayout()
        brands = sorted({d.brand.value for d in ctx.profile.devices})
        for b in brands:
            logos.addWidget(LogoWidget(b, 22))
        logos.addStretch(1)
        sl.addLayout(logos)
        sl.addSpacing(14)

        self.stack = QStackedWidget()
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.pages: dict[str, QWidget] = {}
        for i, (key, label, icon, cls) in enumerate(PAGES):
            page = cls(ctx, self.bridge, accent)
            self.pages[key] = page
            self.stack.addWidget(page)
            btn = QToolButton()
            btn.setObjectName("NavButton")
            btn.setText("  " + label)
            btn.setIcon(qta.icon(icon, color="#8b94a7", color_on=accent))
            btn.setIconSize(QSize(18, 18))
            btn.setCheckable(True)
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            btn.setSizePolicy(btn.sizePolicy().horizontalPolicy(), btn.sizePolicy().verticalPolicy())
            btn.setMinimumHeight(40)
            btn.setSizePolicy(btn.sizePolicy().Policy.Expanding, btn.sizePolicy().Policy.Fixed)
            self.group.addButton(btn, i)
            sl.addWidget(btn)
        sl.addStretch(1)

        # troca de cor de acento (util no modo "Todos")
        self.accent_row = QHBoxLayout()
        for key, tip in (("hikvision", "Acento Hikvision"), ("intelbras", "Acento Intelbras"), ("all", "Acento neutro")):
            b = QPushButton("")
            b.setFixedSize(22, 22)
            b.setToolTip(tip)
            b.setStyleSheet(f"background: {ACCENTS[key]}; border-radius: 11px; border: 2px solid #262e3d;")
            b.clicked.connect(lambda _c, k=key: self._set_accent(k))
            self.accent_row.addWidget(b)
        self.accent_row.addStretch(1)
        sl.addLayout(self.accent_row)
        outer.addWidget(side)

        # ---------------- area principal
        right = QVBoxLayout()
        right.setSpacing(0)
        header = QFrame()
        header.setObjectName("Header")
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 8, 20, 8)
        self.h_title = QLabel("")
        self.h_title.setObjectName("H2")
        self.c_source = QLabel("")
        self.c_source.setObjectName("Chip")
        self.c_state = QLabel("")
        self.c_state.setObjectName("Chip")
        self.c_tunnel = QLabel("")
        self.c_tunnel.setObjectName("Chip")
        self.clock = QLabel("")
        self.clock.setObjectName("Muted")
        hl.addWidget(self.h_title, 1)
        for w in (self.c_source, self.c_state, self.c_tunnel, self.clock):
            hl.addWidget(w)
        right.addWidget(header)
        right.addWidget(self.stack, 1)
        outer.addLayout(right, 1)

        self.group.idClicked.connect(self._goto)
        self._menu()
        self.group.button(0).setChecked(True)
        self._goto(0)
        self.statusBar().showMessage("Pronto")

        self._tick_timer = QTimer(self)
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start(1000)
        self._tick()

    # ------------------------------------------------------------------ navegacao
    def _goto(self, idx: int) -> None:
        self.stack.setCurrentIndex(idx)
        self.h_title.setText(f"Perfil: {self.ctx.profile.name}")
        page = self.stack.currentWidget()
        if hasattr(page, "on_show"):
            page.on_show()

    def _set_accent(self, key: str) -> None:
        self.accent = ACCENTS[key]
        set_accent(QApplication.instance(), self.accent)  # type: ignore[arg-type]

    def _menu(self) -> None:
        mb = self.menuBar()
        m = mb.addMenu("Arquivo")
        for text, fn in (("Novo perfil (assistente)...", lambda: self._request("new")),
                         ("Abrir perfil salvo...", lambda: self._request("open")),
                         ("Salvar perfil", self._save), (None, None), ("Sair", self.close)):
            if text is None:
                m.addSeparator()
                continue
            a = QAction(text, self)
            a.triggered.connect(fn)
            m.addAction(a)

    def _save(self) -> None:
        self.ctx.save()
        self.statusBar().showMessage("Perfil salvo.", 4000)

    def _request(self, what: str) -> None:
        if QMessageBox.question(self, "Trocar perfil", "Os equipamentos serao parados. Continuar?") \
                == QMessageBox.StandardButton.Yes:
            self.restart = what
            self.close()

    # ------------------------------------------------------------------ status
    def _tick(self) -> None:
        mgr = self.ctx.manager
        self.clock.setText(datetime.now().strftime("%d/%m/%Y  %H:%M:%S"))
        self.c_source.setText(f"video: {mgr.capture.source}")
        devs = mgr.list()
        n = sum(d.state == "online" for d in devs)
        self.c_state.setText(f"{n}/{len(devs)} online")
        self.c_tunnel.setText("tunel: ativo" if self.ctx.public_url else "tunel: off")
        if self.ctx.service_errors:
            self.statusBar().showMessage("Servicos com erro: " + "; ".join(self.ctx.service_errors), 0)

    def closeEvent(self, ev: QCloseEvent) -> None:  # noqa: N802
        self._tick_timer.stop()
        self.ctx.save()
        self.bridge.close()
        super().closeEvent(ev)
