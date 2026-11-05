"""Portal web + APIs ISAPI/CGI contra um AppContext real (padrao de teste, sem webcam)."""
import re

import httpx
import pytest

from api.digest_auth import client_digest_header
from api.service import ApiService
from core.app_context import AppContext
from core.device_manager import build_profile

PW = "Str0ngPassw0rd9xQ"


@pytest.fixture(scope="module")
def ctx(tmp_path_factory):
    prof = build_profile(brand_choice="all", mode="recorder", camera_count=2, bind_ip="127.0.0.1", admin_password=PW,
                         web_port=28800, settings={"capture": {"force_test_pattern": True}})
    for i, d in enumerate(prof.devices):
        d.http_port, d.rtsp_port = 28810 + i, 28820 + i
    c = AppContext(prof, str(tmp_path_factory.mktemp("db") / "t.db"))
    c.set_credentials("admin", PW)
    c.manager.start()
    svc = ApiService(c)
    svc.start()
    yield c
    svc.stop()
    c.manager.stop()


def digest_get(url, method="GET", user="admin", pw=PW):
    with httpx.Client(timeout=10) as cl:
        r = cl.request(method, url)
        assert r.status_code == 401
        ch = r.headers["www-authenticate"]
        realm, nonce = re.search(r'realm="([^"]+)"', ch).group(1), re.search(r'nonce="([^"]+)"', ch).group(1)
        path = "/" + url.split("/", 3)[3]
        h = client_digest_header(user, pw, realm, nonce, method, path)
        return cl.request(method, url, headers={"Authorization": h})


def test_portal_login_flow(ctx):
    with httpx.Client(base_url="http://127.0.0.1:28800", follow_redirects=False) as c:
        assert c.get("/live").status_code in (303, 401)
        page = c.get("/login")
        pre = re.search(r'name="pre" value="([^"]+)"', page.text).group(1)
        bad = c.post("/login", data={"username": "admin", "password": "x", "pre": pre})
        assert bad.status_code == 401
        pre = re.search(r'name="pre" value="([^"]+)"', bad.text).group(1)
        ok = c.post("/login", data={"username": "admin", "password": PW, "pre": pre})
        assert ok.status_code == 303
        live = c.get("/live")
        assert live.status_code == 200 and "/stream/" in live.text
        assert "nosniff" in live.headers["x-content-type-options"]
        snap = c.get(f"/snapshot/{ctx.profile.devices[0].id}/1.jpg")
        assert snap.status_code == 200 and snap.content[:2] == b"\xff\xd8"
        # PTZ sem CSRF e recusado
        assert c.post("/api/v1/ptz", json={"dev": ctx.profile.devices[0].id, "ch": 1, "action": "stop"}).status_code == 403


def test_hikvision_isapi(ctx):
    d = next(x for x in ctx.profile.devices if x.brand.value == "hikvision")
    base = f"http://127.0.0.1:{d.http_port}"
    r = digest_get(base + "/ISAPI/System/deviceInfo")
    assert r.status_code == 200 and "<DeviceInfo" in r.text and d.serial in r.text
    assert "<StreamingChannelList" in digest_get(base + "/ISAPI/Streaming/channels").text
    jpg = digest_get(base + "/ISAPI/Streaming/channels/101/picture")
    assert jpg.content[:2] == b"\xff\xd8"
    assert digest_get(base + "/ISAPI/System/deviceInfo", pw="errada-Senha-1").status_code == 401


def test_intelbras_cgi(ctx):
    d = next(x for x in ctx.profile.devices if x.brand.value == "intelbras")
    base = f"http://127.0.0.1:{d.http_port}"
    r = digest_get(base + "/cgi-bin/magicBox.cgi?action=getSystemInfo")
    assert f"serialNumber={d.serial}" in r.text
    assert digest_get(base + "/cgi-bin/snapshot.cgi?channel=1").content[:2] == b"\xff\xd8"
    assert "table.Encode[0].MainFormat[0].Video.Width=" in digest_get(base + "/cgi-bin/configManager.cgi?action=getConfig&name=Encode").text
