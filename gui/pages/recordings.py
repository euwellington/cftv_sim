"""Gravacoes: linha do tempo por canal (azul continua / amarelo movimento / vermelho manual),
playback (frames OpenCV, 1/2/4/8x) e exportacao MP4."""
from __future__ import annotations

import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import cv2
import qtawesome as qta
from PyQt6.QtCore import QDate, Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPixmap, QTextCharFormat
from PyQt6.QtWidgets import (QComboBox, QDateEdit, QFileDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                             QSizePolicy, QVBoxLayout)

from core import config
from core.db import Database
from core.fmt import local_to_utc, utc_to_local
from gui.pages.base import BasePage
from gui.widgets.cards import panel
from gui.widgets.timeline import KIND_COLORS, KIND_LABELS, TimelineView
from gui.workers import run_async


class PlaybackThread(QThread):
    """Le os segmentos MP4 a partir de um instante e emite quadros na cadencia pedida."""

    frame = pyqtSignal(QImage, object)  # imagem, datetime local
    ended = pyqtSignal(str)

    def __init__(self, db: Database, device_id: str, channel: int, start_utc: datetime, speed: float = 1.0) -> None:
        super().__init__()
        self.db, self.device_id, self.channel = db, device_id, channel
        self.start_utc, self.speed = start_utc, speed
        self._stop = False
        self._paused = False

    def stop(self) -> None:
        self._stop = True

    def set_paused(self, on: bool) -> None:
        self._paused = on

    def run(self) -> None:
        segs = self.db.segments_between(self.device_id, self.channel, self.start_utc,
                                        self.start_utc + timedelta(hours=24))
        if not segs:
            self.ended.emit("Sem gravacao neste instante.")
            return
        for seg in segs:
            if self._stop:
                break
            path = Path(seg.path)
            if not path.exists():
                continue
            cap = cv2.VideoCapture(str(path))
            if not cap.isOpened():
                continue
            fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
            offset = max(0.0, (self.start_utc - seg.start).total_seconds())
            if offset > 0:
                cap.set(cv2.CAP_PROP_POS_MSEC, offset * 1000)
            skip = max(0, int(self.speed) - 1) if self.speed > 1 else 0
            delay = (1.0 / fps) / min(self.speed, 1.0 if self.speed > 1 else self.speed)
            while not self._stop:
                if self._paused:
                    time.sleep(0.05)
                    continue
                t0 = time.monotonic()
                ok, img = cap.read()
                if not ok:
                    break
                for _ in range(skip):
                    cap.grab()
                when = seg.start + timedelta(milliseconds=cap.get(cv2.CAP_PROP_POS_MSEC))
                rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
                h, w = rgb.shape[:2]
                self.frame.emit(QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy(), utc_to_local(when))
                rest = delay - (time.monotonic() - t0)
                if rest > 0:
                    time.sleep(rest)
            cap.release()
            self.start_utc = seg.end or self.start_utc
        self.ended.emit("Fim das gravacoes." if not self._stop else "")


def export_mp4(db: Database, device_id: str, channel: int, a_utc: datetime, b_utc: datetime, out: str) -> str:
    """Concatena os segmentos do intervalo em um MP4 (FFmpeg concat, sem recodificar)."""
    ff = config.bin_path("ffmpeg")
    if not ff:
        raise RuntimeError("ffmpeg nao encontrado (rode install_and_run para baixar em bin/).")
    segs = [s for s in db.segments_between(device_id, channel, a_utc, b_utc) if Path(s.path).exists()]
    if not segs:
        raise RuntimeError("Nenhum arquivo de gravacao no intervalo.")
    first = segs[0]
    ss = max(0.0, (a_utc - first.start).total_seconds())
    total = (b_utc - a_utc).total_seconds()
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        for s in segs:
            fh.write(f"file '{Path(s.path).as_posix()}'\n")
        lst = fh.name
    try:
        cmd = [str(ff), "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
               "-ss", f"{ss:.2f}", "-t", f"{total:.2f}", "-c", "copy", out]
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "ffmpeg falhou")
    finally:
        Path(lst).unlink(missing_ok=True)
    return out


class RecordingsPage(BasePage):
    title = "Gravacoes"
    subtitle = "Linha do tempo por canal, playback e exportacao"

    def __init__(self, *a, **kw) -> None:  # noqa: ANN002, ANN003
        super().__init__(*a, **kw)
        self._thread: Optional[PlaybackThread] = None
        self._mark: list[Optional[datetime]] = [None, None]
        self._cur_ch = 1

        bar = QHBoxLayout()
        self.dev = QComboBox()
        for d in self.ctx.profile.devices:
            self.dev.addItem(f"{d.brand.value.capitalize()} - {d.model}", d.id)
        self.dev.currentIndexChanged.connect(self._reload)
        self.date = QDateEdit(QDate.currentDate())
        self.date.setCalendarPopup(True)
        self.date.setDisplayFormat("dd/MM/yyyy")
        self.date.dateChanged.connect(self._reload)
        self.date.calendarWidget().currentPageChanged.connect(self._mark_days)
        today = QPushButton("Hoje")
        today.clicked.connect(lambda: self.date.setDate(QDate.currentDate()))
        refresh = QPushButton(qta.icon("fa5s.sync", color="#e6e9ef"), "")
        refresh.setToolTip("Atualizar")
        refresh.clicked.connect(self._reload)
        bar.addWidget(QLabel("Equipamento:"))
        bar.addWidget(self.dev)
        bar.addWidget(QLabel("Dia:"))
        bar.addWidget(self.date)
        bar.addWidget(today)
        bar.addWidget(refresh)
        bar.addStretch(1)
        for k, label in KIND_LABELS.items():
            chip = QLabel(f"  {label}")
            chip.setStyleSheet(f"border-left: 12px solid {KIND_COLORS[k]}; padding-left: 4px; color: #8b94a7;")
            bar.addWidget(chip)
        self.root.addLayout(bar)

        vp, vl = panel()
        self.screen = QLabel("Clique na linha do tempo para reproduzir")
        self.screen.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.screen.setMinimumHeight(240)
        self.screen.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.screen.setStyleSheet("background: #05070a; border-radius: 8px; color: #596073;")
        vl.addWidget(self.screen, 1)
        ctl = QHBoxLayout()
        self.btn_play = QPushButton(qta.icon("fa5s.pause", color="#e6e9ef"), " Pausar")
        self.btn_play.setCheckable(True)
        self.btn_play.clicked.connect(self._toggle_pause)
        self.btn_stop = QPushButton(qta.icon("fa5s.stop", color="#e6e9ef"), " Parar")
        self.btn_stop.clicked.connect(self._stop)
        self.speed = QComboBox()
        for s in (1, 2, 4, 8):
            self.speed.addItem(f"{s}x", s)
        self.speed.currentIndexChanged.connect(self._speed_changed)
        self.when = QLabel("--:--:--")
        self.when.setObjectName("H2")
        self.btn_in = QPushButton("Marcar inicio")
        self.btn_out = QPushButton("Marcar fim")
        self.btn_in.clicked.connect(lambda: self._set_mark(0))
        self.btn_out.clicked.connect(lambda: self._set_mark(1))
        self.btn_export = QPushButton(qta.icon("fa5s.file-export", color="#ffffff"), " Exportar MP4")
        self.btn_export.setProperty("primary", True)
        self.btn_export.clicked.connect(self._export)
        for w in (self.btn_play, self.btn_stop, QLabel("Velocidade"), self.speed, self.when):
            ctl.addWidget(w)
        ctl.addStretch(1)
        for w in (self.btn_in, self.btn_out, self.btn_export):
            ctl.addWidget(w)
        vl.addLayout(ctl)
        self.root.addWidget(vp, 1)

        self.timeline = TimelineView()
        self.timeline.seek.connect(self._seek)
        self.root.addWidget(self.timeline)
        self.status = QLabel("")
        self.status.setObjectName("Muted")
        self.root.addWidget(self.status)
        self._reload()

    # ------------------------------------------------------------------ dados
    def on_show(self) -> None:
        self._reload()

    def _day(self) -> datetime:
        d = self.date.date()
        return datetime(d.year(), d.month(), d.day())

    def _reload(self) -> None:
        did = self.dev.currentData()
        if not did:
            return
        spec = self.ctx.profile.device(did)
        chans = [c.index for c in spec.channels]
        day = self._day()
        self.timeline.set_day(day)
        self.timeline.set_channels(chans)
        a, b = local_to_utc(day), local_to_utc(day + timedelta(days=1))
        db = self.ctx.db

        def load():  # noqa: ANN202
            return {ch: [(utc_to_local(s.start), utc_to_local(s.end or s.start + timedelta(seconds=60)), s.kind)
                         for s in db.segments_between(did, ch, a, b)] for ch in chans}

        def done(data) -> None:  # noqa: ANN001
            self.timeline.clear_segments()
            total = 0
            for ch, segs in data.items():
                self.timeline.set_segments(ch, segs)
                total += len(segs)
            self.status.setText(f"{total} segmento(s) no dia selecionado.")

        run_async(load, on_done=done, on_error=lambda m: self.status.setText(m))
        self._mark_days()

    def _mark_days(self, *_a) -> None:  # noqa: ANN002
        did = self.dev.currentData()
        if not did:
            return
        cal = self.date.calendarWidget()
        y, m = cal.yearShown(), cal.monthShown()
        fmt = QTextCharFormat()
        fmt.setBackground(QColor(self.accent))
        fmt.setForeground(QColor("white"))
        days = set()
        for ch in self.ctx.profile.device(did).channels:
            days.update(self.ctx.db.days_with_recordings(did, ch.index, y, m))
        for d in days:
            cal.setDateTextFormat(QDate(y, m, d), fmt)

    # ------------------------------------------------------------------ playback
    def _seek(self, channel: int, when: datetime) -> None:
        self._cur_ch = channel
        self._stop()
        self.timeline.set_playhead(when)
        t = PlaybackThread(self.ctx.db, self.dev.currentData(), channel, local_to_utc(when),
                           float(self.speed.currentData()))
        t.frame.connect(self._on_frame)
        t.ended.connect(lambda msg: self.status.setText(msg) if msg else None)
        self._thread = t
        self.btn_play.setChecked(False)
        self.btn_play.setText(" Pausar")
        t.start()

    def _on_frame(self, img: QImage, when: datetime) -> None:
        self.screen.setPixmap(QPixmap.fromImage(img).scaled(
            self.screen.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.when.setText(when.strftime("%d/%m %H:%M:%S"))
        self.timeline.set_playhead(when)

    def _toggle_pause(self, on: bool) -> None:
        if self._thread:
            self._thread.set_paused(on)
        self.btn_play.setText(" Continuar" if on else " Pausar")

    def _speed_changed(self) -> None:
        if self._thread and self._thread.isRunning() and self.timeline.playhead is not None:
            self._seek(self._cur_ch, self.timeline.day + timedelta(seconds=self.timeline.playhead))

    def _stop(self) -> None:
        if self._thread:
            self._thread.stop()
            self._thread.wait(1500)
            self._thread = None

    # ------------------------------------------------------------------ exportacao
    def _set_mark(self, idx: int) -> None:
        if self.timeline.playhead is None:
            self.status.setText("Clique na linha do tempo antes de marcar.")
            return
        self._mark[idx] = self.timeline.day + timedelta(seconds=self.timeline.playhead)
        self.timeline.set_selection(*self._mark)
        self.status.setText("Marcado: " + " -> ".join(m.strftime("%H:%M:%S") if m else "?" for m in self._mark))

    def _export(self) -> None:
        a, b = self._mark
        if not a or not b or a == b:
            QMessageBox.information(self, "Exportar", "Marque o inicio e o fim do trecho na linha do tempo.")
            return
        a, b = sorted((a, b))
        did = self.dev.currentData()
        default = str(Path.home() / f"{did}_ch{self._cur_ch}_{a:%Y%m%d_%H%M%S}.mp4")
        out, _ = QFileDialog.getSaveFileName(self, "Exportar MP4", default, "MP4 (*.mp4)")
        if not out:
            return
        self.btn_export.setEnabled(False)
        self.status.setText("Exportando...")
        run_async(export_mp4, self.ctx.db, did, self._cur_ch, local_to_utc(a), local_to_utc(b), out,
                  on_done=lambda p: (self.status.setText(f"Exportado: {p}"), self.btn_export.setEnabled(True)),
                  on_error=lambda m: (self.status.setText("Falha: " + m), self.btn_export.setEnabled(True)))

    def hideEvent(self, ev) -> None:  # noqa: ANN001, N802
        self._stop()
        super().hideEvent(ev)
