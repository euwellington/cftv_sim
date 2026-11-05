"""Passo 5: resumo, nome do perfil e botao Iniciar."""
from __future__ import annotations

from PyQt6.QtWidgets import QFormLayout, QLabel, QLineEdit, QTextBrowser, QVBoxLayout, QWizardPage

from core.device_manager import ProfileError, build_profile, list_profiles
from core.fmt import primary_ip


class SummaryPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Resumo")
        self.setSubTitle("Confira e clique em Iniciar. O perfil e salvo e pode ser reaberto depois.")
        lay = QVBoxLayout(self)
        self.view = QTextBrowser()
        self.view.setOpenExternalLinks(False)
        lay.addWidget(self.view, 1)
        form = QFormLayout()
        self.name = QLineEdit("padrao")
        form.addRow("Nome do perfil", self.name)
        lay.addLayout(form)
        self.err = QLabel("")
        self.err.setObjectName("Bad")
        self.err.setWordWrap(True)
        lay.addWidget(self.err)
        self.profile = None
        self.name.textChanged.connect(lambda _=None: self.completeChanged.emit())

    def initializePage(self) -> None:  # noqa: N802
        d = self.wizard().data  # type: ignore[attr-defined]
        existing = set(list_profiles())
        base, n = "padrao", 1
        while base in existing:
            n += 1
            base = f"padrao{n}"
        self.name.setText(base)
        try:
            self.profile = build_profile(name="preview", **{k: d[k] for k in (
                "brand_choice", "mode", "camera_count", "hik_recorder", "intelbras_recorder", "bind_ip",
                "admin_user", "admin_password", "ports", "public", "legacy_firmware", "allow_basic",
                "onvif_enabled", "discovery_enabled", "web_port")})
            self.err.setText("")
        except ProfileError as exc:
            self.profile = None
            self.err.setText(str(exc))
        self.view.setHtml(self._html())

    def _html(self) -> str:
        p = self.profile
        if not p:
            return "<p>Nao foi possivel montar o perfil.</p>"
        host = primary_ip() if p.devices[0].bind_ip == "0.0.0.0" else p.devices[0].bind_ip
        rows, fw = [], [p.web_port]
        for dev in p.devices:
            fw += [dev.http_port, dev.rtsp_port]
            chans = ", ".join(c.name for c in dev.channels)
            if dev.brand.value == "hikvision":
                rtsp = f"rtsp://{host}:{dev.rtsp_port}/Streaming/Channels/101"
                api = f"http://{host}:{dev.http_port}/ISAPI/System/deviceInfo"
            else:
                rtsp = f"rtsp://{host}:{dev.rtsp_port}/cam/realmonitor?channel=1&amp;subtype=0"
                api = f"http://{host}:{dev.http_port}/cgi-bin/magicBox.cgi?action=getSystemInfo"
            rows.append(
                f"<h3>{dev.brand.value.capitalize()} - {dev.model} ({dev.kind.value.upper()})</h3>"
                f"<ul><li>Canais: {len(dev.channels)} ({chans})</li><li>Serial: {dev.serial}</li>"
                f"<li>HTTP/ONVIF: <b>{dev.http_port}</b> &nbsp; RTSP: <b>{dev.rtsp_port}</b></li>"
                f"<li>API: {api}</li><li>RTSP: {rtsp}</li></ul>")
        pub = p.public
        acesso = (f"Publico via <b>{pub.provider}</b> (APIs das marcas "
                  f"{'expostas' if pub.expose_brand_apis else 'NAO expostas'})") if pub.enabled else "Somente rede local"
        return (f"<h2>Perfil</h2><p>Usuario: <b>{self.wizard().data['admin_user']}</b> "  # type: ignore[attr-defined]
                f"(senha forte validada) &nbsp; Acesso: {acesso}</p>"
                f"<p>Portal web: <b>http://{host}:{p.web_port}</b></p>{''.join(rows)}"
                f"<h3>Firewall</h3><p>Libere (TCP): {', '.join(str(x) for x in sorted(set(fw)))}. "
                f"No Windows: Painel de Controle &gt; Firewall &gt; Regras de entrada.</p>")

    def isComplete(self) -> bool:  # noqa: N802
        return self.profile is not None and bool(self.name.text().strip())

    def validatePage(self) -> bool:  # noqa: N802
        d = self.wizard().data  # type: ignore[attr-defined]
        try:
            d["final_profile"] = build_profile(name=self.name.text().strip(), **{k: d[k] for k in (
                "brand_choice", "mode", "camera_count", "hik_recorder", "intelbras_recorder", "bind_ip",
                "admin_user", "admin_password", "ports", "public", "legacy_firmware", "allow_basic",
                "onvif_enabled", "discovery_enabled", "web_port")})
        except ProfileError as exc:
            self.err.setText(str(exc))
            return False
        d["_session_pw"] = d["admin_password"]  # so memoria; usada na barra de URLs
        d["admin_password"] = ""
        return True
