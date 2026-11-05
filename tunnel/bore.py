"""bore (TCP, sem conta): abre as portas do simulador em ``bore.pub:PORTA`` para sistemas de fora da sua rede.

Um processo ``bore local <porta> --to bore.pub`` por porta (RTSP e HTTP de cada equipamento + portal).
Os enderecos publicos (host:porta) ficam em ``ctx.tcp_endpoints`` e aparecem na barra de URLs
(Endereco > Tunel TCP). O servidor ``bore.pub`` e publico e gratuito: qualquer um que saiba o endereco
alcanca a porta, entao a protecao e a autenticacao Digest + bloqueio por tentativas do simulador.
"""
from __future__ import annotations

import re
import subprocess
import sys
import threading
import time

from core import config

from .base import TunnelError, TunnelProvider

SERVER = "bore.pub"
LISTEN_RE = re.compile(r"listening at\s+([\w.\-]+):(\d+)")


class BoreTunnel(TunnelProvider):
    name = "bore"
    binary = "bore"
    install_hint = "Rode: python tools/fetch_binaries.py --only bore"

    def __init__(self, ctx) -> None:  # noqa: ANN001
        super().__init__(ctx)
        self.procs: list[subprocess.Popen] = []

    def find_binary(self) -> str:
        p = config.bin_path("bore")
        if p:
            return str(p)
        self.log_line("bore nao encontrado: baixando automaticamente...")
        try:
            sys.path.insert(0, str(config.ROOT))
            from tools import fetch_binaries
            fetch_binaries.BIN.mkdir(exist_ok=True)
            fetch_binaries.fetch_bore(False)
        except Exception as exc:  # noqa: BLE001
            raise TunnelError(f"nao foi possivel baixar o bore ({exc}). {self.install_hint}") from exc
        p = config.bin_path("bore")
        if not p:
            raise TunnelError("bore nao encontrado apos o download. " + self.install_hint)
        return str(p)

    def _ports(self) -> list[int]:
        prof = self.ctx.profile
        ports = [d.rtsp_port for d in prof.devices] + [d.http_port for d in prof.devices] + [prof.web_port]
        return list(dict.fromkeys(ports))

    @staticmethod
    def _ipv4(host: str) -> str:
        """Endereco IPv4 numerico do servidor (formularios de VMS costumam pedir IPv4)."""
        import socket
        try:
            return socket.gethostbyname(host)
        except OSError:
            return host

    def _open_one(self, exe: str, port: int, out: dict[int, tuple[str, int]]) -> None:
        proc = self.spawn([exe, "local", str(port), "--to", SERVER])
        self.procs.append(proc)

        def on_line(line: str) -> None:
            m = LISTEN_RE.search(line)
            if m:
                out[port] = (self._ipv4(m.group(1)), int(m.group(2)))
        self.pump(proc, on_line)

    def start(self) -> str:
        try:
            return self._start_with(SERVER)
        except TunnelError as first:
            self.log_line(f"bore falhou ({first}). Tentando Pinggy (ssh, sem conta)...")
            from .pinggy import PinggyTunnel
            alt = PinggyTunnel(self.ctx)
            alt.procs = self.procs
            try:
                url = alt.start()
            except TunnelError as second:
                raise TunnelError(f"bore: {first} | pinggy: {second}") from second
            self.procs = alt.procs
            self.url = url
            return url

    def _start_with(self, server: str) -> str:
        exe = self.find_binary()
        self.status("starting")
        eps: dict[int, tuple[str, int]] = {}
        ports = self._ports()
        for p in ports:
            self._open_one(exe, p, eps)
        end = time.monotonic() + 30
        while time.monotonic() < end and len(eps) < len(ports):
            if all(pr.poll() is not None for pr in self.procs):
                break
            time.sleep(0.3)
        if not eps:
            self.stop()
            raise TunnelError(f"nao consegui abrir o tunel em {server} (sem internet ou servidor indisponivel?)")
        self.ctx.tcp_endpoints = eps
        web = eps.get(self.ctx.profile.web_port)
        self.url = f"http://{web[0]}:{web[1]}" if web else server
        first_host = next(iter(eps.values()))[0]
        self.log_line(f"Servidor do tunel = {first_host} (use este IPv4 e as portas abaixo no sistema de fora)")
        self.log_line("Enderecos publicos: " + ", ".join(f"{lp} -> {h}:{rp}" for lp, (h, rp) in sorted(eps.items())))
        missing = [p for p in ports if p not in eps]
        if missing:
            self.log_line(f"aviso: sem tunel para as portas {missing}")
        self.status("running")
        threading.Thread(target=self._selftest, args=(dict(eps),), daemon=True).start()
        return self.url

    @staticmethod
    def _rtsp_exchange(host: str, port: int, uri: str, timeout: float = 8.0) -> str:
        """OPTIONS + DESCRIBE crus; devolve um resumo das respostas (para diagnostico no log do tunel)."""
        import socket
        out: list[str] = []
        try:
            s = socket.create_connection((host, port), timeout=timeout)
        except OSError as exc:
            return f"nao conectou: {exc}"
        try:
            s.settimeout(timeout)
            for n, (m, extra) in enumerate((("OPTIONS", ""), ("DESCRIBE", "Accept: application/sdp\r\n")), start=1):
                s.sendall(f"{m} {uri} RTSP/1.0\r\nCSeq: {n}\r\nUser-Agent: cftv-selftest\r\n{extra}\r\n".encode())
                try:
                    data = s.recv(65536).decode(errors="replace")
                except OSError as exc:
                    out.append(f"{m}: sem resposta ({exc})")
                    break
                if not data:
                    out.append(f"{m}: conexao fechada pelo servidor")
                    break
                head = data.split("\r\n")
                srv = next((h for h in head if h.lower().startswith("server:")), "")
                out.append(f"{m}: {head[0]} {srv}".strip())
        finally:
            s.close()
        return " | ".join(out)

    def _selftest(self, eps: dict[int, tuple[str, int]]) -> None:
        """Testa o RTSP pelo endereco publico do tunel e pela porta local e escreve o resultado no log."""
        try:
            from core.urls import rtsp_path
            dev = self.ctx.profile.devices[0]
            path = "/" + rtsp_path(dev, 1, 0)
            time.sleep(1.5)
            if dev.rtsp_port in eps:
                host, port = eps[dev.rtsp_port]
                self.log_line(f"[teste] RTSP local 127.0.0.1:{dev.rtsp_port} -> "
                              + self._rtsp_exchange("127.0.0.1", dev.rtsp_port, f"rtsp://127.0.0.1:{dev.rtsp_port}{path}"))
                self.log_line(f"[teste] RTSP via tunel {host}:{port} -> "
                              + self._rtsp_exchange(host, port, f"rtsp://{host}:{port}{path}"))
        except Exception as exc:  # noqa: BLE001
            self.log_line(f"[teste] falhou: {exc}")

    def stop(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs.clear()
        self.proc = None
        self.url = ""
        self.ctx.tcp_endpoints = {}
        self.status("stopped")
