"""Formatadores e utilitarios de rede/tempo usados pela GUI e pelas APIs."""
from __future__ import annotations

import socket
from datetime import datetime, timezone

import psutil


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def human_duration(seconds: float) -> str:
    s = int(max(0, seconds))
    d, s = divmod(s, 86400)
    h, s = divmod(s, 3600)
    m, s = divmod(s, 60)
    if d:
        return f"{d}d {h:02d}:{m:02d}:{s:02d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def utc_to_local(dt: datetime) -> datetime:
    """UTC (naive ou aware) -> horario local (naive)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone().replace(tzinfo=None)


def local_to_utc(dt: datetime) -> datetime:
    """Horario local (naive) -> UTC naive."""
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def local_ips() -> list[str]:
    """IPv4 das interfaces ativas (sem loopback)."""
    out: list[str] = []
    try:
        for addrs in psutil.net_if_addrs().values():
            for a in addrs:
                if a.family == socket.AF_INET and not a.address.startswith("127.") \
                        and not a.address.startswith("169.254."):
                    out.append(a.address)
    except Exception:  # noqa: BLE001
        pass
    return out


def primary_ip() -> str:
    """IP LAN principal (ou 127.0.0.1)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # nao envia pacote algum
            return s.getsockname()[0]
    except Exception:  # noqa: BLE001
        ips = local_ips()
        return ips[0] if ips else "127.0.0.1"
