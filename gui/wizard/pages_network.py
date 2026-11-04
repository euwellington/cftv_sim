"""Passo 3: rede (IP, portas com deteccao de conflito) e seguranca (senha forte obrigatoria)."""
from __future__ import annotations

from PyQt6.QtWidgets import (QCheckBox, QComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel,
                             QLineEdit, QMessageBox, QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget,
                             QWizardPage)

from core.config import load_defaults
from core.fmt import local_ips
from core.ports import check_conflicts, suggest_port_plan
from core.security import generate_password
from gui.widgets.strength_meter import StrengthMeter


class NetworkPage(QWizardPage):
    def __init__(self) -> None:
        super().__init__()
        self.setTitle("Rede e seguranca")
        self.setSubTitle("IP de escuta, portas (com deteccao de portas ocupadas) e senha do administrador.")
        self._defaults = load_defaults()
        self._brands: list[str] = []
        self._spins: list[tuple[QSpinBox, QSpinBox]] = []

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)
        lay = QVBoxLayout(body)
        lay.setSpacing(12)

        # --- rede
        net = QGroupBox("Rede")
        nl = QVBoxLayout(net)
        r1 = QHBoxLayout()
        r1.addWidget(QLabel("IP de escuta:"))
        self.bind = QComboBox()
        self.bind.addItem("0.0.0.0  (todas as interfaces - acessivel na rede)", "0.0.0.0")
        self.bind.addItem("127.0.0.1  (somente este computador)", "127.0.0.1")
        for ip in local_ips():
            self.bind.addItem(f"{ip}  (interface de rede)", ip)
        self.bind.currentIndexChanged.connect(self._revalidate)
        r1.addWidget(self.bind, 1)
        self.suggest_btn = QPushButton("Sugerir portas")
        self.suggest_btn.clicked.connect(self._suggest)
        r1.addWidget(self.suggest_btn)
        nl.addLayout(r1)
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(10)
        nl.addLayout(self.grid)
        r2 = QHBoxLayout()
        r2.addWidget(QLabel("Porta do portal web:"))
        self.web_port = QSpinBox()
        self.web_port.setRange(1, 65535)
        self.web_port.setValue(self._defaults["ports"]["web_portal"])
        self.web_port.valueChanged.connect(self._revalidate)
        r2.addWidget(self.web_port)
        r2.addStretch(1)
        nl.addLayout(r2)
        self.port_msg = QLabel("")
        self.port_msg.setWordWrap(True)
        nl.addWidget(self.port_msg)
        lay.addWidget(net)

        # --- credenciais
        cred = QGroupBox("Administrador")
        form = QFormLayout(cred)
        self.user = QLineEdit("admin")
        self.user.textChanged.connect(self._on_pw)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw.textChanged.connect(self._on_pw)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw2.textChanged.connect(self._on_pw)
        self.meter = StrengthMeter()
        self.show_pw = QCheckBox("Mostrar senha")
        self.show_pw.toggled.connect(self._toggle_echo)
        gen = QPushButton("Gerar senha forte")
        gen.clicked.connect(self._generate)
        pr = QHBoxLayout()
        pr.addWidget(self.pw, 1)
        pr.addWidget(gen)
        form.addRow("Usuario", self.user)
        form.addRow("Senha", pr)
        form.addRow("Confirmar", self.pw2)
        form.addRow("", self.meter)
        form.addRow("", self.show_pw)
        self.match = QLabel("")
        form.addRow("", self.match)
        note = QLabel("Senhas padrao (admin, 12345, 123456...) sao bloqueadas. Minimo de 10 caracteres com 3 tipos de caractere.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        form.addRow("", note)
        lay.addWidget(cred)

        # --- avancado
        adv = QGroupBox("Avancado")
        adv.setCheckable(True)
        adv.setChecked(False)
        al = QVBoxLayout(adv)
        self.onvif = QCheckBox("ONVIF ligado (Profile S, WS-Discovery)")
        self.onvif.setChecked(True)
        self.discovery = QCheckBox("Descoberta proprietaria (SADP / DHIP) - aproximada, desligada por padrao")
        self.legacy = QCheckBox("Firmware legado Hikvision (aliases PSIA)")
        self.basic = QCheckBox("Aceitar HTTP Basic alem do Digest (modelos antigos)")
        for w in (self.onvif, self.discovery, self.legacy, self.basic):
            al.addWidget(w)
        adv.toggled.connect(lambda on: [w.setVisible(on) for w in (self.onvif, self.discovery, self.legacy, self.basic)])
        for w in (self.onvif, self.discovery, self.legacy, self.basic):
            w.setVisible(False)
        self.adv = adv
        lay.addWidget(adv)
        lay.addStretch(1)

    # ------------------------------------------------------------------ ciclo da pagina
    def initializePage(self) -> None:  # noqa: N802
        brand = self.wizard().data["brand_choice"]  # type: ignore[attr-defined]
        self._brands = ["hikvision", "intelbras"] if brand == "all" else [brand]
        self.legacy.setVisible(self.adv.isChecked() and "hikvision" in self._brands)
        self._build_port_rows()
        self._suggest()

    def _build_port_rows(self) -> None:
        while self.grid.count():
            w = self.grid.takeAt(0).widget()
            if w:
                w.deleteLater()
        self._spins.clear()
        for col, t in enumerate(("Equipamento", "HTTP / ONVIF", "RTSP")):
            lbl = QLabel(t)
            lbl.setObjectName("Muted")
            self.grid.addWidget(lbl, 0, col)
        for i, b in enumerate(self._brands, start=1):
            self.grid.addWidget(QLabel(b.capitalize()), i, 0)
            h, r = QSpinBox(), QSpinBox()
            for s in (h, r):
                s.setRange(1, 65535)
                s.valueChanged.connect(self._revalidate)
            self.grid.addWidget(h, i, 1)
            self.grid.addWidget(r, i, 2)
            self._spins.append((h, r))

    def _suggest(self) -> None:
        d = self._defaults["ports"]
        plan = suggest_port_plan(self._brands, host=self.bind.currentData(), prefer_standard=d["prefer_standard"],
                                 hik_http_start=d["hikvision_http_start"], intelbras_http_start=d["intelbras_http_start"],
                                 rtsp_start=d["rtsp_start"], reserved=[self.web_port.value()])
        for (h, r), ps in zip(self._spins, plan):
            h.blockSignals(True)
            r.blockSignals(True)
            h.setValue(ps.http)
            r.setValue(ps.rtsp)
            h.blockSignals(False)
            r.blockSignals(False)
        self._revalidate()

    # ------------------------------------------------------------------ validacao
    def _all_ports(self) -> list[int]:
        ports = [self.web_port.value()]
        for h, r in self._spins:
            ports += [h.value(), r.value()]
        return ports

    def _revalidate(self) -> None:
        bad = check_conflicts(self._all_ports(), self.bind.currentData() or "0.0.0.0")
        if bad:
            self.port_msg.setObjectName("Bad")
            self.port_msg.setText("\n".join(f"Porta {p}: {why}" for p, why in bad.items()))
        else:
            self.port_msg.setObjectName("Ok")
            self.port_msg.setText("Todas as portas estao livres.")
        self.port_msg.style().unpolish(self.port_msg)
        self.port_msg.style().polish(self.port_msg)
        self._port_problems = bad
        self.completeChanged.emit()

    def _on_pw(self) -> None:
        self.meter.update_for(self.pw.text(), self.user.text())
        same = self.pw.text() == self.pw2.text()
        self.match.setObjectName("Ok" if same and self.pw2.text() else "Bad")
        self.match.setText("" if not self.pw2.text() else "As senhas conferem." if same else "As senhas nao conferem.")
        self.match.style().unpolish(self.match)
        self.match.style().polish(self.match)
        self.completeChanged.emit()

    def _toggle_echo(self, on: bool) -> None:
        mode = QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password
        self.pw.setEchoMode(mode)
        self.pw2.setEchoMode(mode)

    def _generate(self) -> None:
        p = generate_password(14)
        self.pw.setText(p)
        self.pw2.setText(p)
        self.show_pw.setChecked(True)

    def isComplete(self) -> bool:  # noqa: N802
        rep = self.meter.report
        return bool(self.user.text().strip() and rep and rep.ok and self.pw.text() == self.pw2.text()
                    and not getattr(self, "_port_problems", {}))

    def validatePage(self) -> bool:  # noqa: N802
        if not self.isComplete():
            QMessageBox.warning(self, "Revise os dados", "Corrija as portas e use uma senha forte e confirmada.")
            return False
        from core.ports import PortSet
        d = self.wizard().data  # type: ignore[attr-defined]
        d["bind_ip"] = self.bind.currentData()
        d["ports"] = [PortSet(http=h.value(), rtsp=r.value(), sdk=8000 if b == "hikvision" else 37777)
                      for (h, r), b in zip(self._spins, self._brands)]
        d["web_port"] = self.web_port.value()
        d["admin_user"] = self.user.text().strip()
        d["admin_password"] = self.pw.text()
        d["onvif_enabled"] = self.onvif.isChecked() if self.adv.isChecked() else True
        d["discovery_enabled"] = self.discovery.isChecked() and self.adv.isChecked()
        d["legacy_firmware"] = self.legacy.isChecked() and self.adv.isChecked()
        d["allow_basic"] = self.basic.isChecked() and self.adv.isChecked()
        return True
