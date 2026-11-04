"""Teste de fumaca da GUI (Qt offscreen): wizard completo, painel e reboot sem congelar."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PyQt6")

from PyQt6.QtCore import QEventLoop, QTimer  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

STRONG = "Zq7#mK2!pLw9"


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    from gui.theme import apply_theme
    apply_theme(app, "all")
    return app


def pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_wizard_fluxo_completo(qapp):
    from gui.wizard.wizard import ProfileWizard
    w = ProfileWizard()
    w.show()
    brand = w.page(0)
    assert not brand.isComplete()  # precisa escolher
    brand._pick("all")
    w.next()
    w.currentPage()._pick("recorder")
    w.currentPage().spin.setValue(3)
    w.next()
    net = w.currentPage()
    net.pw.setText("12345")
    net.pw2.setText("12345")
    assert not net.isComplete()  # senha padrao bloqueada
    net.pw.setText(STRONG)
    net.pw2.setText(STRONG)
    assert net.isComplete()
    w.next()
    w.next()  # acesso: rede local
    summary = w.currentPage()
    assert summary.isComplete()
    assert summary.validatePage()
    prof = w.result_profile()
    assert prof and len(prof.devices) == 2 and all(len(d.channels) == 3 for d in prof.devices)


def test_wizard_modo_publico_exige_confirmacao(qapp):
    from gui.wizard.pages_access import AccessPage
    p = AccessPage()
    p.r_public.setChecked(True)
    assert not p.isComplete()
    p.ack.setChecked(True)
    assert p.isComplete()
    p.provider.setCurrentIndex(p.provider.findData("ngrok"))
    assert not p.isComplete()  # ngrok exige authtoken


def test_logo_placeholder_sem_arquivo(qapp):
    from gui.widgets.logo_widget import LogoWidget
    lw = LogoWidget("hikvision", 40)
    assert lw.sizeHint().width() > 40
    assert lw.grab().toImage().pixelColor(5, 20).red() > 150  # vermelho Hikvision


def test_painel_e_reboot(qapp, tmp_path: Path):
    from core.app_context import AppContext
    from core.device_manager import build_profile
    from gui.main_window import PAGES, MainWindow

    prof = build_profile(name="t", brand_choice="hikvision", mode="recorder", camera_count=2, admin_password=STRONG,
                         settings={"capture": {"force_test_pattern": True, "width": 320, "height": 180, "fps": 20},
                                   "simulation": {"reboot_seconds": [1, 1]}})
    for d in prof.devices:
        for ch in d.channels:
            ch.main.width, ch.main.height, ch.main.fps = 320, 180, 15
            ch.sub.width, ch.sub.height = 160, 90
    ctx = AppContext(prof, db_path=str(tmp_path / "t.db"))
    ctx.start()
    win = MainWindow(ctx, "#3b82f6")
    win.show()
    try:
        pump(600)
        for i in range(len(PAGES)):
            win._goto(i)
            pump(120)
        # a UI continua respondendo durante o reboot
        did = prof.devices[0].id
        ctx.reboot_system(did)
        ticks = []
        t = QTimer()
        t.timeout.connect(lambda: ticks.append(time.monotonic()))
        t.start(50)
        pump(900)
        t.stop()
        assert len(ticks) >= 8, "o laco de eventos da UI foi bloqueado"
        assert not ctx.manager.get(did).is_available
        pump(1800)
        assert ctx.manager.get(did).is_available
        assert ctx.set_recording(did, 1, True) and ctx.manager.camera(did, 1).recording
    finally:
        win.close()
        ctx.stop()
