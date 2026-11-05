"""Pinggy (TCP, sem conta): ``ssh -p 443 -R0:localhost:PORTA tcp@free.pinggy.io`` (nao exige instalar nada alem do ssh).

Um processo ssh por porta. Sessoes gratuitas podem ter limite de tempo; reinicie o tunel se cair.
"""
from __future__ import annotations

import re
import shutil
import subprocess

from .base import TunnelError
from .bore import BoreTunnel

ADDR_RE = re.compile(r"([A-Za-z0-9\-.]+\.pinggy\.(?:link|io|online)):(\d+)")
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


class PinggyTunnel(BoreTunnel):
    name = "pinggy"
    binary = "ssh"
    install_hint = "Instale o cliente OpenSSH (sudo apt install openssh-client)."

    def find_binary(self) -> str:
        p = shutil.which("ssh")
        if not p:
            raise TunnelError("ssh nao encontrado. " + self.install_hint)
        return p

    def _open_one(self, exe: str, port: int, out: dict[int, tuple[str, int]]) -> None:
        cmd = [exe, "-T", "-p", "443", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
               "-o", "ServerAliveInterval=30", "-o", "ExitOnForwardFailure=yes", "-o", "BatchMode=yes",
               f"-R0:localhost:{port}", "tcp@free.pinggy.io"]
        proc = self.spawn(cmd)
        self.procs.append(proc)

        def on_line(line: str) -> None:
            m = ADDR_RE.search(ANSI.sub("", line))
            if m and port not in out:
                out[port] = (self._ipv4(m.group(1)), int(m.group(2)))
        self.pump(proc, on_line)

    def start(self) -> str:
        return self._start_with("pinggy.io")
