"""Acesso remoto: tunel (Cloudflare / ngrok / Tailscale), URLs publicas, QR code e log."""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QComboBox, QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QVBoxLayout)

from core.event_bus import Event, EventType
from gui.pages.base import BasePage
from gui.pages.dashboard import make_qr_pixmap
from gui.theme import repolish
from gui.widgets.cards import panel
from gui.workers import run_async


class RemotePage(BasePage):
    title = "Acesso remoto"
    subtitle = "Portal web pela internet via tunel HTTPS"

    def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
        super().__init__(*a, **kw)
        top = QHBoxLayout()
        top.setSpacing(14)

        left, ll = panel("Tunel")
        self.provider = QComboBox()
        self.provider.addItem("Cloudflare Tunnel (quick tunnel)", "cloudflare")
        self.provider.addItem("ngrok", "ngrok")
        self.provider.addItem("Tailscale Funnel", "tailscale")
        self.provider.addItem("TCP publico sem conta - bore/pinggy (RTSP/HTTP de fora)", "bore")
        idx = self.provider.findData(self.ctx.profile.public.provider)
        self.provider.setCurrentIndex(max(0, idx))
        self.status = QLabel("Parado")
        self.status.setObjectName("ChipBad")
        self.btn = QPushButton("Iniciar tunel")
        self.btn.setProperty("primary", True)
        self.btn.clicked.connect(self._toggle)
        ll.addWidget(self.provider)
        ll.addWidget(self.status)
        ll.addWidget(self.btn)
        warn = QLabel("O tunel expoe o portal web (HTTPS). RTSP cru nao passa por tunel HTTP: use o portal para ver o video, "
                      "ou ngrok com TCP (Configuracoes > Acesso) para abrir o RTSP no VLC. Na rede Tailscale, use o IP 100.x da maquina.")
        warn.setObjectName("Muted")
        warn.setWordWrap(True)
        ll.addWidget(warn)
        ll.addStretch(1)
        top.addWidget(left, 2)

        mid, ml = panel("URLs")
        self.urls = QLabel("-")
        self.urls.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.urls.setWordWrap(True)
        self.copy = QPushButton("Copiar URL publica")
        self.copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.ctx.public_url))
        self.copy.setEnabled(False)
        ml.addWidget(self.urls)
        ml.addWidget(self.copy)
        ml.addStretch(1)
        top.addWidget(mid, 3)

        right, rl = panel("QR code")
        self.qr = QLabel("")
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rl.addWidget(self.qr)
        top.addWidget(right, 2)
        self.root.addLayout(top)

        lp, lv = panel("Log do tunel")
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setMaximumBlockCount(1000)
        lv.addWidget(self.log)
        self.root.addWidget(lp, 1)
        self.bridge.event.connect(self._on_event)
        self._render()

    def _chip(self, text: str, obj: str) -> None:
        self.status.setText(text)
        self.status.setObjectName(obj)
        repolish(self.status)

    def _render(self) -> None:
        url = self.ctx.public_url
        lines = [f"<b>Portal local:</b> {self.ctx.web_url()}"]
        for did, u in self.ctx.http_urls().items():
            lines.append(f"<span style='color:#8b94a7'>API {did}:</span> {u}")
        if url:
            lines.insert(0, f"<b>Portal publico:</b> {url}")
            if self.ctx.profile.public.expose_brand_apis:
                lines.append(f"<b>API publica:</b> {url}/ISAPI/... e /cgi-bin/... (exposta por opcao do perfil)")
            self.qr.setPixmap(make_qr_pixmap(url, 200))
            self._chip("Ativo", "ChipOk")
            self.btn.setText("Parar tunel")
        else:
            self.qr.clear()
            self.btn.setText("Iniciar tunel")
        self.copy.setEnabled(bool(url))
        self.urls.setText("<br>".join(lines))

    def _toggle(self) -> None:
        self.btn.setEnabled(False)
        if self.ctx.public_url or self.ctx.tunnel:
            self._chip("Parando...", "ChipWarn")
            run_async(self.ctx.stop_tunnel, on_done=lambda _r: self._after(), on_error=self._fail)
        else:
            self.ctx.profile.public.provider = self.provider.currentData()  # type: ignore[assignment]
            self._chip("Iniciando...", "ChipWarn")
            self.log.appendPlainText(f"> iniciando {self.provider.currentData()}...")
            run_async(self.ctx.start_tunnel, on_done=lambda _u: self._after(), on_error=self._fail)

    def _after(self) -> None:
        self.btn.setEnabled(True)
        if not self.ctx.public_url:
            self._chip("Parado", "ChipBad")
        self._render()

    def _fail(self, msg: str) -> None:
        self.btn.setEnabled(True)
        self._chip("Erro", "ChipBad")
        self.log.appendPlainText("ERRO: " + msg)
        self._render()

    def _on_event(self, ev: Event) -> None:
        if ev.type == EventType.TUNNEL_LOG:
            self.log.appendPlainText(str(ev.data.get("line", "")))
        elif ev.type == EventType.TUNNEL_STATUS:
            st = ev.data.get("status", "")
            if st == "stopped":
                self.ctx.public_url = ""
            self._render()
