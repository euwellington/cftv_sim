"""Configuracoes: Rede, Usuarios, Video por canal, Movimento, Gravacao e Tunel."""
from __future__ import annotations

from typing import Optional

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFormLayout,
                             QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton, QSlider,
                             QSpinBox, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from core.event_bus import EventType
from core.fmt import local_ips
from core.models import Brand, DeviceSpec
from core.ports import check_conflicts
from gui.pages.base import BasePage
from gui.widgets.region_editor import MotionRegionEditor
from gui.widgets.strength_meter import StrengthMeter

RESOLUTIONS = ["1920x1080", "1280x720", "960x540", "704x576", "704x480", "640x360", "352x288"]
DAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sab", "Dom"]


class Picker(QWidget):
    """Seletor de equipamento (e, opcionalmente, canal) reutilizado pelas abas."""

    def __init__(self, ctx, with_channel: bool, on_change) -> None:  # noqa: ANN001
        super().__init__()
        self.ctx = ctx
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.dev = QComboBox()
        for d in ctx.profile.devices:
            self.dev.addItem(f"{d.brand.value.capitalize()} - {d.model}", d.id)
        lay.addWidget(QLabel("Equipamento:"))
        lay.addWidget(self.dev)
        self.chan: Optional[QComboBox] = None
        if with_channel:
            self.chan = QComboBox()
            lay.addWidget(QLabel("Canal:"))
            lay.addWidget(self.chan)
            self.dev.currentIndexChanged.connect(self._fill_channels)
            self._fill_channels()
            self.chan.currentIndexChanged.connect(lambda _i: on_change())
        lay.addStretch(1)
        self.dev.currentIndexChanged.connect(lambda _i: on_change())

    def _fill_channels(self) -> None:
        assert self.chan is not None
        self.chan.blockSignals(True)
        self.chan.clear()
        for c in self.spec().channels:
            self.chan.addItem(f"{c.index} - {c.name}", c.index)
        self.chan.blockSignals(False)

    def spec(self) -> DeviceSpec:
        return self.ctx.profile.device(self.dev.currentData())

    def channel_index(self) -> int:
        return int(self.chan.currentData()) if self.chan and self.chan.currentData() else 1


class PasswordDialog(QDialog):
    """Cria usuario ou troca senha, validando a politica (senha padrao bloqueada)."""

    def __init__(self, parent, title: str, ask_user: bool, username: str = "") -> None:  # noqa: ANN001
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(420)
        form = QFormLayout(self)
        self.user = QLineEdit(username)
        self.user.setEnabled(ask_user)
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw2 = QLineEdit()
        self.pw2.setEchoMode(QLineEdit.EchoMode.Password)
        self.role = QComboBox()
        for r, t in (("viewer", "Visualizador"), ("operator", "Operador"), ("admin", "Administrador")):
            self.role.addItem(t, r)
        self.meter = StrengthMeter()
        self.pw.textChanged.connect(lambda t: self.meter.update_for(t, self.user.text()))
        form.addRow("Usuario", self.user)
        if ask_user:
            form.addRow("Perfil", self.role)
        form.addRow("Senha", self.pw)
        form.addRow("Confirmar", self.pw2)
        form.addRow("", self.meter)
        bb = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        form.addRow(bb)

    def _ok(self) -> None:
        rep = self.meter.update_for(self.pw.text(), self.user.text())
        if not self.user.text().strip():
            QMessageBox.warning(self, "Usuario", "Informe o usuario.")
        elif not rep.ok:
            QMessageBox.warning(self, "Senha fraca", "\n".join(rep.issues))
        elif self.pw.text() != self.pw2.text():
            QMessageBox.warning(self, "Senha", "As senhas nao conferem.")
        else:
            self.accept()


class SettingsPage(BasePage):
    title = "Configuracoes"
    subtitle = "Rede, usuarios, video, movimento, gravacao e tunel"

    def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
        super().__init__(*a, **kw)
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        self.msg = QLabel("")
        self.msg.setObjectName("Muted")
        self.msg.setWordWrap(True)
        self.root.addWidget(self.msg)
        self._build_network()
        self._build_users()
        self._build_video()
        self._build_motion()
        self._build_recording()
        self._build_tunnel()

    # ------------------------------------------------------------------ util
    def _tab(self, name: str) -> QVBoxLayout:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(10)
        self.tabs.addTab(w, name)
        return lay

    def _saved(self, text: str) -> None:
        self.ctx.save()
        self.msg.setText(text)

    # ------------------------------------------------------------------ rede
    def _build_network(self) -> None:
        lay = self._tab("Rede")
        self.net_pick = Picker(self.ctx, False, self._load_network)
        lay.addWidget(self.net_pick)
        form = QFormLayout()
        self.n_bind = QComboBox()
        self.n_bind.setEditable(True)
        for ip in ["0.0.0.0", "127.0.0.1"] + local_ips():
            self.n_bind.addItem(ip)
        self.n_http, self.n_rtsp = QSpinBox(), QSpinBox()
        for s in (self.n_http, self.n_rtsp):
            s.setRange(1, 65535)
        self.n_onvif = QCheckBox("ONVIF ligado")
        self.n_disc = QCheckBox("Descoberta ligada (SADP / DHIP / WS-Discovery)")
        self.n_basic = QCheckBox("Aceitar HTTP Basic alem do Digest")
        self.n_legacy = QCheckBox("Firmware legado (aliases PSIA, Hikvision)")
        form.addRow("IP de escuta", self.n_bind)
        form.addRow("Porta HTTP / ONVIF", self.n_http)
        form.addRow("Porta RTSP", self.n_rtsp)
        for w in (self.n_onvif, self.n_disc, self.n_basic, self.n_legacy):
            form.addRow("", w)
        lay.addLayout(form)
        btn = QPushButton("Salvar rede")
        btn.setProperty("primary", True)
        btn.clicked.connect(self._save_network)
        lay.addWidget(btn)
        note = QLabel("Alterar IP/portas exige reiniciar o aplicativo (as portas sao abertas na inicializacao). "
                      "ONVIF e descoberta tambem valem no proximo inicio.")
        note.setObjectName("Muted")
        note.setWordWrap(True)
        lay.addWidget(note)
        lay.addStretch(1)
        self._load_network()

    def _load_network(self) -> None:
        s = self.net_pick.spec()
        self.n_bind.setCurrentText(s.bind_ip)
        self.n_http.setValue(s.http_port)
        self.n_rtsp.setValue(s.rtsp_port)
        self.n_onvif.setChecked(s.onvif_enabled)
        self.n_disc.setChecked(s.discovery_enabled)
        self.n_basic.setChecked(s.allow_basic)
        self.n_legacy.setChecked(s.legacy_firmware)
        self.n_legacy.setEnabled(s.brand is Brand.HIKVISION)

    def _save_network(self) -> None:
        s = self.net_pick.spec()
        others = [p for d in self.ctx.profile.devices if d.id != s.id for p in (d.http_port, d.rtsp_port)]
        others.append(self.ctx.profile.web_port)
        new = [self.n_http.value(), self.n_rtsp.value()]
        if set(new) & set(others) or new[0] == new[1]:
            QMessageBox.warning(self, "Portas", "Portas repetidas entre equipamentos/portal.")
            return
        changed = [p for p, old in zip(new, (s.http_port, s.rtsp_port)) if p != old]
        bad = check_conflicts(changed, self.n_bind.currentText() or "0.0.0.0")
        if bad:
            QMessageBox.warning(self, "Portas", "\n".join(f"{p}: {w}" for p, w in bad.items()))
            return
        s.bind_ip, s.http_port, s.rtsp_port = self.n_bind.currentText(), new[0], new[1]
        s.onvif_enabled, s.discovery_enabled = self.n_onvif.isChecked(), self.n_disc.isChecked()
        s.allow_basic = self.n_basic.isChecked()
        s.legacy_firmware = self.n_legacy.isChecked() and s.brand is Brand.HIKVISION
        self.ctx.db.add_log("operation", "Configuracao de rede alterada", s.id, "GUI")
        self._saved("Rede salva. Reinicie o aplicativo para aplicar IP e portas.")

    # ------------------------------------------------------------------ usuarios
    def _build_users(self) -> None:
        lay = self._tab("Usuarios")
        self.u_pick = Picker(self.ctx, False, self._load_users)
        lay.addWidget(self.u_pick)
        self.u_table = QTableWidget(0, 4)
        self.u_table.setHorizontalHeaderLabels(["Usuario", "Perfil", "Ativo", "Trocar senha no 1o acesso"])
        self.u_table.verticalHeader().setVisible(False)
        self.u_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.u_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.u_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.u_table, 1)
        row = QHBoxLayout()
        for text, fn, prop in (("Adicionar", self._add_user, "primary"), ("Trocar senha", self._change_pw, None),
                               ("Ativar/Desativar", self._toggle_user, None), ("Perfil...", self._cycle_role, None),
                               ("Excluir", self._del_user, "danger")):
            b = QPushButton(text)
            if prop:
                b.setProperty(prop, True)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)
        self._load_users()

    def _store(self):  # noqa: ANN202
        return self.ctx.manager.get(self.u_pick.spec().id).users

    def _load_users(self) -> None:
        users = self.u_pick.spec().users
        self.u_table.setRowCount(len(users))
        names = {"admin": "Administrador", "operator": "Operador", "viewer": "Visualizador"}
        for i, u in enumerate(users):
            for j, v in enumerate((u.username, names[u.role], "sim" if u.enabled else "nao",
                                   "sim" if u.must_change_password else "nao")):
                self.u_table.setItem(i, j, QTableWidgetItem(v))

    def _selected_user(self) -> Optional[str]:
        r = self.u_table.currentRow()
        return self.u_table.item(r, 0).text() if r >= 0 else None

    def _add_user(self) -> None:
        dlg = PasswordDialog(self, "Novo usuario", True)
        if dlg.exec():
            try:
                self._store().set_password(dlg.user.text().strip(), dlg.pw.text(), role=dlg.role.currentData())
            except ValueError as exc:
                QMessageBox.warning(self, "Usuario", str(exc))
                return
            self.ctx.db.add_log("operation", f"Usuario {dlg.user.text()} criado", self.u_pick.spec().id, "GUI")
            self._load_users()
            self._saved("Usuario criado.")

    def _change_pw(self) -> None:
        u = self._selected_user()
        if not u:
            return
        dlg = PasswordDialog(self, f"Trocar senha de {u}", False, u)
        if dlg.exec():
            self._store().set_password(u, dlg.pw.text())
            self.ctx.db.add_log("operation", f"Senha de {u} alterada", self.u_pick.spec().id, "GUI")
            self._load_users()
            self._saved("Senha alterada.")

    def _toggle_user(self) -> None:
        u = self._selected_user()
        rec = self._store().get(u) if u else None
        if rec:
            admins = [x for x in self.u_pick.spec().users if x.role == "admin" and x.enabled]
            if rec.enabled and rec.role == "admin" and len(admins) <= 1:
                QMessageBox.warning(self, "Usuario", "Mantenha ao menos um administrador ativo.")
                return
            rec.enabled = not rec.enabled
            self._load_users()
            self._saved("Usuario atualizado.")

    def _cycle_role(self) -> None:
        u = self._selected_user()
        rec = self._store().get(u) if u else None
        if rec:
            order = ["viewer", "operator", "admin"]
            new = order[(order.index(rec.role) + 1) % 3]
            if rec.role == "admin" and sum(x.role == "admin" for x in self.u_pick.spec().users) <= 1:
                QMessageBox.warning(self, "Usuario", "Mantenha ao menos um administrador.")
                return
            rec.role = new  # type: ignore[assignment]
            self._load_users()
            self._saved(f"Perfil de {u}: {new}.")

    def _del_user(self) -> None:
        u = self._selected_user()
        rec = self._store().get(u) if u else None
        if not rec:
            return
        if rec.role == "admin" and sum(x.role == "admin" for x in self.u_pick.spec().users) <= 1:
            QMessageBox.warning(self, "Usuario", "Nao e possivel excluir o ultimo administrador.")
            return
        if QMessageBox.question(self, "Excluir", f"Excluir {u}?") == QMessageBox.StandardButton.Yes:
            self._store().delete(u)
            self._load_users()
            self._saved("Usuario excluido.")

    # ------------------------------------------------------------------ video
    def _build_video(self) -> None:
        lay = self._tab("Video")
        self.v_pick = Picker(self.ctx, True, self._load_video)
        lay.addWidget(self.v_pick)
        form = QFormLayout()
        self.v_name = QLineEdit()
        self.v_osd = QCheckBox("Exibir OSD (data/hora e nome do canal)")
        self.v_stream = QComboBox()
        self.v_stream.addItem("Stream principal", "main")
        self.v_stream.addItem("Stream extra (sub)", "sub")
        self.v_stream.currentIndexChanged.connect(self._load_video)
        self.v_res = QComboBox()
        self.v_res.addItems(RESOLUTIONS)
        self.v_fps = QSpinBox()
        self.v_fps.setRange(1, 30)
        self.v_br = QSpinBox()
        self.v_br.setRange(64, 16384)
        self.v_br.setSuffix(" kbps")
        self.v_codec = QComboBox()
        self.v_codec.addItems(["H.264", "H.265", "MJPEG"])
        self.v_brc = QComboBox()
        self.v_brc.addItems(["CBR", "VBR"])
        form.addRow("Nome do canal", self.v_name)
        form.addRow("", self.v_osd)
        form.addRow("Stream", self.v_stream)
        form.addRow("Resolucao", self.v_res)
        form.addRow("FPS", self.v_fps)
        form.addRow("Bitrate", self.v_br)
        form.addRow("Codec", self.v_codec)
        form.addRow("Controle de bitrate", self.v_brc)
        lay.addLayout(form)
        btn = QPushButton("Aplicar")
        btn.setProperty("primary", True)
        btn.clicked.connect(self._save_video)
        lay.addWidget(btn)
        note = QLabel("Resolucao e FPS valem na hora para o video da GUI; os publishers RTSP reiniciam o stream sozinhos.")
        note.setObjectName("Muted")
        lay.addWidget(note)
        lay.addStretch(1)
        self._load_video()

    def _load_video(self) -> None:
        ch = self.v_pick.spec().channel(self.v_pick.channel_index())
        v = getattr(ch, self.v_stream.currentData())
        self.v_name.setText(ch.name)
        self.v_osd.setChecked(ch.osd_enabled)
        if self.v_res.findText(v.resolution) < 0:
            self.v_res.addItem(v.resolution)
        self.v_res.setCurrentText(v.resolution)
        self.v_fps.setValue(v.fps)
        self.v_br.setValue(v.bitrate_kbps)
        self.v_codec.setCurrentText(v.codec)
        self.v_brc.setCurrentText(v.bitrate_control)

    def _save_video(self) -> None:
        spec = self.v_pick.spec()
        ch = spec.channel(self.v_pick.channel_index())
        v = getattr(ch, self.v_stream.currentData())
        w, h = (int(x) for x in self.v_res.currentText().split("x"))
        v.width, v.height, v.fps = w, h, self.v_fps.value()
        v.bitrate_kbps, v.codec = self.v_br.value(), self.v_codec.currentText()
        v.bitrate_control = self.v_brc.currentText()  # type: ignore[assignment]
        ch.name, ch.osd_enabled = self.v_name.text().strip() or ch.name, self.v_osd.isChecked()
        self.ctx.manager.camera(spec.id, ch.index).apply_spec(ch)
        self.ctx.bus.publish(EventType.CONFIG_CHANGED, spec.id, ch.index, section="video")
        self.ctx.db.add_log("operation", f"Video do canal {ch.index} alterado", spec.id, "GUI")
        self._saved("Configuracao de video aplicada.")

    # ------------------------------------------------------------------ movimento
    def _build_motion(self) -> None:
        lay = self._tab("Movimento")
        self.m_pick = Picker(self.ctx, True, self._load_motion)
        lay.addWidget(self.m_pick)
        row = QHBoxLayout()
        self.m_enabled = QCheckBox("Deteccao ligada")
        self.m_sens = QSlider(Qt.Orientation.Horizontal)
        self.m_sens.setRange(0, 100)
        self.m_val = QLabel("60")
        self.m_sens.valueChanged.connect(lambda v: self.m_val.setText(str(v)))
        row.addWidget(self.m_enabled)
        row.addWidget(QLabel("Sensibilidade"))
        row.addWidget(self.m_sens, 1)
        row.addWidget(self.m_val)
        lay.addLayout(row)
        self.m_editor = MotionRegionEditor()
        lay.addWidget(self.m_editor, 1)
        row2 = QHBoxLayout()
        clear = QPushButton("Limpar regioes")
        clear.clicked.connect(self.m_editor.clear)
        apply_ = QPushButton("Aplicar")
        apply_.setProperty("primary", True)
        apply_.clicked.connect(self._save_motion)
        row2.addWidget(clear)
        row2.addStretch(1)
        row2.addWidget(apply_)
        lay.addLayout(row2)
        self._load_motion()

    def _load_motion(self) -> None:
        spec = self.m_pick.spec()
        ch = spec.channel(self.m_pick.channel_index())
        self.m_enabled.setChecked(ch.motion.enabled)
        self.m_sens.setValue(ch.motion.sensitivity)
        self.m_editor.set_regions(ch.motion.regions)
        self.m_editor.set_camera(self.ctx.manager.camera(spec.id, ch.index))

    def _save_motion(self) -> None:
        spec = self.m_pick.spec()
        ch = spec.channel(self.m_pick.channel_index())
        ch.motion.enabled = self.m_enabled.isChecked()
        ch.motion.sensitivity = self.m_sens.value()
        ch.motion.regions = self.m_editor.regions()
        self.ctx.manager.camera(spec.id, ch.index).apply_spec(ch)
        self.ctx.bus.publish(EventType.CONFIG_CHANGED, spec.id, ch.index, section="motion")
        self._saved("Deteccao de movimento aplicada.")

    # ------------------------------------------------------------------ gravacao
    def _build_recording(self) -> None:
        lay = self._tab("Gravacao")
        self.r_pick = Picker(self.ctx, False, self._load_recording)
        lay.addWidget(self.r_pick)
        form = QFormLayout()
        self.r_mode = QComboBox()
        for k, t in (("continuous", "Continua"), ("motion", "Por movimento (com pre-gravacao)"), ("off", "Desligada")):
            self.r_mode.addItem(t, k)
        self.r_pre, self.r_post, self.r_seg = QSpinBox(), QSpinBox(), QSpinBox()
        for s, lo, hi, suf in ((self.r_pre, 0, 60, " s"), (self.r_post, 0, 300, " s"), (self.r_seg, 10, 600, " s")):
            s.setRange(lo, hi)
            s.setSuffix(suf)
        self.r_over = QCheckBox("Overwrite (apagar as gravacoes mais antigas quando o disco encher)")
        form.addRow("Modo", self.r_mode)
        form.addRow("Pre-evento", self.r_pre)
        form.addRow("Pos-evento", self.r_post)
        form.addRow("Duracao do segmento", self.r_seg)
        form.addRow("", self.r_over)
        lay.addLayout(form)
        lay.addWidget(QLabel("Agenda (clique para alternar; azul = grava)"))
        self.r_sched = QTableWidget(7, 24)
        self.r_sched.setVerticalHeaderLabels(DAYS)
        self.r_sched.setHorizontalHeaderLabels([f"{h:02d}" for h in range(24)])
        self.r_sched.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.r_sched.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.r_sched.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.r_sched.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.r_sched.setMaximumHeight(240)
        self.r_sched.cellClicked.connect(self._toggle_cell)
        lay.addWidget(self.r_sched)
        btn = QPushButton("Aplicar")
        btn.setProperty("primary", True)
        btn.clicked.connect(self._save_recording)
        lay.addWidget(btn)
        lay.addStretch(1)
        self._load_recording()

    def _paint_cell(self, r: int, c: int, on: bool) -> None:
        it = self.r_sched.item(r, c) or QTableWidgetItem()
        it.setData(Qt.ItemDataRole.UserRole, on)
        from PyQt6.QtGui import QColor
        it.setBackground(QColor(self.accent if on else "#0f141c"))
        self.r_sched.setItem(r, c, it)

    def _toggle_cell(self, r: int, c: int) -> None:
        it = self.r_sched.item(r, c)
        self._paint_cell(r, c, not bool(it.data(Qt.ItemDataRole.UserRole)))

    def _load_recording(self) -> None:
        rc = self.r_pick.spec().recording
        self.r_mode.setCurrentIndex(max(0, self.r_mode.findData(rc.mode)))
        self.r_pre.setValue(rc.pre_event_s)
        self.r_post.setValue(rc.post_event_s)
        self.r_seg.setValue(rc.segment_s)
        self.r_over.setChecked(rc.overwrite)
        sched = rc.schedule or [[True] * 24 for _ in range(7)]
        for r in range(7):
            for c in range(24):
                self._paint_cell(r, c, bool(sched[r][c]))

    def _save_recording(self) -> None:
        spec = self.r_pick.spec()
        rc = spec.recording
        rc.mode = self.r_mode.currentData()
        rc.pre_event_s, rc.post_event_s, rc.segment_s = self.r_pre.value(), self.r_post.value(), self.r_seg.value()
        rc.overwrite = self.r_over.isChecked()
        grid = [[bool(self.r_sched.item(r, c).data(Qt.ItemDataRole.UserRole)) for c in range(24)] for r in range(7)]
        rc.schedule = [] if all(all(row) for row in grid) else grid
        self.ctx.bus.publish(EventType.CONFIG_CHANGED, spec.id, 0, section="recording")
        self.ctx.db.add_log("operation", "Configuracao de gravacao alterada", spec.id, "GUI")
        self._saved("Gravacao salva.")

    # ------------------------------------------------------------------ tunel
    def _build_tunnel(self) -> None:
        lay = self._tab("Tunel")
        pub = self.ctx.profile.public
        form = QFormLayout()
        self.t_enabled = QCheckBox("Iniciar o tunel automaticamente ao abrir o perfil")
        self.t_enabled.setChecked(pub.enabled)
        self.t_provider = QComboBox()
        for k, t in (("cloudflare", "Cloudflare Tunnel (quick tunnel, sem conta)"), ("ngrok", "ngrok"),
                     ("tailscale", "Tailscale Funnel"), ("bore", "TCP publico sem conta - bore/pinggy (RTSP/HTTP)")):
            self.t_provider.addItem(t, k)
        self.t_provider.setCurrentIndex(max(0, self.t_provider.findData(pub.provider)))
        self.t_token = QLineEdit(pub.ngrok_authtoken)
        self.t_token.setEchoMode(QLineEdit.EchoMode.Password)
        self.t_tcp = QCheckBox("ngrok: tunel TCP para RTSP")
        self.t_tcp.setChecked(pub.ngrok_tcp_rtsp)
        self.t_expose = QCheckBox("Expor as APIs ISAPI/CGI das marcas pela internet (desligado por padrao)")
        self.t_expose.setChecked(pub.expose_brand_apis)
        self.t_rtsp_auth = QCheckBox("RTSP exige autenticacao (usuario e senha)")
        self.t_rtsp_auth.setChecked(self.ctx.profile.rtsp_auth)
        form.addRow("", self.t_rtsp_auth)
        form.addRow("", self.t_enabled)
        form.addRow("Provedor", self.t_provider)
        form.addRow("Authtoken ngrok", self.t_token)
        form.addRow("", self.t_tcp)
        form.addRow("", self.t_expose)
        lay.addLayout(form)
        btn = QPushButton("Salvar")
        btn.setProperty("primary", True)
        btn.clicked.connect(self._save_tunnel)
        lay.addWidget(btn)
        lay.addStretch(1)

    def _save_tunnel(self) -> None:
        pub = self.ctx.profile.public
        self.ctx.profile.rtsp_auth = self.t_rtsp_auth.isChecked()
        pub.enabled = self.t_enabled.isChecked()
        pub.provider = self.t_provider.currentData()
        pub.ngrok_authtoken = self.t_token.text().strip()
        pub.ngrok_tcp_rtsp = self.t_tcp.isChecked()
        pub.expose_brand_apis = self.t_expose.isChecked()
        self._saved("Configuracao salva (RTSP com/sem autenticacao vale na hora; a opcao de expor APIs vale no proximo inicio).")
