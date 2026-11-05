"""URLs de acesso (HTTP e RTSP) com usuario e senha reais, prontas para copiar.

Usuario e senha sao codificados (``%23`` para ``#`` etc.), senao o cliente (VLC, ffmpeg, navegador)
quebra a URL em senhas geradas com simbolos.
"""
from __future__ import annotations

from urllib.parse import quote

from .models import Brand, DeviceSpec


def cred(user: str, password: str) -> str:
    if not user:
        return ""
    return f"{quote(user, safe='')}:{quote(password, safe='')}@" if password else f"{quote(user, safe='')}@"


def rtsp_path(spec: DeviceSpec, channel: int, stream: int) -> str:
    if spec.brand is Brand.HIKVISION:
        return f"Streaming/Channels/{channel * 100 + stream + 1}"
    return f"cam/realmonitor?channel={channel}&subtype={stream}"


def rtsp_url(spec: DeviceSpec, channel: int, stream: int, host: str, user: str, password: str,
             port: int | None = None) -> str:
    return f"rtsp://{cred(user, password)}{host}:{port or spec.rtsp_port}/{rtsp_path(spec, channel, stream)}"


def http_url(spec: DeviceSpec, host: str, user: str, password: str, port: int | None = None) -> str:
    return f"http://{cred(user, password)}{host}:{port or spec.http_port}/"


def portal_url(host: str, port: int, user: str, password: str) -> str:
    return f"http://{cred(user, password)}{host}:{port}/"
