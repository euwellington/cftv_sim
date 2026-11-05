import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from core import upnp

DESC = """<?xml version="1.0"?><root xmlns="urn:schemas-upnp-org:device-1-0"><device><serviceList><service>
<serviceType>urn:schemas-upnp-org:service:WANIPConnection:1</serviceType><controlURL>/ctl</controlURL></service></serviceList></device></root>"""
calls = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(DESC.encode())

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"])).decode()
        calls.append((self.headers["SOAPAction"], body))
        out = "<NewExternalIPAddress>100.70.1.2</NewExternalIPAddress>" if "GetExternalIP" in body else ""
        self.send_response(200); self.end_headers(); self.wfile.write(out.encode())


def test_open_ports_and_cgnat_detection():
    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    r = upnp.open_ports([8554, 8800], "192.168.18.233", location=f"http://127.0.0.1:{srv.server_port}/desc.xml")
    srv.shutdown()
    assert r["opened"] == [8554, 8800] and r["cgnat"] is True and r["external_ip"] == "100.70.1.2"
    assert any("AddPortMapping" in a and "<NewExternalPort>8554</NewExternalPort>" in b for a, b in calls)
    assert not upnp.is_cgnat_or_private("191.6.14.86")
