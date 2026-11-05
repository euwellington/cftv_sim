"""Passo 4: acesso (rede local ou modo publico pela internet, com tunel e avisos de seguranca)."""
from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QFrame, QLabel, QLineEdit, QRadioButton, QVBoxLayout,
                             QWizardPage)

from core.models import PublicAccess

WARNINGS = (
    "<b>Avisos de seguranca do modo publico</b><ul>"
    "<li>O portal fica acessivel por uma URL <b>HTTPS</b> na internet. Use senha forte e unica.</li>"
    "<li>Apos 5 tentativas erradas o IP e o usuario sao bloqueados (rate limit).</li>"
    "<li>O video externo vai por <b>WebRTC/HLS</b> pelo portal. <b>RTSP cru nao passa por tunel HTTP</b> "
    "(so pelo tunel TCP do ngrok).</li>"
    "<li>WebRTC por tunel pode precisar de TURN/ICE-TCP; o HLS e o fallback garantido.</li>"
    "<li>As APIs ISAPI/CGI <b>nao</b> sao expostas, a menos que voce marque abaixo.</li>"
    "<li>Todo acesso externo e registrado em log. Encerre o tunel quando terminar de usar.</li></ul>"
)


class AccessPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Acesso")
        self.setSubTitle("Quem pode abrir o portal e as APIs?")
        lay = QVBoxLayout(self)
        self.r_local = QRadioButton("Somente rede local")
        self.r_public = QRadioButton("Modo publico (internet) via tunel")
        self.r_local.setChecked(True)
        lay.addWidget(self.r_local)
        lay.addWidget(self.r_public)

        self.box = QFrame()
        self.box.setObjectName("Panel")
        form = QFormLayout(self.box)
        self.provider = QComboBox()
        self.provider.addItem("Cloudflare Tunnel - quick tunnel (sem conta) [padrao]", "cloudflare")
        self.provider.addItem("ngrok (requer authtoken)", "ngrok")
        self.provider.addItem("Tailscale Funnel", "tailscale")
        self.provider.addItem("TCP publico sem conta - bore/pinggy (RTSP/HTTP de fora)", "bore")
        self.token = QLineEdit()
        self.token.setPlaceholderText("authtoken do ngrok")
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.tcp = QCheckBox("Tunel TCP do ngrok para RTSP (ngrok tcp 554)")
        self.expose = QCheckBox("Expor tambem as APIs ISAPI/CGI das marcas pela internet (nao recomendado)")
        self.ack = QCheckBox("Entendo os riscos de expor equipamentos de video na internet")
        form.addRow("Tunel", self.provider)
        form.addRow("Authtoken", self.token)
        form.addRow("", self.tcp)
        form.addRow("", self.expose)
        warn = QLabel(WARNINGS)
        warn.setWordWrap(True)
        warn.setTextFormat(warn.textFormat().RichText)
        form.addRow(warn)
        form.addRow("", self.ack)
        lay.addWidget(self.box)
        lay.addStretch(1)
        self.box.setEnabled(False)
        self.r_public.toggled.connect(self._toggle)
        self.provider.currentIndexChanged.connect(self._toggle)
        for w in (self.ack,):
            w.toggled.connect(lambda _=None: self.completeChanged.emit())
        self.token.textChanged.connect(lambda _=None: self.completeChanged.emit())
        self._toggle()

    def _toggle(self) -> None:
        pub = self.r_public.isChecked()
        self.box.setEnabled(pub)
        ng = self.provider.currentData() == "ngrok"
        self.token.setEnabled(pub and ng)
        self.tcp.setEnabled(pub and ng)
        self.completeChanged.emit()

    def isComplete(self) -> bool:  # noqa: N802
        if not self.r_public.isChecked():
            return True
        if self.provider.currentData() == "ngrok" and not self.token.text().strip():
            return False
        return self.ack.isChecked()

    def validatePage(self) -> bool:  # noqa: N802
        pub = self.r_public.isChecked()
        self.wizard().data["public"] = PublicAccess(  # type: ignore[attr-defined]
            enabled=pub, provider=self.provider.currentData(),
            ngrok_authtoken=self.token.text().strip() if pub else "",
            expose_brand_apis=self.expose.isChecked() if pub else False,
            ngrok_tcp_rtsp=self.tcp.isChecked() if pub else False)
        return True
