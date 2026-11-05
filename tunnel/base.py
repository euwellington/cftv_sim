"""TunnelProvider: processo externo (cloudflared/ngrok/tailscale) que publica o portal web."""
from __future__ import annotations

import logging
import re
import subprocess
import threading
import time
from typing import Optional

from core import config
from core.event_bus import EventType

log = logging.getLogger(__name__)
URL_RE = re.compile(r"https://[A-Za-z0-9.-]+")


class TunnelError(RuntimeError):
    pass


class TunnelProvider:
    name = "tunnel"
    binary = ""
    install_hint = ""

    def __init__(self, ctx) -> None:  # noqa: ANN001
        self.ctx = ctx
        self.proc: Optional[subprocess.Popen] = None
        self.url = ""
        self.local_port = ctx.profile.web_port

    # ------------------------------------------------------------------ util
    def log_line(self, line: str) -> None:
        line = line.rstrip()
        if line:
            self.ctx.bus.publish(EventType.TUNNEL_LOG, line=line, provider=self.name)

    def status(self, status: str, **kw) -> None:  # noqa: ANN003
        self.ctx.bus.publish(EventType.TUNNEL_STATUS, status=status, provider=self.name, url=self.url, **kw)

    def find_binary(self) -> str:
        p = config.bin_path(self.binary)
        if p:
            return str(p)
        for extra in self.extra_paths():
            import os
            if os.path.exists(extra):
                return extra
        raise TunnelError(f"'{self.binary}' nao encontrado. {self.install_hint}")

    def extra_paths(self) -> list[str]:
        return []

    def spawn(self, cmd: list[str]) -> subprocess.Popen:
        self.log_line("$ " + " ".join(c if "token" not in c.lower() else "***" for c in cmd))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                creationflags=flags)

    def pump(self, proc: subprocess.Popen, on_line=None) -> None:  # noqa: ANN001
        def run() -> None:
            for line in proc.stdout:  # type: ignore[union-attr]
                self.log_line(line)
                if on_line:
                    on_line(line)
            self.log_line("processo encerrado")
            if self.proc is proc and self.url:
                self.url = ""
                self.ctx.public_url = ""
                self.status("stopped")
        threading.Thread(target=run, daemon=True, name=f"tunnel-{self.name}").start()

    def wait_for(self, predicate, timeout: float, what: str) -> str:  # noqa: ANN001
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self.proc is not None and self.proc.poll() is not None:
                raise TunnelError(f"{self.binary} encerrou (codigo {self.proc.returncode}); veja o log acima")
            v = predicate()
            if v:
                return v
            time.sleep(0.4)
        self.stop()
        raise TunnelError(f"tempo esgotado esperando {what}")

    # ------------------------------------------------------------------ API
    def start(self) -> str:
        raise NotImplementedError

    def stop(self) -> None:
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(timeout=4)
            except subprocess.TimeoutExpired:
                p.kill()
        self.url = ""
        self.status("stopped")
