"""ngrok: portal por HTTPS e, opcionalmente, RTSP por TCP (``ngrok_tcp_rtsp``).

RTSP nao passa por tunel HTTP; o ngrok oferece enderecos TCP (``tcp://0.tcp.sa.ngrok.io:12345``),
exigidos conta com cartao verificado.  Com TCP ativo, usa um arquivo de configuracao proprio com
todos os tunneis em uma unica sessao (o plano gratis permite apenas uma sessao do agente).
"""
from __future__ import annotations

import json
import re
import subprocess
import urllib.request
from pathlib import Path

from core import config

from .base import TunnelError, TunnelProvider


# Authtoken fixo (usado quando o perfil nao tem um valido). ATENCAO: nao suba este arquivo para um repo publico.
DEFAULT_AUTHTOKEN = "3FT8AOhbTzFSfzlhkAz7WqL3aNS_3vRnSXPVyE2QMQLy8BZkH"
TOKEN_RE = re.compile(r"^[A-Za-z0-9]{8,}_[A-Za-z0-9]{10,}$")


def valid_token(tok: str) -> bool:
    """Authtokens do ngrok tem o formato ``<id>_<segredo>`` sem espacos."""
    return bool(TOKEN_RE.match(tok.strip()))


class NgrokTunnel(TunnelProvider):
    name = "ngrok"
    binary = "ngrok"
    install_hint = "Instale em https://ngrok.com/download e configure o authtoken (ngrok config add-authtoken ...)."

    def _api(self) -> list[dict]:
        try:
            with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=1.5) as r:
                return json.load(r).get("tunnels", [])
        except Exception:  # noqa: BLE001
            return []

    def _default_config(self, exe: str) -> str:
        try:
            r = subprocess.run([exe, "config", "check"], capture_output=True, text=True, timeout=10)
            m = re.search(r"at\s+(.+?)\s*$", (r.stdout + r.stderr).strip(), re.M)
            return m.group(1).strip() if m else ""
        except Exception:  # noqa: BLE001
            return ""

    def _token(self) -> str:
        tok = (self.ctx.profile.public.ngrok_authtoken or "").strip()
        if tok and valid_token(tok):
            return tok
        if tok:
            self.log_line("aviso: o authtoken do perfil nao tem formato valido; usando o fixo do codigo")
        return DEFAULT_AUTHTOKEN

    def _write_config(self) -> Path:
        lines = ['version: "2"']
        if self._token():
            lines.append(f"authtoken: {self._token()}")
        lines += ["tunnels:", "  portal:", "    proto: http", f"    addr: {self.local_port}"]
        for d in self.ctx.profile.devices:
            lines += [f"  rtsp_{d.rtsp_port}:", "    proto: tcp", f"    addr: {d.rtsp_port}"]
        path = config.DATA_DIR / "ngrok.yml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        try:
            path.chmod(0o600)  # contem o authtoken
        except OSError:
            pass
        return path

    def _ready(self) -> bool:
        ts = self._api()
        want_tcp = self.ctx.profile.public.ngrok_tcp_rtsp
        has_http = any(t.get("public_url", "").startswith("https://") for t in ts)
        has_tcp = any(t.get("public_url", "").startswith("tcp://") for t in ts)
        return has_http and (has_tcp or not want_tcp)

    def start(self) -> str:
        exe = self.find_binary()
        self.status("starting")
        pub = self.ctx.profile.public
        if pub.ngrok_tcp_rtsp:
            cfg = self._write_config()
            cmd = [exe, "start", "--all", "--log", "stdout", "--config", str(cfg)]
            if not self._token():
                default = self._default_config(exe)
                if default:
                    cmd[-2:] = ["--config", default, "--config", str(cfg)]
        else:
            cmd = [exe, "http", str(self.local_port), "--log", "stdout"]
            if self._token():
                cmd += ["--authtoken", self._token()]
        self.proc = self.spawn(cmd)
        errors: list[str] = []
        self.pump(self.proc, lambda ln: errors.append(ln) if ("err=" in ln or "ERR_NGROK" in ln) else None)
        try:
            self.wait_for(self._ready, 45, "os tuneis do ngrok (authtoken configurado? TCP exige conta verificada)")
        except TunnelError as exc:
            hint = ""
            if any("ERR_NGROK_105" in e or "ERR_NGROK_107" in e for e in errors):
                hint = " Authtoken invalido: copie o correto em https://dashboard.ngrok.com/get-started/your-authtoken e rode 'ngrok config add-authtoken SEU_TOKEN' (ou limpe o campo no perfil)."
            if any("ERR_NGROK_8013" in e or "credit card" in e.lower() or "verif" in e.lower() for e in errors):
                hint = " TCP no ngrok exige conta com cartao verificado: https://dashboard.ngrok.com/settings#id-verification"
            raise TunnelError(str(exc) + hint) from exc
        eps: dict[int, tuple[str, int]] = {}
        for t in self._api():
            u = t.get("public_url", "")
            if u.startswith("https://"):
                self.url = u
            elif u.startswith("tcp://"):
                host, _, port = u[6:].partition(":")
                addr = t.get("config", {}).get("addr", "")
                m = re.search(r":(\d+)$", addr)
                if m and port.isdigit():
                    eps[int(m.group(1))] = (host, int(port))
        self.ctx.tcp_endpoints = eps
        self.status("running")
        return self.url
