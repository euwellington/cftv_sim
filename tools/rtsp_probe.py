"""Teste cru de RTSP: mostra cada pedido/resposta. Uso:
    python tools/rtsp_probe.py IP PORTA /Streaming/Channels/101 [usuario senha]
Compare o resultado no IP local (192.168.x.x:8554) com o do tunel (IP:porta publicos)."""
from __future__ import annotations

import re
import socket
import sys
import time


def talk(s: socket.socket, req: str) -> str:
    s.sendall(req.encode())
    time.sleep(0.8)
    s.settimeout(3)
    try:
        return s.recv(65536).decode(errors="replace")
    except OSError as exc:
        return f"<sem resposta: {exc}>"


def main() -> None:
    host, port, path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    user = sys.argv[4] if len(sys.argv) > 4 else ""
    pw = sys.argv[5] if len(sys.argv) > 5 else ""
    uri = f"rtsp://{host}:{port}{path}"
    s = socket.create_connection((host, port), timeout=8)
    print(">>> conectou")
    for n, (m, extra) in enumerate((("OPTIONS", ""), ("DESCRIBE", "Accept: application/sdp\r\n")), start=1):
        r = talk(s, f"{m} {uri} RTSP/1.0\r\nCSeq: {n}\r\nUser-Agent: probe\r\n{extra}\r\n")
        print(f">>> {m}\n{r}")
        if r.startswith("RTSP/1.0 401") and user:
            import hashlib
            realm = re.search(r'realm="([^"]+)"', r).group(1)
            nonce = re.search(r'nonce="([^"]+)"', r).group(1)
            md = lambda x: hashlib.md5(x.encode()).hexdigest()  # noqa: E731
            resp = md(f"{md(f'{user}:{realm}:{pw}')}:{nonce}:{md(f'{m}:{path}')}")
            hdr = f'Digest username="{user}", realm="{realm}", nonce="{nonce}", uri="{path}", response="{resp}"'
            r = talk(s, f"{m} {uri} RTSP/1.0\r\nCSeq: {n + 10}\r\nAuthorization: {hdr}\r\n\r\n")
            print(f">>> {m} (com senha)\n{r}")
    s.close()


if __name__ == "__main__":
    main()
