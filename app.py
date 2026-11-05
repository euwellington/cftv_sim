#!/usr/bin/env python3
"""CFTV Simulator: ponto de entrada. Toda a interacao e pela GUI (splash -> assistente -> painel)."""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

from PyQt6.QtCore import QEventLoop
from PyQt6.QtWidgets import QApplication, QDialog, QLabel, QMessageBox, QProgressBar, QVBoxLayout

from core import config
from core.app_context import AppContext
from core.device_manager import load_profile, save_profile
from core.models import Profile
from core.ports import PortSet, check_conflicts, suggest_port_plan
from gui.main_window import MainWindow
from gui.splash import SplashScreen
from gui.theme import ACCENTS, apply_theme
from gui.wizard.start_dialog import StartDialog
from gui.wizard.wizard import ProfileWizard
from gui.workers import run_async

log = logging.getLogger("cftv_sim")


def setup_logging() -> None:
    """Log somente em arquivo (logs/app.log): nao ha terminal na aplicacao."""
    config.ensure_dirs()
    h = RotatingFileHandler(config.LOG_DIR / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(config.load_defaults()["app"]["log_level"])
    root.addHandler(h)

    def hook(exc_type, exc, tb) -> None:  # noqa: ANN001
        log.critical("excecao nao tratada", exc_info=(exc_type, exc, tb))
    sys.excepthook = hook


def run_splash() -> None:
    loop = QEventLoop()
    s = SplashScreen()
    s.finished.connect(loop.quit)
    s.show_for(1800)
    loop.exec()


def choose_profile() -> Optional[tuple[Profile, str, str]]:
    """StartDialog -> assistente (novo) ou perfil salvo. ``None`` = usuario cancelou."""
    dlg = StartDialog()
    if not dlg.exec() or not dlg.choice:
        return None
    kind, name = dlg.choice
    if kind == "open" and name:
        try:
            prof = load_profile(name)
            return prof, *ask_password(prof)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(None, "Perfil", f"Nao foi possivel abrir '{name}':\n{exc}")
            return choose_profile()
    wiz = ProfileWizard()
    if not wiz.exec():
        return choose_profile()
    prof = wiz.result_profile()
    if prof is None:
        return None
    save_profile(prof)
    user, pw = wiz.result_credentials()
    if pw:
        from core import credentials
        credentials.save(prof.name, pw)
    return prof, user, pw


def ask_password(profile: Profile) -> tuple[str, str]:
    """Perfil salvo nao guarda a senha: pergunta (so memoria) para exibir as URLs reais."""
    from PyQt6.QtWidgets import QInputDialog, QLineEdit
    from core.security import UserStore
    dev = profile.devices[0]
    admin = next((u.username for u in dev.users if u.role == "admin"), "admin")
    store = UserStore(dev.users, realms=[dev.realm])
    from core import credentials
    saved = credentials.load(profile.name)
    if saved and store.verify(admin, saved):
        return admin, saved
    for _ in range(3):
        pw, ok = QInputDialog.getText(None, "Senha do administrador",
                                      f"Senha de '{admin}' (so para mostrar as URLs; nao e gravada):",
                                      QLineEdit.EchoMode.Password)
        if not ok or not pw:
            return admin, ""
        if store.verify(admin, pw):
            credentials.save(profile.name, pw)
            return admin, pw
        QMessageBox.warning(None, "Senha", "Senha incorreta.")
    return admin, ""


def ensure_free_ports(profile: Profile) -> None:
    """Perfil reaberto: se alguma porta estiver ocupada, sugere novas e avisa."""
    ports = [profile.web_port] + [p for d in profile.devices for p in (d.http_port, d.rtsp_port)]
    bad = check_conflicts(ports, profile.devices[0].bind_ip if profile.devices else "0.0.0.0")
    if not bad:
        return
    cfg = config.load_settings(profile.settings)["ports"]
    plan: list[PortSet] = suggest_port_plan([d.brand.value for d in profile.devices],
                                            host=profile.devices[0].bind_ip, rtsp_start=cfg["rtsp_start"],
                                            hik_http_start=cfg["hikvision_http_start"],
                                            intelbras_http_start=cfg["intelbras_http_start"],
                                            reserved=[profile.web_port])
    for d, ps in zip(profile.devices, plan):
        d.http_port, d.rtsp_port = ps.http, ps.rtsp
    save_profile(profile)
    QMessageBox.information(None, "Portas ajustadas",
                            "Algumas portas do perfil estavam ocupadas:\n" +
                            "\n".join(f"  {p}: {w}" for p, w in bad.items()) +
                            "\n\nNovas portas foram escolhidas automaticamente.")


def start_context(profile: Profile, user: str = "admin", password: str = "") -> Optional[AppContext]:
    """Cria o contexto e inicia os equipamentos em thread de trabalho (a UI nao congela)."""
    ctx = AppContext(profile)
    ctx.set_credentials(user, password)
    dlg = QDialog()
    dlg.setWindowTitle("CFTV Simulator")
    dlg.setMinimumWidth(380)
    lay = QVBoxLayout(dlg)
    lay.addWidget(QLabel("Iniciando equipamentos, streams e servicos..."))
    bar = QProgressBar()
    bar.setRange(0, 0)
    lay.addWidget(bar)
    result: dict = {}
    loop = QEventLoop()

    def done(_r) -> None:  # noqa: ANN001
        result["ok"] = True
        loop.quit()

    def failed(msg: str) -> None:
        result["err"] = msg
        loop.quit()

    dlg.show()
    run_async(ctx.start, on_done=done, on_error=failed)
    loop.exec()
    dlg.close()
    if "err" in result:
        QMessageBox.critical(None, "Falha ao iniciar", result["err"])
        return None
    return ctx


def main() -> int:
    setup_logging()
    app = QApplication(sys.argv)
    app.setApplicationName("CFTV Simulator")
    apply_theme(app, "all")
    run_splash()
    code = 0
    while True:
        chosen = choose_profile()
        if chosen is None:
            break
        profile, adm_user, adm_pw = chosen
        brands = {d.brand.value for d in profile.devices}
        accent = ACCENTS[next(iter(brands))] if len(brands) == 1 else ACCENTS["all"]
        apply_theme(app, next(iter(brands)) if len(brands) == 1 else "all")
        ensure_free_ports(profile)
        ctx = start_context(profile, adm_user, adm_pw)
        if ctx is None:
            continue
        win = MainWindow(ctx, accent)
        win.show()
        code = app.exec()
        run_async(ctx.stop)  # para tudo fora da thread da UI
        loop = QEventLoop()
        from PyQt6.QtCore import QTimer
        QTimer.singleShot(50, loop.quit)
        loop.exec()
        ctx.stop()
        if not win.restart:
            break
    return code


if __name__ == "__main__":
    sys.exit(main())
