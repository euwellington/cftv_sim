"""RtspRouter: auth Digest, reescrita de URL/Content-Base, forca TCP, bloqueio por tentativas."""
import asyncio
import re
import threading
import time

import pytest

from api.digest_auth import DigestAuthenticator, client_digest_header
from core.device_manager import build_profile
from core.security import LoginLimiter, UserStore
from streaming.rtsp_router import RtspRouter, Upstream, read_message

PW = "Str0ngPassw0rd9xQ"
import itertools
_ports = itertools.count(28700, 2)
P = U = 0
SDP = ("v=0\r\no=- 0 0 IN IP4 127.0.0.1\r\ns=x\r\nt=0 0\r\nm=video 0 RTP/AVP 96\r\na=rtpmap:96 H264/90000\r\n"
       "a=control:trackID=0\r\n")


@pytest.fixture()
def env(request):
    global P, U
    P = next(_ports)
    U = P + 1
    prof = build_profile(brand_choice="intelbras", mode="camera", camera_count=1, bind_ip="127.0.0.1", admin_password=PW)
    dev = prof.devices[0]
    dev.rtsp_port = P
    us = UserStore(dev.users, realms=[dev.realm])
    seen: list[str] = []
    ready = threading.Event()
    loop = asyncio.new_event_loop()

    async def up(reader, writer):
        while True:
            m = await read_message(reader)
            if m is None:
                break
            seen.append(m.start_line)
            cs, meth = m.get("CSeq"), m.start_line.split()[0]
            base = f"rtsp://127.0.0.1:{U}/" + m.start_line.split()[1].split("/")[3].split("?")[0]
            h = f"RTSP/1.0 200 OK\r\nCSeq: {cs}\r\n"
            if meth == "DESCRIBE":
                b = SDP.encode()
                writer.write((h + f"Content-Base: {base}/\r\nContent-Type: application/sdp\r\nContent-Length: {len(b)}\r\n\r\n").encode() + b)
            elif meth == "SETUP":
                writer.write((h + "Session: s1\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n\r\n").encode())
            else:
                writer.write((h + "\r\n").encode())
            await writer.drain()

    def run():
        asyncio.set_event_loop(loop)
        auth = DigestAuthenticator(dev.realm, us.digest_ha1)
        r = RtspRouter(dev, auth, upstream=Upstream("127.0.0.1", U), limiter=LoginLimiter(3, 60, 60),
                       require_auth=lambda: not getattr(request, "param", False))
        loop.run_until_complete(asyncio.start_server(up, "127.0.0.1", U))
        loop.run_until_complete(r.start("127.0.0.1"))
        ready.set()
        loop.run_forever()

    threading.Thread(target=run, daemon=True).start()
    ready.wait(5)
    yield dev, seen
    loop.call_soon_threadsafe(loop.stop)


def _talk(sock, req: str) -> str:
    sock.sendall(req.encode())
    time.sleep(0.3)
    return sock.recv(65536).decode()


def _auth_describe(sock, dev, uri_path, pw=PW, cseq=1):
    uri = f"rtsp://127.0.0.1:{P}{uri_path}"
    r = _talk(sock, f"DESCRIBE {uri} RTSP/1.0\r\nCSeq: {cseq}\r\n\r\n")
    assert r.startswith("RTSP/1.0 401")
    nonce = re.search(r'nonce="([^"]+)"', r).group(1)
    h = client_digest_header("admin", pw, dev.realm, nonce, "DESCRIBE", uri_path)
    return uri, _talk(sock, f"DESCRIBE {uri} RTSP/1.0\r\nCSeq: {cseq + 1}\r\nAuthorization: {h}\r\n\r\n")


def test_describe_rewrites_and_authenticates(env):
    import socket
    dev, seen = env
    s = socket.create_connection(("127.0.0.1", P))
    path = "/cam/realmonitor?channel=1&subtype=0"
    uri, r = _auth_describe(s, dev, path)
    assert r.startswith("RTSP/1.0 200")
    assert f"/{dev.id}_ch1_s0" in seen[0]
    assert "Authorization" not in seen[0]
    assert f"Content-Base: rtsp://127.0.0.1:{P}{path}" in r  # volta para a URL publica
    # SETUP so UDP -> 461 ; com TCP -> encaminhado
    r = _talk(s, f"SETUP {uri}/trackID=0 RTSP/1.0\r\nCSeq: 5\r\nTransport: RTP/AVP;unicast;client_port=5000-5001\r\n\r\n")
    assert " 461 " in r.split("\r\n")[0] + " "
    r = _talk(s, f"SETUP {uri}/trackID=0 RTSP/1.0\r\nCSeq: 6\r\nTransport: RTP/AVP;unicast;client_port=5000-5001,RTP/AVP/TCP;unicast;interleaved=0-1\r\n\r\n")
    assert r.startswith("RTSP/1.0 200") and "interleaved=0-1" in r
    assert seen[-1].startswith("SETUP") and seen[-1].endswith("/trackID=0 RTSP/1.0")
    s.close()


def test_wrong_password_and_lockout(env):
    import socket
    dev, _ = env
    for i in range(2):
        s = socket.create_connection(("127.0.0.1", P))
        _, r = _auth_describe(s, dev, "/cam/realmonitor?channel=1&subtype=0", pw="Wrong-Password-1")
        assert r.startswith("RTSP/1.0 401")
        s.close()
    s = socket.create_connection(("127.0.0.1", P))
    _, r = _auth_describe(s, dev, "/cam/realmonitor?channel=1&subtype=0", pw="Wrong-Password-1")
    assert r.startswith("RTSP/1.0 403") or r == ""  # 3a falha bloqueia
    s.close()


def test_unknown_path_404(env):
    import socket
    dev, _ = env
    s = socket.create_connection(("127.0.0.1", P))
    _, r = _auth_describe(s, dev, "/cam/realmonitor?channel=9&subtype=0")
    assert r.startswith("RTSP/1.0 404")


def test_rtsp_auth_toggle_urls():
    from core.urls import cred
    assert cred("", "") == ""
    assert cred("admin", "a b") == "admin:a%20b@"


@pytest.mark.parametrize("env", [True], indirect=True)
def test_rtsp_without_auth_when_disabled(env):
    import socket
    dev, seen = env
    s = socket.create_connection(("127.0.0.1", P))
    uri = f"rtsp://127.0.0.1:{P}/cam/realmonitor?channel=1&subtype=0"
    r = _talk(s, f"DESCRIBE {uri} RTSP/1.0\r\nCSeq: 1\r\n\r\n")
    assert r.startswith("RTSP/1.0 200")
    s.close()


@pytest.mark.parametrize("env", [True], indirect=True)
def test_rtsp_noauth_options_then_describe(env):
    import socket
    dev, seen = env
    s = socket.create_connection(("127.0.0.1", P))
    uri = f"rtsp://127.0.0.1:{P}/cam/realmonitor?channel=1&subtype=0"
    r = _talk(s, f"OPTIONS {uri} RTSP/1.0\r\nCSeq: 1\r\nUser-Agent: LibVLC\r\n\r\n")
    print("OPTIONS->", r)
    assert r.startswith("RTSP/1.0 200")
    r = _talk(s, f"DESCRIBE {uri} RTSP/1.0\r\nCSeq: 2\r\nAccept: application/sdp\r\n\r\n")
    print("DESCRIBE->", r[:200])
    assert r.startswith("RTSP/1.0 200")
    r = _talk(s, f"SETUP {uri}/trackID=0 RTSP/1.0\r\nCSeq: 3\r\nTransport: RTP/AVP/TCP;unicast;interleaved=0-1\r\n\r\n")
    assert r.startswith("RTSP/1.0 200")
    s.close()
