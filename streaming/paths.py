"""Resolucao de URLs RTSP publicas de cada marca para o path interno do MediaMTX.

Hikvision:  /Streaming/Channels/101  (canal*100 + stream; 101 = canal 1 principal, 102 = sub)
            /ISAPI/Streaming/Channels/101,  legado /h264/ch1/main/av_stream (ou sub)
            playback: /Streaming/tracks/101?starttime=20261104T190000Z&endtime=...  (UTC)
Intelbras:  /cam/realmonitor?channel=1&subtype=0   (canal a partir de 1; subtype 0 = principal)
            playback: /cam/playback?channel=1&starttime=2026_11_04_19_00_00&endtime=...  (hora local)

Clientes anexam ``/trackID=N`` depois do path (Hikvision) ou depois da query (Dahua/Intelbras);
esse sufixo e preservado.  Formatos exatos: conhecimento proprio, confira com seu cliente.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import unquote

from core.fmt import local_to_utc
from core.models import Brand, DeviceSpec

_HIK_CH = re.compile(r"^/(?:ISAPI/)?Streaming/Channels/(\d+)(?P<rest>/.*)?$", re.I)
_HIK_TRACKS = re.compile(r"^/(?:ISAPI/)?Streaming/tracks/(\d+)(?P<rest>/.*)?$", re.I)
_HIK_LEGACY = re.compile(r"^/h264/ch(\d+)/(main|sub)/av_stream(?P<rest>/.*)?$", re.I)
_QS_DIGITS = re.compile(r"(?P<key>channel|subtype)=(?P<val>\d+)", re.I)


@dataclass
class Resolved:
    kind: str  # live | playback
    channel: int
    stream: int  # 0 principal, 1 extra
    suffix: str = ""  # ex.: "/trackID=0"
    start_utc: Optional[datetime] = None
    end_utc: Optional[datetime] = None


def internal_path(device_id: str, channel: int, stream: int) -> str:
    return f"{device_id}_ch{channel}_s{stream}"


def parse_hik_time(s: str) -> datetime:
    """``20261104T190000Z`` -> UTC naive."""
    return datetime.strptime(s.rstrip("Z"), "%Y%m%dT%H%M%S")


def parse_intelbras_time(s: str) -> datetime:
    """``2026_11_04_19_00_00`` (hora local) -> UTC naive."""
    return local_to_utc(datetime.strptime(s, "%Y_%m_%d_%H_%M_%S"))


def _split_query_suffix(query: str) -> tuple[dict[str, str], str]:
    """Separa ``a=1&b=2/trackID=0`` em ({a:1, b:2}, "/trackID=0")."""
    suffix = ""
    if "/" in query:
        idx = query.index("/")
        query, suffix = query[:idx], query[idx:]
    params: dict[str, str] = {}
    for part in query.split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            params[k.lower()] = unquote(v)
    return params, suffix


def resolve(spec: DeviceSpec, path: str, query: str) -> Optional[Resolved]:
    """Traduz path+query publicos em ``Resolved`` (ou ``None`` se desconhecido)."""
    n_ch = len(spec.channels)
    if spec.brand is Brand.HIKVISION:
        m = _HIK_CH.match(path)
        if m:
            n = int(m.group(1))
            ch, s = (n // 100, n % 100) if n >= 100 else (n, 1)
            if 1 <= ch <= n_ch and s in (1, 2):
                return Resolved("live", ch, s - 1, m.group("rest") or "")
            return None
        m = _HIK_LEGACY.match(path)
        if m:
            ch = int(m.group(1))
            if 1 <= ch <= n_ch:
                return Resolved("live", ch, 0 if m.group(2).lower() == "main" else 1, m.group("rest") or "")
            return None
        m = _HIK_TRACKS.match(path)
        if m:
            n = int(m.group(1))
            ch, s = n // 100, n % 100
            params, suffix = _split_query_suffix(query)
            try:
                a, b = parse_hik_time(params["starttime"]), parse_hik_time(params["endtime"])
            except (KeyError, ValueError):
                return None
            if 1 <= ch <= n_ch:
                return Resolved("playback", ch, max(0, s - 1), (m.group("rest") or "") + suffix, a, b)
        return None
    # Intelbras / Dahua
    p = path.rstrip("/").lower()
    if p in ("/cam/realmonitor", "/cam/playback"):
        params, suffix = _split_query_suffix(query)
        try:
            ch = int(params.get("channel", "1"))
        except ValueError:
            return None
        if not (1 <= ch <= n_ch):
            return None
        if p.endswith("realmonitor"):
            stream = int(params.get("subtype", "0") or 0)
            if stream not in (0, 1):
                return None
            return Resolved("live", ch, stream, suffix)
        try:
            a, b = parse_intelbras_time(params["starttime"]), parse_intelbras_time(params["endtime"])
        except (KeyError, ValueError):
            return None
        return Resolved("playback", ch, 0, suffix, a, b)
    return None


def public_live_url(spec: DeviceSpec, host: str, channel: int, stream: int, user: str = "", password: str = "") -> str:
    """URL RTSP ao vivo publica (para GUI/portal/ONVIF)."""
    cred = f"{user}:{password}@" if user else ""
    if spec.brand is Brand.HIKVISION:
        return f"rtsp://{cred}{host}:{spec.rtsp_port}/Streaming/Channels/{channel * 100 + stream + 1}"
    return f"rtsp://{cred}{host}:{spec.rtsp_port}/cam/realmonitor?channel={channel}&subtype={stream}"
