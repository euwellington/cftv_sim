"""Cloudflare quick tunnel: ``cloudflared tunnel --url http://127.0.0.1:PORTA`` (URL *.trycloudflare.com)."""
from __future__ import annotations

from .base import URL_RE, TunnelProvider


class CloudflareTunnel(TunnelProvider):
    name = "cloudflare"
    binary = "cloudflared"
    install_hint = "Rode tools/fetch_binaries.py ou instale: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"

    def start(self) -> str:
        exe = self.find_binary()
        self.status("starting")
        self.proc = self.spawn([exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.local_port}"])
        found: list[str] = []

        def on_line(line: str) -> None:
            for m in URL_RE.findall(line):
                if "trycloudflare.com" in m and not found:
                    found.append(m)
        self.pump(self.proc, on_line)
        self.url = self.wait_for(lambda: found[0] if found else "", 45, "a URL do Cloudflare")
        self.status("running")
        return self.url
