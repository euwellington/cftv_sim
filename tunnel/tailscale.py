"""Tailscale Funnel: ``tailscale funnel --bg PORTA`` e URL ``https://<maquina>.<tailnet>.ts.net``."""
from __future__ import annotations

import json
import os
import subprocess

from .base import TunnelError, TunnelProvider


class TailscaleTunnel(TunnelProvider):
    name = "tailscale"
    binary = "tailscale"
    install_hint = ("Instale o Tailscale (https://tailscale.com/download), faça login e habilite o Funnel "
                    "no painel admin (ACL 'funnel').")

    def extra_paths(self) -> list[str]:
        return [r"C:\Program Files\Tailscale\tailscale.exe", "/usr/bin/tailscale", "/usr/local/bin/tailscale",
                "/Applications/Tailscale.app/Contents/MacOS/Tailscale"]

    def _run(self, *args: str, timeout: int = 40) -> subprocess.CompletedProcess:
        exe = self.find_binary()
        self.log_line("$ tailscale " + " ".join(args))
        r = subprocess.run([exe, *args], capture_output=True, text=True, timeout=timeout,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for ln in (r.stdout + r.stderr).splitlines():
            self.log_line(ln)
        return r

    def _funnel(self, timeout: float = 25.0) -> tuple[int | None, str]:
        """Roda ``tailscale funnel --bg`` sem travar: se ficar esperando habilitacao, encerra e devolve a saida."""
        import threading
        import time
        exe = self.find_binary()
        self.log_line(f"$ tailscale funnel --bg {self.local_port}")
        proc = subprocess.Popen([exe, "funnel", "--bg", str(self.local_port)], stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True, bufsize=1, stdin=subprocess.DEVNULL,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        out: list[str] = []

        def rd() -> None:
            for ln in proc.stdout:  # type: ignore[union-attr]
                out.append(ln)
                self.log_line(ln)
        t = threading.Thread(target=rd, daemon=True)
        t.start()
        end = time.monotonic() + timeout
        while time.monotonic() < end and proc.poll() is None:
            time.sleep(0.3)
            if any("login.tailscale.com" in ln or "not enabled" in ln.lower() for ln in out):
                time.sleep(0.5)
                break
        code = proc.poll()
        if code is None:
            proc.kill()
        t.join(timeout=1)
        return code, "".join(out)

    def start(self) -> str:
        self.status("starting")
        st = self._run("status", "--json", timeout=15)
        if st.returncode != 0:
            raise TunnelError("Tailscale nao esta ativo/logado. Abra o app do Tailscale e conecte.")
        try:
            data = json.loads(st.stdout)
            dns = data["Self"]["DNSName"].rstrip(".")
        except (KeyError, ValueError) as exc:
            raise TunnelError("nao foi possivel ler o nome DNS do Tailscale (MagicDNS habilitado?)") from exc
        if not data.get("CertDomains"):
            raise TunnelError(
                "HTTPS nao esta habilitado no seu tailnet (CertDomains vazio). Abra https://login.tailscale.com/admin/dns, "
                "role ate 'HTTPS Certificates' e clique em 'Enable HTTPS'. Depois habilite o Funnel (abaixo) e tente de novo.")
        code, out = self._funnel()
        link = next((w for w in out.split() if w.startswith("https://login.tailscale.com")), "")
        if link or "not enabled" in out.lower() or code is None:
            raise TunnelError(
                "Funnel nao esta habilitado nesta conta/maquina. Abra este link, aprove e tente novamente: "
                + (link or "https://login.tailscale.com/admin/acls/file (adicione o atributo 'funnel' em nodeAttrs)"))
        if code != 0:
            raise TunnelError(f"tailscale funnel falhou: {out.strip()[:200]}")
        self.url = f"https://{dns}"
        self.status("running")
        return self.url

    def stop(self) -> None:
        try:
            self._run("funnel", "reset", timeout=15)
        except Exception:  # noqa: BLE001
            pass
        self.url = ""
        self.status("stopped")
