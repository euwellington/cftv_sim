"""Deteccao de portas ocupadas e sugestao de plano de portas por equipamento."""
from __future__ import annotations

import socket
from dataclasses import dataclass, asdict
from typing import Iterable

from .config import IS_WINDOWS, is_admin


def is_port_free(port: int, host: str = "0.0.0.0", proto: str = "tcp") -> bool:
    """True se for possivel fazer bind na porta (nao ocupada)."""
    if not (0 < port < 65536):
        return False
    kind = socket.SOCK_STREAM if proto == "tcp" else socket.SOCK_DGRAM
    with socket.socket(socket.AF_INET, kind) as s:
        if not IS_WINDOWS and proto == "tcp":
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def is_privileged(port: int) -> bool:
    """True se a porta exige privilegio elevado (Linux/macOS, < 1024, sem root)."""
    return (not IS_WINDOWS) and port < 1024 and not is_admin()


def find_free_port(start: int, host: str = "0.0.0.0", avoid: Iterable[int] = (), max_tries: int = 300) -> int:
    """Primeira porta livre >= ``start`` que nao esteja em ``avoid``."""
    avoid_set = set(avoid)
    for p in range(start, min(start + max_tries, 65535)):
        if p in avoid_set:
            continue
        if is_port_free(p, host):
            return p
    raise RuntimeError(f"nenhuma porta livre a partir de {start}")


@dataclass
class PortSet:
    """Portas de um equipamento. ``onvif`` 0 = ONVIF no proprio HTTP."""

    http: int
    rtsp: int
    sdk: int = 0  # 8000 (Hikvision) ou 37777 (Intelbras): somente listener
    onvif: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)

    def all_ports(self) -> list[int]:
        return [p for p in (self.http, self.rtsp, self.sdk, self.onvif) if p]


def suggest_port_plan(
    brands: list[str],
    host: str = "0.0.0.0",
    prefer_standard: bool = False,
    hik_http_start: int = 8080,
    intelbras_http_start: int = 8090,
    rtsp_start: int = 8554,
    reserved: Iterable[int] = (),
) -> list[PortSet]:
    """Plano de portas, um ``PortSet`` por equipamento, na ordem de ``brands``.

    * Cada equipamento recebe a propria porta HTTP e a propria porta RTSP
      (o ``RtspRouter`` abre uma por equipamento).
    * Com ``prefer_standard`` o primeiro equipamento tenta 80/554, desde que
      estejam livres e nao exijam privilegio.
    * A porta SDK (8000 / 37777) e so um listener que registra conexoes.
    """
    used: set[int] = set(reserved)
    out: list[PortSet] = []
    sdk_default = {"hikvision": 8000, "intelbras": 37777}
    http_next = {"hikvision": hik_http_start, "intelbras": intelbras_http_start}
    rtsp_next = rtsp_start
    first = True
    for brand in brands:
        http = rtsp = 0
        if prefer_standard and first:
            if 80 not in used and is_port_free(80, host) and not is_privileged(80):
                http = 80
            if 554 not in used and is_port_free(554, host) and not is_privileged(554):
                rtsp = 554
        if not http:
            http = find_free_port(http_next[brand], host, used)
            http_next[brand] = http + 1
        used.add(http)
        if not rtsp:
            rtsp = find_free_port(rtsp_next, host, used)
            rtsp_next = rtsp + 1
        used.add(rtsp)
        sdk = find_free_port(sdk_default[brand], host, used)
        used.add(sdk)
        out.append(PortSet(http=http, rtsp=rtsp, sdk=sdk))
        first = False
    return out


def check_conflicts(ports: Iterable[int], host: str = "0.0.0.0") -> dict[int, str]:
    """Mapa porta -> motivo para as portas com problema (ocupada ou privilegiada)."""
    bad: dict[int, str] = {}
    seen: set[int] = set()
    for p in ports:
        if p in seen:
            bad[p] = "porta repetida no plano"
            continue
        seen.add(p)
        if is_privileged(p):
            bad[p] = "exige privilegio de administrador (use 8080/8554)"
        elif not is_port_free(p, host):
            bad[p] = "porta ja esta em uso"
    return bad
