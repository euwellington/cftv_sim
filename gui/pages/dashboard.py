"""Dashboard: equipamentos, uptime, CPU/RAM, FPS/bitrate, HDD, ultimos eventos, URLs e QR code."""
from __future__ import annotations

import os

import psutil
import qrcode
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QGuiApplication, QImage, QPixmap
from PyQt6.QtWidgets import (QFrame, QGridLayout, QHBoxLayout, QLabel, QListWidget, QPushButton, QTableWidget,
                             QTableWidgetItem, QVBoxLayout, QHeaderView, QAbstractItemView)

from core.event_bus import Event, EventType
from core.fmt import human_bytes, human_duration, utc_to_local
from core.models import Brand
from gui.pages.base import BasePage
from gui.theme import repolish
from gui.widgets.cards import StatCard, UsageBar, panel
from gui.widgets.logo_widget import LogoWidget

STATE_TXT = {"online": ("Online", "ChipOk"), "rebooting": ("Reiniciando", "ChipWarn"),
             "offline": ("Offline", "ChipBad"), "stopped": ("Parado", "ChipBad")}
EVENT_TXT = {EventType.MOTION_START: "Movimento", EventType.MOTION_STOP: "Fim do movimento",
             EventType.VIDEO_LOSS: "Perda de video", EventType.VIDEO_RESTORED: "Video restaurado",
             EventType.CAMERA_REBOOTING: "Camera reiniciando", EventType.CAMERA_ONLINE: "Camera online",
             EventType.SYSTEM_REBOOTING: "Sistema reiniciando", EventType.SYSTEM_ONLINE: "Sistema online",
             EventType.LOGIN_OK: "Login", EventType.LOGIN_FAIL: "Login falhou", EventType.LOGIN_LOCKED: "Login bloqueado",
             EventType.RECORDING_START: "Gravacao iniciada", EventType.RECORDING_STOP: "Gravacao parada"}


def make_qr_pixmap(text: str, size: int = 180) -> QPixmap:
    """Gera QR code (biblioteca qrcode) como QPixmap."""
    img = qrcode.make(text, box_size=6, border=2).convert("RGBA")
    qi = QImage(img.tobytes("raw", "RGBA"), img.width, img.height, QImage.Format.Format_RGBA8888).copy()
    return QPixmap.fromImage(qi).scaled(size, size, Qt.AspectRatioMode.KeepAspectRatio,
                                        Qt.TransformationMode.FastTransformation)


class DashboardPage(BasePage):
    title = "Dashboard"
    subtitle = "Visao geral dos equipamentos simulados"

    def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
        super().__init__(*a, **kw)
        self._proc = psutil.Process(os.getpid())
        self._proc.cpu_percent(None)
        psutil.cpu_percent(None)

        cards = QHBoxLayout()
        cards.setSpacing(12)
        self.c_dev = StatCard("Equipamentos online")
        self.c_ch = StatCard("Canais online")
        self.c_rec = StatCard("Gravando")
        self.c_cpu = StatCard("CPU (app / sistema)")
        self.c_ram = StatCard("Memoria (app / sistema)")
        for c in (self.c_dev, self.c_ch, self.c_rec, self.c_cpu, self.c_ram):
            cards.addWidget(c)
        self.root.addLayout(cards)

        mid = QHBoxLayout()
        mid.setSpacing(14)
        left = QVBoxLayout()
        left.setSpacing(14)
        self.devs_panel, self.devs_lay = panel("Equipamentos")
        self._dev_widgets: dict[str, dict] = {}
        self._build_device_cards()
        left.addWidget(self.devs_panel)

        sp, sl = panel("Streams")
        self.streams = QTableWidget(0, 6)
        self.streams.setHorizontalHeaderLabels(["Equipamento", "Canal", "Estado", "FPS", "Bitrate", "Uptime"])
        self.streams.verticalHeader().setVisible(False)
        self.streams.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.streams.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.streams.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.streams.setAlternatingRowColors(True)
        sl.addWidget(self.streams)
        left.addWidget(sp, 1)
        mid.addLayout(left, 3)

        right = QVBoxLayout()
        right.setSpacing(14)
        self.access_panel, al = panel("Acesso")
        self.url_lbl = QLabel("")
        self.url_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.url_lbl.setWordWrap(True)
        self.qr = QLabel()
        self.qr.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.copy_btn = QPushButton("Copiar URL do portal")
        self.copy_btn.clicked.connect(lambda: QGuiApplication.clipboard().setText(self._best_url()))
        al.addWidget(self.url_lbl)
        al.addWidget(self.qr)
        al.addWidget(self.copy_btn)
        right.addWidget(self.access_panel)

        ep, el = panel("Ultimos eventos")
        self.events = QListWidget()
        self.events.setMinimumHeight(140)
        el.addWidget(self.events)
        right.addWidget(ep, 1)
        mid.addLayout(right, 2)
        self.root.addLayout(mid, 1)

        self.bridge.event.connect(self._on_event)
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(1000)
        self._load_recent()
        self.refresh()

    # ------------------------------------------------------------------ construcao
    def _build_device_cards(self) -> None:
        for dev in self.ctx.profile.devices:
            row = QFrame()
            lay = QHBoxLayout(row)
            lay.setContentsMargins(0, 4, 0, 4)
            lay.addWidget(LogoWidget(dev.brand.value, 30))
            info = QLabel(f"<b>{dev.model}</b> &nbsp;<span style='color:#8b94a7'>{dev.kind.value.upper()} - "
                          f"{len(dev.channels)} canal(is) - HTTP {dev.http_port} / RTSP {dev.rtsp_port}</span>")
            chip = QLabel("")
            up = QLabel("")
            up.setObjectName("Muted")
            lay.addWidget(info, 1)
            lay.addWidget(up)
            lay.addWidget(chip)
            bar_row = QHBoxLayout()
            bar = UsageBar()
            hdd = QLabel("")
            hdd.setObjectName("Muted")
            bar_row.addWidget(QLabel("HDD"))
            bar_row.addWidget(bar, 1)
            bar_row.addWidget(hdd)
            self.devs_lay.addWidget(row)
            self.devs_lay.addLayout(bar_row)
            self._dev_widgets[dev.id] = {"chip": chip, "uptime": up, "bar": bar, "hdd": hdd}

    # ------------------------------------------------------------------ atualizacao
    def _best_url(self) -> str:
        return self.ctx.public_url or self.ctx.web_url()

    def refresh(self) -> None:
        mgr = self.ctx.manager
        devs = mgr.list()
        n_on = sum(d.state == "online" for d in devs)
        cams = [c for d in devs for c in d.cameras.values()]
        self.c_dev.set(f"{n_on}/{len(devs)}", "reiniciando: " + str(sum(d.state == "rebooting" for d in devs)))
        self.c_ch.set(f"{sum(c.is_online for c in cams)}/{len(cams)}", f"fonte: {mgr.capture.source}")
        self.c_rec.set(str(sum(c.recording for c in cams)), "canais com REC ativo")
        vm = psutil.virtual_memory()
        ncpu = psutil.cpu_count() or 1
        self.c_cpu.set(f"{self._proc.cpu_percent(None) / ncpu:.0f}% / {psutil.cpu_percent(None):.0f}%",
                       f"{psutil.cpu_count()} nucleos")
        self.c_ram.set(f"{human_bytes(self._proc.memory_info().rss)} / {vm.percent:.0f}%",
                       f"total {human_bytes(vm.total)}")

        for d in devs:
            w = self._dev_widgets[d.id]
            txt, obj = STATE_TXT.get(d.state, STATE_TXT["offline"])
            if d.state == "rebooting":
                txt += f" ({int(d.reboot_remaining()) + 1}s)"
            w["chip"].setText(txt)
            if w["chip"].objectName() != obj:
                w["chip"].setObjectName(obj)
                repolish(w["chip"])
            w["uptime"].setText("uptime " + human_duration(d.uptime()))
            info = self.ctx.storage.info(d.id)
            w["bar"].set_percent(info.percent)
            w["hdd"].setText(f"{human_bytes(info.used)} / {human_bytes(info.capacity)}  [{info.status}]")

        self.streams.setRowCount(len(cams))
        for i, c in enumerate(cams):
            nominal = c.spec.main.bitrate_kbps if c.is_online else 0
            br = c.stats.get("bitrate_kbps") or nominal
            vals = [c.device_id, f"{c.channel} - {c.name}", c.state.value, f"{c.stats['fps']:.1f}",
                    f"{br:.0f} kbps", human_duration(c.uptime())]
            for j, v in enumerate(vals):
                it = self.streams.item(i, j)
                if it is None:
                    it = QTableWidgetItem()
                    self.streams.setItem(i, j, it)
                it.setText(v)

        url = self._best_url()
        lines = [f"<b>Portal:</b> {url}"]
        for did, u in self.ctx.http_urls().items():
            lines.append(f"<span style='color:#8b94a7'>{did}:</span> {u}")
        if self.ctx.public_url:
            lines.append(f"<b>Publico:</b> {self.ctx.public_url}")
        self.url_lbl.setText("<br>".join(lines))
        if getattr(self, "_qr_for", None) != url:
            self._qr_for = url
            self.qr.setPixmap(make_qr_pixmap(url))

    def _load_recent(self) -> None:
        self.events.clear()
        for row in reversed(self.ctx.db.events(limit=8)):
            self._add_event_line(utc_to_local(row.ts).strftime("%H:%M:%S"), row.device_id, row.channel, row.description)

    def _add_event_line(self, when: str, dev: str, ch: int, text: str) -> None:
        self.events.insertItem(0, f"{when}  {dev}{' CH' + str(ch) if ch else ''}  {text}")
        while self.events.count() > 30:
            self.events.takeItem(self.events.count() - 1)

    def _on_event(self, ev: Event) -> None:
        if ev.type in EVENT_TXT:
            self._add_event_line(utc_to_local(ev.ts).strftime("%H:%M:%S"), ev.device_id, ev.channel, EVENT_TXT[ev.type])

    def on_show(self) -> None:
        self.refresh()
