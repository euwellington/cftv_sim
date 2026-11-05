"""Dispositivos: reiniciar camera/sistema, testar API e copiar URLs RTSP/HTTP."""
from __future__ import annotations

import httpx
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit,
                             QMessageBox, QPushButton, QTableWidget, QTableWidgetItem, QWidget)

from core.fmt import human_duration
from core.models import Brand
from gui.pages.base import BasePage
from gui.theme import repolish
from gui.widgets.cards import panel
from gui.widgets.logo_widget import LogoWidget
from gui.workers import run_async
from PyQt6.QtCore import QTimer

STATE = {"online": ("Online", "ChipOk"), "rebooting": ("Reiniciando", "ChipWarn"), "stopped": ("Parado", "ChipBad")}


def api_probe_url(spec, host: str = "127.0.0.1") -> str:  # noqa: ANN001
    base = f"http://{host}:{spec.http_port}"
    if spec.brand is Brand.HIKVISION:
        return base + "/ISAPI/System/deviceInfo"
    return base + "/cgi-bin/magicBox.cgi?action=getSystemInfo"


def probe_api(url: str, user: str, password: str) -> str:
    """GET com Digest; devolve resumo legivel (roda em thread de trabalho)."""
    try:
        with httpx.Client(timeout=6.0) as c:
            r0 = c.get(url)
            r = c.get(url, auth=httpx.DigestAuth(user, password))
    except httpx.ConnectError:
        return "Conexao recusada: a API nao esta escutando (equipamento reiniciando ou servico de API desligado)."
    except Exception as exc:  # noqa: BLE001
        return f"Erro: {type(exc).__name__}: {exc}"
    return (f"Sem credenciais: HTTP {r0.status_code} ({r0.headers.get('www-authenticate', '-')[:60]})\n"
            f"Com Digest: HTTP {r.status_code}\n\n{r.text[:700]}")


class DevicesPage(BasePage):
    title = "Dispositivos"
    subtitle = "Controle, teste de API e URLs de acesso"

    def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
        super().__init__(*a, **kw)
        self._w: dict[str, dict] = {}
        self.msg = QLabel("")
        self.msg.setObjectName("Muted")
        self.msg.setWordWrap(True)
        for dev in self.ctx.profile.devices:
            self._build_device(dev)
        self.root.addWidget(self.msg)
        self.root.addStretch(1)
        self.refresh()
        self._timer = QTimer(self)
        self._timer.timeout.connect(lambda: self.isVisible() and self.refresh())
        self._timer.start(1000)
        self.bridge.event.connect(lambda _e: self.refresh())

    def _build_device(self, spec) -> None:  # noqa: ANN001
        p, lay = panel()
        head = QHBoxLayout()
        head.addWidget(LogoWidget(spec.brand.value, 30))
        title = QLabel(f"<b>{spec.model}</b>  <span style='color:#8b94a7'>{spec.kind.value.upper()} - serial {spec.serial}</span>")
        chip = QLabel("")
        up = QLabel("")
        up.setObjectName("Muted")
        head.addWidget(title, 1)
        head.addWidget(up)
        head.addWidget(chip)
        lay.addLayout(head)
        info = QLabel(f"MAC {spec.mac}  |  firmware {spec.firmware} ({spec.firmware_build})  |  HTTP {spec.http_port}  "
                      f"RTSP {spec.rtsp_port}  SDK {spec.sdk_port} (listener)  |  ONVIF {'on' if spec.onvif_enabled else 'off'}")
        info.setObjectName("Muted")
        info.setWordWrap(True)
        lay.addWidget(info)
        btns = QHBoxLayout()
        b_sys = QPushButton("Reiniciar sistema")
        b_sys.setProperty("danger", True)
        b_sys.clicked.connect(lambda: self._reboot_system(spec.id))
        b_api = QPushButton("Testar API")
        b_api.clicked.connect(lambda: self._test_api(spec))
        b_rtsp = QPushButton("Copiar URLs RTSP")
        b_rtsp.clicked.connect(lambda: self._copy_rtsp(spec.id))
        b_http = QPushButton("Copiar URL HTTP")
        b_http.clicked.connect(lambda: self._copy(self.ctx.http_urls()[spec.id], "URL HTTP copiada."))
        for b in (b_sys, b_api, b_rtsp, b_http):
            btns.addWidget(b)
        btns.addStretch(1)
        lay.addLayout(btns)

        table = QTableWidget(len(spec.channels), 5)
        table.setHorizontalHeaderLabels(["Canal", "Nome", "Estado", "Uptime", "Acao"])
        table.verticalHeader().setVisible(False)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        table.setFixedHeight(44 + 38 * len(spec.channels))
        for i, ch in enumerate(spec.channels):
            table.setRowHeight(i, 38)
            table.setItem(i, 0, QTableWidgetItem(str(ch.index)))
            for c in (1, 2, 3):
                table.setItem(i, c, QTableWidgetItem(""))
            b = QPushButton("Reiniciar camera" if spec.is_recorder else "Reiniciar")
            b.setFixedHeight(28)
            b.setStyleSheet("padding: 2px 12px;")
            b.clicked.connect(lambda _c, d=spec.id, n=ch.index: self._reboot_camera(d, n))
            w = QWidget()
            hl = QHBoxLayout(w)
            hl.setContentsMargins(4, 2, 4, 2)
            hl.addWidget(b)
            table.setCellWidget(i, 4, w)
        lay.addWidget(table)
        self.root.addWidget(p)
        self._w[spec.id] = {"chip": chip, "up": up, "table": table}

    def refresh(self) -> None:
        for dev in self.ctx.manager.list():
            w = self._w[dev.id]
            txt, obj = STATE.get(dev.state, STATE["stopped"])
            if dev.state == "rebooting":
                txt += f" ({int(dev.reboot_remaining()) + 1}s)"
            w["chip"].setText(txt)
            if w["chip"].objectName() != obj:
                w["chip"].setObjectName(obj)
                repolish(w["chip"])
            w["up"].setText("uptime " + human_duration(dev.uptime()))
            for i, cam in enumerate(dev.cameras.values()):
                w["table"].item(i, 1).setText(cam.name)
                st = cam.state.value + (f" ({int(cam.reboot_remaining()) + 1}s)" if cam.state.value == "rebooting" else "")
                w["table"].item(i, 2).setText(st)
                w["table"].item(i, 3).setText(human_duration(cam.uptime()))

    # ------------------------------------------------------------------ acoes
    def _reboot_camera(self, did: str, ch: int) -> None:
        if QMessageBox.question(self, "Reiniciar camera", f"Reiniciar o canal {ch}?") == QMessageBox.StandardButton.Yes:
            d = self.ctx.reboot_camera(did, ch)
            self.msg.setText(f"Canal {ch} reiniciando por ~{d:.0f}s.")

    def _reboot_system(self, did: str) -> None:
        if QMessageBox.question(self, "Reiniciar sistema",
                                "Reiniciar o equipamento inteiro? Todos os canais e a API caem por 20 a 40 s.") \
                == QMessageBox.StandardButton.Yes:
            d = self.ctx.reboot_system(did)
            self.msg.setText(f"Sistema reiniciando por ~{d:.0f}s.")

    def _copy(self, text: str, ok: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.msg.setText(ok)

    def _copy_rtsp(self, did: str) -> None:
        spec = self.ctx.profile.device(did)
        user = spec.users[0].username if spec.users else "admin"
        urls = self.ctx.manager.rtsp_urls(did, self.ctx.host(), user, "<senha>")
        self._copy("\n".join(urls), f"{len(urls)} URLs RTSP copiadas (troque <senha> pela senha do administrador).")

    def _test_api(self, spec) -> None:  # noqa: ANN001
        user = spec.users[0].username if spec.users else "admin"
        pw, ok = QInputDialog.getText(self, "Testar API", f"Senha de '{user}' (nao e salva):", QLineEdit.EchoMode.Password)
        if not ok:
            return
        url = api_probe_url(spec)
        self.msg.setText("Testando " + url)
        run_async(probe_api, url, user, pw,
                  on_done=lambda t: QMessageBox.information(self, "Resultado do teste", f"{url}\n\n{t}"),
                  on_error=lambda m: self.msg.setText(m))
