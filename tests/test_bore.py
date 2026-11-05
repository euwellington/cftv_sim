from core.app_context import AppContext
from core.device_manager import build_profile
from tunnel.bore import LISTEN_RE, BoreTunnel


def test_listen_regex():
    m = LISTEN_RE.search("2026-11-05T00:00:00Z  INFO bore_cli::client: listening at bore.pub:34567")
    assert m.groups() == ("bore.pub", "34567")


def test_rows_use_tunnel_endpoints(tmp_path):
    prof = build_profile(brand_choice="hikvision", mode="camera", camera_count=1, bind_ip="127.0.0.1",
                         admin_password="Str0ngPassw0rd9xQ", settings={"capture": {"force_test_pattern": True}})
    prof.public.provider = "bore"
    ctx = AppContext(prof, str(tmp_path / "t.db"))
    ctx.set_credentials("admin", "Str0ngPassw0rd9xQ")
    d = prof.devices[0]
    ctx.tcp_endpoints = {d.rtsp_port: ("bore.pub", 1111), d.http_port: ("bore.pub", 2222)}
    r = ctx.access_rows(ngrok_tcp=True)[0]
    assert "@bore.pub:1111/Streaming/Channels/101" in r["rtsp"] and "@bore.pub:2222/" in r["http"]
    assert BoreTunnel(ctx)._ports()[-1] == prof.web_port


def test_pinggy_regex_and_fallback_wiring():
    from tunnel.pinggy import ADDR_RE, ANSI
    m = ADDR_RE.search(ANSI.sub("", "\x1b[32mtcp://rnabc-1-2-3.a.pinggy.link:40527\x1b[0m"))
    assert m.groups() == ("rnabc-1-2-3.a.pinggy.link", "40527")
