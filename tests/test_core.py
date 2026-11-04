"""Testes do nucleo (etapa 1): sem Qt, sem rede, sem webcam (padrao de teste)."""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from core import config
from core.capture import CaptureService, make_test_pattern
from core.db import Database
from core.device_manager import DeviceManager, ProfileError, build_profile, load_profile, save_profile
from core.event_bus import EventBus, EventType
from core.models import Brand, DeviceKind, Profile
from core.motion import MotionDetector, MotionEventTracker
from core.osd import hikvision_datetime, intelbras_datetime
from core.ports import PortSet, check_conflicts, find_free_port, is_port_free, suggest_port_plan
from core.security import (LoginLimiter, SessionManager, UserStore, generate_password, hash_password,
                           is_forbidden_password, make_ha1, password_strength, verify_password)

STRONG = "Zq7#mK2!pLw9"


# ----------------------------------------------------------------------------- seguranca
@pytest.mark.parametrize("pw", ["admin", "12345", "123456", "Admin", "password", "intelbras"])
def test_senhas_padrao_bloqueadas(pw):
    assert is_forbidden_password(pw)
    assert not password_strength(pw).ok


def test_senha_forte_aceita_e_fraca_recusada():
    assert password_strength(STRONG).ok
    assert not password_strength("abc12").ok
    assert not password_strength("aaaaaaaaaaaa1A!").ok  # repeticao
    assert not password_strength("Abcd1234!x").ok  # sequencia abcd/1234
    assert not password_strength("Admin#2026xx", username="admin").ok  # contem usuario
    assert generate_password() and password_strength(generate_password()).ok


def test_hash_e_ha1():
    h = hash_password(STRONG)
    assert verify_password(STRONG, h) and not verify_password("outra", h) and not verify_password(STRONG, "lixo")
    # vetor conhecido: MD5("Mufasa:testrealm@host.com:Circle Of Life") (RFC 2617)
    assert make_ha1("Mufasa", "testrealm@host.com", "Circle Of Life") == "939e7578ed9e3c518a452acee763bce9"


def test_userstore_digest_e_troca_de_senha():
    users: list = []
    st = UserStore(users, realms=["R1"])
    st.set_password("admin", STRONG, role="admin")
    assert st.verify("admin", STRONG) and not st.verify("admin", "x")
    assert st.digest_ha1("admin", "R1") == make_ha1("admin", "R1", STRONG)
    assert st.digest_ha1("admin", "outro") is None
    with pytest.raises(ValueError):
        st.set_password("admin", "12345")


def test_lockout_apos_5_falhas():
    t = [0.0]
    lim = LoginLimiter(5, 900, 1800, clock=lambda: t[0])
    for i in range(4):
        assert lim.fail("ip") is False
        assert lim.check("ip")[0]
    assert lim.fail("ip") is True
    ok, wait = lim.check("ip")
    assert not ok and wait > 1700
    t[0] += 1801
    assert lim.check("ip")[0]


def test_sessoes_expiram():
    t = [0.0]
    sm = SessionManager(ttl=10, clock=lambda: t[0])
    s = sm.create("admin", "admin", "1.2.3.4")
    assert sm.get(s.token) is s
    t[0] = 11
    assert sm.get(s.token) is None


# ----------------------------------------------------------------------------- event bus
def test_bus_callback_fila_e_wildcard():
    bus = EventBus()
    got: list[str] = []
    bus.subscribe(lambda e: got.append(e.type), ["motion.*"])
    q, unsub = bus.subscribe_queue(["video.loss"])
    bus.publish(EventType.MOTION_START, "d", 1)
    bus.publish(EventType.VIDEO_LOSS, "d", 1)
    bus.publish(EventType.LOGIN_OK, "d")
    assert got == ["motion.start"] and q.qsize() == 1
    unsub()
    bus.publish(EventType.VIDEO_LOSS, "d", 1)
    assert q.qsize() == 1
    assert len(bus.history(10)) == 4


def test_bus_assinante_com_erro_nao_derruba_os_outros():
    bus = EventBus()
    seen = []
    bus.subscribe(lambda e: 1 / 0)
    bus.subscribe(lambda e: seen.append(e.seq))
    bus.publish("x")
    assert seen == [1]


def test_bus_async_entre_threads():
    async def run():
        bus = EventBus()
        loop = asyncio.get_running_loop()
        q, unsub = bus.subscribe_async(loop)
        threading.Thread(target=lambda: bus.publish(EventType.MOTION_START, "d", 2)).start()
        ev = await asyncio.wait_for(q.get(), 2)
        unsub()
        return ev

    ev = asyncio.run(run())
    assert ev.type == "motion.start" and ev.channel == 2 and ev.to_dict()["device_id"] == "d"


# ----------------------------------------------------------------------------- portas
def test_portas():
    p = find_free_port(20000)
    assert is_port_free(p)
    plan = suggest_port_plan(["hikvision", "intelbras"], reserved=[8800])
    assert len(plan) == 2
    ports = [x for ps in plan for x in ps.all_ports()]
    assert len(ports) == len(set(ports)) and 8800 not in ports
    assert check_conflicts([plan[0].http, plan[0].http])[plan[0].http].startswith("porta repetida")
    import socket
    with socket.socket() as s:
        s.bind(("0.0.0.0", 0))
        busy = s.getsockname()[1]
        assert not is_port_free(busy)


# ----------------------------------------------------------------------------- osd / formatos
def test_formatos_de_data_das_marcas():
    dt = datetime(2026, 11, 4, 19, 31, 5)  # quarta-feira
    assert hikvision_datetime(dt) == "11-04-2026 Wed 19:31:05"
    assert intelbras_datetime(dt) == "04/11/2026 19:31:05"


# ----------------------------------------------------------------------------- captura / movimento
def test_padrao_de_teste_e_captura():
    f = make_test_pattern(320, 180, 1.0)
    assert f.shape == (180, 320, 3) and f.dtype == np.uint8
    cap = CaptureService(width=320, height=180, fps=30, force_test_pattern=True)
    cap.start()
    try:
        fr, seq = cap.wait_frame(0, 2)
        fr2, seq2 = cap.wait_frame(seq, 2)
        assert fr is not None and seq2 > seq and cap.source == "test-pattern"
    finally:
        cap.stop()


def test_movimento_detecta_objeto():
    det = MotionDetector(sensitivity=80, warmup_frames=5)
    bg = np.full((180, 320, 3), 100, np.uint8)
    for _ in range(30):
        assert not det.process(bg.copy()).active
    moving = bg.copy()
    moving[40:140, 80:240] = 255
    assert det.process(moving).active


def test_movimento_regiao_ignora_fora():
    det = MotionDetector(sensitivity=80, regions=[(0.0, 0.0, 0.2, 0.2)], warmup_frames=5)
    bg = np.full((180, 320, 3), 100, np.uint8)
    for _ in range(30):
        det.process(bg.copy())
    moving = bg.copy()
    moving[60:170, 150:310] = 255  # fora da regiao monitorada
    assert not det.process(moving).active


def test_tracker_histerese():
    tr = MotionEventTracker(hold_seconds=3, min_active_frames=2)
    assert tr.update(True, 0.0) is None
    assert tr.update(True, 0.1) == "start"
    assert tr.update(False, 1.0) is None
    assert tr.update(False, 3.2) == "stop"


# ----------------------------------------------------------------------------- banco
def test_banco_segmentos_eventos_logs():
    db = Database()
    t0 = datetime(2026, 11, 4, 19, 0, 0, tzinfo=timezone.utc)
    for i in range(3):
        db.add_segment("hikcam1", 1, "continuous", t0 + timedelta(minutes=i), f"/x/{i}.mp4",
                       end=t0 + timedelta(minutes=i + 1), size=1000 * (i + 1))
    segs = db.segments_between("hikcam1", 1, t0 + timedelta(seconds=90), t0 + timedelta(minutes=2, seconds=30))
    assert [s.path for s in segs] == ["/x/1.mp4", "/x/2.mp4"]
    assert db.total_size("hdd1") == 6000
    assert db.days_with_recordings("hikcam1", 1, 2026, 11) == [4]
    old = db.oldest_segments("hdd1", 1)
    db.delete_segments([old[0].id])
    assert db.total_size() == 5000
    db.add_event(t0, "hikcam1", 1, "VMD", "start", "Motion alarm")
    assert db.events(device_id="hikcam1")[0].type == "VMD"
    db.add_log("alarm", "Movimento detectado", "hikcam1")
    assert db.logs(text="moviment")[0].category == "alarm"
    assert db.logs(category="system") == []


# ----------------------------------------------------------------------------- perfil / dispositivos
def test_build_profile_todos_com_recorder_e_senha_fraca():
    with pytest.raises(ProfileError):
        build_profile(admin_password="admin")
    prof = build_profile(brand_choice="all", mode="recorder", camera_count=5, intelbras_recorder="MHDX",
                         admin_password=STRONG)
    assert [d.brand for d in prof.devices] == [Brand.HIKVISION, Brand.INTELBRAS]
    hik, intel = prof.devices
    assert hik.kind is DeviceKind.NVR and len(hik.channels) == 5 and hik.model.startswith("DS-76")
    assert intel.family == "MHDX" and intel.model == "MHDX 1108"  # menor modelo com >= 5 canais
    assert hik.http_port != intel.http_port and hik.rtsp_port != intel.rtsp_port
    assert hik.realm == "DS-7608NI-I2" and intel.realm == f"Login to {intel.serial}"
    assert STRONG not in prof.model_dump_json()  # senha nunca vai para o disco
    assert hik.users[0].ha1[hik.realm]


def test_perfil_roundtrip_em_disco(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "PROFILES_DIR", tmp_path)
    prof = build_profile(name="meu perfil", brand_choice="intelbras", admin_password=STRONG)
    path = save_profile(prof)
    assert path.exists() and path.name == "meu_perfil.json"
    again = load_profile("meu perfil")
    assert again == prof and isinstance(again, Profile)


def test_dispositivos_reboot_e_eventos(tmp_path):
    settings = config.load_settings({"capture": {"width": 320, "height": 180, "fps": 30, "force_test_pattern": True},
                                     "simulation": {"reboot_seconds": [1, 1]}})
    prof = build_profile(brand_choice="hikvision", mode="recorder", camera_count=2, admin_password=STRONG,
                         settings={"capture": {"width": 320, "height": 180, "fps": 30, "force_test_pattern": True},
                                   "simulation": {"reboot_seconds": [1, 1]}})
    for d in prof.devices:
        for ch in d.channels:
            ch.main.width, ch.main.height, ch.main.fps = 320, 180, 20
            ch.sub.width, ch.sub.height = 160, 90
    bus = EventBus()
    db = Database()
    mgr = DeviceManager(bus, db, settings)
    mgr.load(prof)
    types: list[str] = []
    bus.subscribe(lambda e: types.append(e.type))
    mgr.start()
    try:
        dev = mgr.get("hikdvr1" if prof.devices[0].kind is DeviceKind.DVR else "hiknvr1")
        cam = dev.cameras[1]
        fr, _ = cam.wait_frame(0, 3)
        assert fr is not None and fr.shape == (180, 320, 3)
        assert cam.snapshot_jpeg()[:2] == b"\xff\xd8"
        assert cam.get_frame("sub").shape == (90, 160, 3)

        time.sleep(1.2)
        assert dev.uptime() >= 1.0
        dur = dev.reboot_system()
        assert 0.5 < dur <= 1.0 + 1e-6 and not dev.is_available
        assert cam.snapshot_jpeg() is None  # offline durante o reboot
        time.sleep(2.2)
        assert dev.is_available and cam.is_online and dev.uptime() < 1.5  # uptime zerou
        assert EventType.SYSTEM_REBOOTING in types and EventType.SYSTEM_ONLINE in types
        assert EventType.CAMERA_ONLINE in types

        dev.reboot_camera(2, 1.0)
        assert dev.cameras[2].state.value == "rebooting" and dev.cameras[1].is_online
        time.sleep(1.8)
        # o escritor grava eventos no banco
        assert any(e.type == "reboot" for e in db.events(device_id=dev.id))
        assert db.logs(device_id=dev.id)
    finally:
        mgr.stop()


def test_ptz_digital_desloca_recorte():
    from core.virtual_camera import _crop_zoom
    img = np.zeros((90, 160, 3), np.uint8)
    img[:, :80] = 50
    img[:, 80:] = 200
    assert _crop_zoom(img, 1.0, 0, 0) is img
    left = _crop_zoom(img, 2.0, -1.0, 0)
    right = _crop_zoom(img, 2.0, 1.0, 0)
    assert left.shape == (45, 80, 3) and left.mean() < right.mean()
