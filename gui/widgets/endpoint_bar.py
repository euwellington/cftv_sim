"""Barra fixa com as URLs HTTP e RTSP reais (usuario e senha) prontas para copiar.

Mostra host, IP local e IP publico; o seletor "Endereco" troca o host usado nas URLs
(IP local, hostname ou IP publico; o publico exige redirecionamento de portas no roteador).
"""
from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QCheckBox, QComboBox, QFrame, QGridLayout, QLabel, QLineEdit, QPushButton

from core import urls
from core.fmt import fetch_public_ip, hostname, tailscale_ip
from gui.workers import run_async


class EndpointBar(QFrame):
    def __init__(self, ctx) -> None:  # noqa: ANN001
        super().__init__()
        self.ctx = ctx
        self.setObjectName("Header")
        g = QGridLayout(self)
        g.setContentsMargins(20, 6, 20, 6)
        g.setHorizontalSpacing(8)
        g.setVerticalSpacing(4)
        self.combo = QComboBox()
        self.combo.currentIndexChanged.connect(self._fill)
        self.addr = QComboBox()
        self.addr.addItems(["IP local", "Hostname", "IP publico", "Tunel TCP (ngrok/bore)", "IP Tailscale"])
        self.addr.currentIndexChanged.connect(self._addr_changed)
        self.btn_pub = QPushButton("Obter IP publico")
        self.btn_pub.clicked.connect(self._get_public)
        self.btn_upnp = QPushButton("Abrir portas no roteador (UPnP)")
        self.btn_upnp.clicked.connect(self._upnp)
        self.info = QLabel("")
        self.info.setObjectName("Muted")
        self.info.setTextInteractionFlags(self.info.textInteractionFlags() | self.info.textInteractionFlags().TextSelectableByMouse)
        self.creds = QLabel("")
        self.creds.setObjectName("Muted")
        g.addWidget(QLabel("Equipamento:"), 0, 0)
        g.addWidget(self.combo, 0, 1)
        g.addWidget(QLabel("Endereco:"), 0, 2)
        g.addWidget(self.addr, 0, 3)
        g.addWidget(self.btn_pub, 0, 4)
        g.addWidget(self.btn_upnp, 0, 5, 1, 2)
        self.btn_pw = QPushButton("Informar senha")
        self.btn_pw.clicked.connect(self._ask_pw)
        g.addWidget(self.creds, 1, 1, 1, 3)
        g.addWidget(self.btn_pw, 1, 4)
        g.addWidget(self.info, 2, 0, 1, 5)
        self.http, self.rtsp, self.portal = self._line(), self._line(), self._line()
        self.chk_auth = QCheckBox("RTSP exige autenticacao (usuario e senha)")
        self.chk_auth.setChecked(self.ctx.profile.rtsp_auth)
        self.chk_auth.toggled.connect(self._auth_toggled)
        rows_def = (("HTTP", self.http, 3), ("RTSP", self.rtsp, 4), ("Portal", self.portal, 6))
        for lbl, w, i in rows_def:
            g.addWidget(QLabel(lbl + ":"), i, 0)
            g.addWidget(w, i, 1, 1, 3)
            b = QPushButton("Copiar")
            b.setFixedWidth(80)
            b.clicked.connect(lambda _=False, ww=w, bb=b: self._copy(ww, bb))
            g.addWidget(b, i, 4)
            if lbl == "RTSP":
                ob = QPushButton("Abrir no VLC")
                ob.clicked.connect(self._open_player)
                cb = QPushButton("Copiar comando")
                cb.clicked.connect(lambda _=False, bb=cb: self._copy_cmd(bb))
                g.addWidget(ob, i, 5)
                g.addWidget(cb, i, 6)
        g.addWidget(self.chk_auth, 5, 1, 1, 3)  # logo abaixo da linha RTSP
        g.setColumnStretch(3, 1)
        g.setColumnStretch(5, 0)
        self._ts_ip = ""
        self._tcp_auto = False
        self._reload_rows()
        self._default_addr()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._fill)
        self._timer.start(5000)

    @staticmethod
    def _line() -> QLineEdit:
        e = QLineEdit()
        e.setReadOnly(True)
        e.setStyleSheet("font-family: Consolas, 'DejaVu Sans Mono', monospace;")
        return e

    def _reload_rows(self) -> None:
        cur = self.combo.currentIndex()
        self.rows = self.ctx.access_rows()
        self.combo.blockSignals(True)
        self.combo.clear()
        self.combo.addItems([r["label"] for r in self.rows])
        self.combo.setCurrentIndex(max(0, min(cur, len(self.rows) - 1)))
        self.combo.blockSignals(False)
        self._fill()

    def _default_addr(self) -> None:
        """Se o Tailscale estiver ativo, ele e o endereco padrao (funciona de fora sem abrir portas)."""
        def done(ip: str) -> None:
            self._ts_ip = ip
            if ip:
                self.addr.setCurrentIndex(4)
            self._fill()
        run_async(tailscale_ip, on_done=done, on_error=lambda _m: None)

    def _tcp(self) -> bool:
        return self.addr.currentIndex() == 3

    def _host(self) -> str:
        k = self.addr.currentIndex()
        if k == 1:
            return hostname()
        if k == 4:
            return self._ts_ip or self.ctx.host()
        if k == 2 and self.ctx.public_ip:
            return self.ctx.public_ip
        return self.ctx.host()

    def _addr_changed(self) -> None:
        if self.addr.currentIndex() == 2 and not self.ctx.public_ip:
            self._get_public()
        self._fill()

    def _get_public(self) -> None:
        self.btn_pub.setEnabled(False)
        self.btn_pub.setText("Consultando...")

        def done(ip: str) -> None:
            self.ctx.public_ip = ip
            self.btn_pub.setEnabled(True)
            self.btn_pub.setText("Atualizar IP publico")
            self._fill()

        def fail(msg: str) -> None:
            self.btn_pub.setEnabled(True)
            self.btn_pub.setText("Obter IP publico")
            self.info.setText("IP publico indisponivel: " + msg)

        run_async(fetch_public_ip, on_done=done, on_error=fail)

    def _upnp(self) -> None:
        from PyQt6.QtWidgets import QMessageBox
        from core import upnp
        from core.fmt import primary_ip
        ports = sorted({d.rtsp_port for d in self.ctx.profile.devices} | {d.http_port for d in self.ctx.profile.devices}
                       | {self.ctx.profile.web_port})
        ip = primary_ip()
        self.btn_upnp.setEnabled(False)
        self.btn_upnp.setText("Abrindo portas...")

        def done(r: dict) -> None:
            self.btn_upnp.setEnabled(True)
            self.btn_upnp.setText("Abrir portas no roteador (UPnP)")
            lines = [f"Portas abertas (TCP) para {ip}: {', '.join(map(str, r['opened'])) or 'nenhuma'}",
                     f"IP externo que o roteador enxerga: {r['external_ip'] or 'desconhecido'}"]
            if r["errors"]:
                lines.append("Erros: " + "; ".join(f"{p}: {e}" for p, e in r["errors"].items()))
            if r["cgnat"]:
                lines.append("\nATENCAO: esse IP e privado/CGNAT. Seu provedor nao entrega IP publico direto: "
                             "a porta NAO sera acessivel pela internet. Use Tailscale, ngrok TCP ou peca IP publico ao provedor.")
            elif r["opened"]:
                lines.append("\nTeste de FORA da sua rede (4G): vlc \"" + self.rtsp.text().replace(self.ctx.host(), r["external_ip"] or "IP_PUBLICO") + "\"")
                lines.append("Se o Linux tiver firewall: sudo ufw allow 8554/tcp")
            QMessageBox.information(self, "UPnP", "\n".join(lines))

        def fail(msg: str) -> None:
            self.btn_upnp.setEnabled(True)
            self.btn_upnp.setText("Abrir portas no roteador (UPnP)")
            QMessageBox.warning(self, "UPnP", f"{msg}\n\nAbra a porta manualmente no painel do roteador "
                                f"(Port Forwarding: TCP {', '.join(map(str, ports))} -> {ip}).")

        run_async(upnp.open_ports, ports, ip, on_done=done, on_error=fail)

    def _auth_toggled(self, on: bool) -> None:
        self.ctx.profile.rtsp_auth = on  # vale na hora: o RtspRouter consulta o perfil a cada conexao
        try:
            self.ctx.db.add_log("operation", f"RTSP autenticacao {'ligada' if on else 'desligada'}", "", "GUI")
        except Exception:  # noqa: BLE001
            pass
        try:
            self.ctx.save()
        except Exception:  # noqa: BLE001
            pass
        self._fill()

    def _fill(self) -> None:
        if self.chk_auth.isChecked() != self.ctx.profile.rtsp_auth:
            self.chk_auth.blockSignals(True)
            self.chk_auth.setChecked(self.ctx.profile.rtsp_auth)
            self.chk_auth.blockSignals(False)
        if self.ctx.tcp_endpoints and not self._tcp_auto:  # tunel TCP subiu: passa a mostrar o IP/portas dele
            self._tcp_auto = True
            self.addr.blockSignals(True)
            self.addr.setCurrentIndex(3)
            self.addr.blockSignals(False)
        elif not self.ctx.tcp_endpoints:
            self._tcp_auto = False
        rows = self.ctx.access_rows(self._host(), ngrok_tcp=self._tcp())
        i = max(0, self.combo.currentIndex())
        if i >= len(rows):
            return
        r = rows[i]
        self.http.setText(r["http"])
        self.rtsp.setText(r["rtsp"])
        pw = self.ctx.admin_password or "<senha>"
        self.portal.setText(self.ctx.public_url or urls.portal_url(self._host(), self.ctx.profile.web_port,
                                                                    self.ctx.admin_user, pw))
        self.btn_pw.setVisible(not self.ctx.admin_password)
        shown_pw = self.ctx.admin_password or "(senha nao informada)"
        if self._tcp() and not self.ctx.tcp_endpoints:
            self.info.setText("Tunel TCP inativo: em Acesso remoto escolha 'bore' (sem conta) e inicie o tunel.")
            return
        self.creds.setText(f"Usuario: {self.ctx.admin_user}   Senha: {shown_pw}")
        pub = self.ctx.public_ip or "nao consultado"
        tun = f"   Tunel: {self.ctx.public_url}" if self.ctx.public_url else ""
        ts = f"   Tailscale: {self._ts_ip}" if self._ts_ip else ""
        self.info.setText(f"Host: {hostname()}   IP local: {self.ctx.host()}   IP publico: {pub}{ts}{tun}")

    def _cmd(self) -> list[str] | None:
        url = self.rtsp.text()
        if "%3Csenha%3E" in url or "<senha>" in url:
            return None
        import shutil
        for exe, extra in (("vlc", []), ("ffplay", ["-rtsp_transport", "tcp", "-fflags", "nobuffer"]),
                           ("mpv", ["--rtsp-transport=tcp"])):
            path = shutil.which(exe)
            if path:
                return [path, *extra, url]
        return []

    def _open_player(self) -> None:
        import subprocess
        from PyQt6.QtWidgets import QMessageBox
        cmd = self._cmd()
        if cmd is None:
            QMessageBox.information(self, "Senha", "Informe a senha primeiro ('Informar senha').")
        elif not cmd:
            QMessageBox.information(self, "Player", "VLC/ffplay/mpv nao encontrados no PATH. Instale o VLC (sudo apt install vlc) "
                                    "ou use 'Copiar comando'.")
        else:
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    def _copy_cmd(self, b: QPushButton) -> None:
        url = self.rtsp.text()
        if "%3Csenha%3E" in url or "<senha>" in url:
            return self._open_player()
        QGuiApplication.clipboard().setText(f'vlc "{url}"')
        b.setText("Copiado!")
        QTimer.singleShot(1200, lambda: b.setText("Copiar comando"))

    def _ask_pw(self) -> None:
        from PyQt6.QtWidgets import QInputDialog, QLineEdit, QMessageBox
        from core.security import UserStore
        dev = self.ctx.profile.devices[0]
        store = UserStore(dev.users, realms=[dev.realm])
        pw, ok = QInputDialog.getText(self, "Senha", f"Senha de '{self.ctx.admin_user}' (so memoria):",
                                      QLineEdit.EchoMode.Password)
        if not ok or not pw:
            return
        if store.verify(self.ctx.admin_user, pw):
            from core import credentials
            credentials.save(self.ctx.profile.name, pw)
            self.ctx.admin_password = pw
            self._fill()
        else:
            QMessageBox.warning(self, "Senha", "Senha incorreta.")

    def _copy(self, w: QLineEdit, b: QPushButton) -> None:
        if "%3Csenha%3E" in w.text() or "<senha>" in w.text():
            from PyQt6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Senha", "A senha ainda nao foi informada. Clique em 'Informar senha' primeiro.")
            return
        QGuiApplication.clipboard().setText(w.text())
        b.setText("Copiado!")
        QTimer.singleShot(1200, lambda: b.setText("Copiar"))
