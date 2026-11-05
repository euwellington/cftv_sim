"""Modelos de dados (pydantic v2): perfil, equipamentos, canais e usuarios.

O perfil e salvo em ``config/profiles/<nome>.json``.  Senhas nunca sao
gravadas em texto: guardamos hash PBKDF2 (portal) e HA1 do Digest por realm.
"""
from __future__ import annotations

import random
import re
import string
from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Brand(str, Enum):
    HIKVISION = "hikvision"
    INTELBRAS = "intelbras"


class DeviceKind(str, Enum):
    IPCAM = "ipcam"
    NVR = "nvr"
    DVR = "dvr"


# ----------------------------------------------------------------------------- video
class VideoConfig(BaseModel):
    width: int = 1280
    height: int = 720
    fps: int = Field(25, ge=1, le=60)
    bitrate_kbps: int = Field(2048, ge=64, le=16384)
    codec: Literal["H.264", "H.265", "MJPEG"] = "H.264"
    bitrate_control: Literal["CBR", "VBR"] = "CBR"

    @property
    def resolution(self) -> str:
        return f"{self.width}x{self.height}"


def _default_main() -> VideoConfig:
    return VideoConfig(width=1280, height=720, fps=25, bitrate_kbps=2048)


def _default_sub() -> VideoConfig:
    return VideoConfig(width=640, height=360, fps=15, bitrate_kbps=512)


class MotionConfig(BaseModel):
    enabled: bool = True
    sensitivity: int = Field(60, ge=0, le=100)
    # regioes normalizadas (x, y, w, h) em 0..1; vazio = quadro inteiro
    regions: list[tuple[float, float, float, float]] = Field(default_factory=list)


class ChannelSpec(BaseModel):
    index: int = Field(1, ge=1, le=16)
    name: str = "Camera 01"
    osd_enabled: bool = True
    # variacoes para as cameras parecerem distintas
    mirror: bool = False
    zoom: float = Field(1.0, ge=1.0, le=4.0)
    hue_shift: int = Field(0, ge=-90, le=90)  # graus
    noise: float = Field(0.0, ge=0.0, le=30.0)
    main: VideoConfig = Field(default_factory=_default_main)
    sub: VideoConfig = Field(default_factory=_default_sub)
    motion: MotionConfig = Field(default_factory=MotionConfig)
    record_mode: Literal["auto", "manual", "off"] = "auto"


# Variacoes padrao por indice de canal (1..5)
CHANNEL_VARIATIONS: dict[int, dict] = {
    1: {},
    2: {"mirror": True},
    3: {"zoom": 1.3},
    4: {"hue_shift": 12, "noise": 4.0},
    5: {"mirror": True, "zoom": 1.15, "noise": 6.0},
}


# ----------------------------------------------------------------------------- usuarios
class UserRecord(BaseModel):
    username: str
    pw_hash: str = ""  # PBKDF2 (login do portal)
    ha1: dict[str, str] = Field(default_factory=dict)  # realm -> MD5(user:realm:senha) (Digest)
    role: Literal["admin", "operator", "viewer"] = "viewer"
    permissions: list[str] = Field(default_factory=list)
    must_change_password: bool = False
    enabled: bool = True


# ----------------------------------------------------------------------------- gravacao / disco
class RecordingConfig(BaseModel):
    mode: Literal["continuous", "motion", "off"] = "continuous"
    pre_event_s: int = 10
    post_event_s: int = 15
    segment_s: int = 60
    overwrite: bool = True
    # agenda: 7 dias x 24 horas (True = grava). Vazio = sempre.
    schedule: list[list[bool]] = Field(default_factory=list)


class DiskSpec(BaseModel):
    id: str = "hdd1"
    capacity_gb: float = Field(4.0, gt=0)
    status: Literal["ok", "full", "error"] = "ok"


# ----------------------------------------------------------------------------- equipamento
class DeviceSpec(BaseModel):
    id: str
    brand: Brand
    kind: DeviceKind
    family: str  # IPC, DS-76xxNI, DS-72xx, VIP, NVD, MHDX, iMHDX
    model: str
    name: str
    serial: str
    mac: str
    firmware: str
    firmware_build: str
    bind_ip: str = "0.0.0.0"
    http_port: int
    rtsp_port: int
    sdk_port: int = 0
    onvif_port: int = 0  # 0 = ONVIF no proprio HTTP
    onvif_enabled: bool = True
    discovery_enabled: bool = False
    legacy_firmware: bool = False  # Hikvision: habilita aliases PSIA
    allow_basic: bool = False  # aceitar HTTP Basic alem do Digest
    users: list[UserRecord] = Field(default_factory=list)
    channels: list[ChannelSpec] = Field(default_factory=list)
    recording: RecordingConfig = Field(default_factory=RecordingConfig)
    disks: list[DiskSpec] = Field(default_factory=lambda: [DiskSpec()])
    cloud_qr: str = ""  # Intelbras: apenas campo simulado "Cloud: desabilitado"

    @property
    def is_recorder(self) -> bool:
        return self.kind in (DeviceKind.NVR, DeviceKind.DVR)

    @property
    def realm(self) -> str:
        return default_realm(self)

    def channel(self, index: int) -> ChannelSpec:
        for c in self.channels:
            if c.index == index:
                return c
        raise KeyError(index)


class PublicAccess(BaseModel):
    enabled: bool = False
    provider: Literal["cloudflare", "ngrok", "tailscale", "bore", "pinggy"] = "cloudflare"
    ngrok_authtoken: str = ""
    expose_brand_apis: bool = False  # APIs ISAPI/CGI pela internet: desligado por padrao
    ngrok_tcp_rtsp: bool = False


class Profile(BaseModel):
    schema_version: int = 1
    name: str = "padrao"
    brand_choice: Literal["hikvision", "intelbras", "all"] = "hikvision"
    mode: Literal["camera", "recorder"] = "camera"
    created: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    devices: list[DeviceSpec] = Field(default_factory=list)
    public: PublicAccess = Field(default_factory=PublicAccess)
    web_port: int = 8800
    rtsp_auth: bool = True  # False => RTSP aberto, sem usuario/senha
    settings: dict = Field(default_factory=dict)  # sobrescritas de default.json

    @field_validator("name")
    @classmethod
    def _safe_name(cls, v: str) -> str:
        v = re.sub(r"[^A-Za-z0-9_.-]+", "_", v.strip())
        if not v:
            raise ValueError("nome de perfil vazio")
        return v

    def device(self, device_id: str) -> DeviceSpec:
        for d in self.devices:
            if d.id == device_id:
                return d
        raise KeyError(device_id)


# ----------------------------------------------------------------------------- catalogo de modelos
# (familia, modelo, canais maximos, firmware, build)
# Nomes de modelo Intelbras sao conhecimento proprio (nao confirmado): configuravel.
HIK_CAMERAS = [("IPC", "DS-2CD2143G2-I", 1, "V5.7.3", "build 220914")]
HIK_NVRS = [
    ("DS-76xxNI", "DS-7604NI-K1", 4, "V4.22.005", "build 191208"),
    ("DS-76xxNI", "DS-7608NI-I2/8P", 8, "V4.22.005", "build 191208"),
]
HIK_DVRS = [
    ("DS-72xx", "DS-7204HQHI-K1", 4, "V4.30.210", "build 200810"),
    ("DS-72xx", "DS-7208HQHI-K1", 8, "V4.30.210", "build 200810"),
]
INTELBRAS_CAMERAS = [("VIP", "VIP 3230 B", 1, "2.800.0000000.20.R", "build:2024-05-10")]
INTELBRAS_RECORDERS: dict[str, list[tuple[str, str, int, str, str]]] = {
    "NVD": [("NVD", "NVD 1404", 4, "4.001.0000000.1.R", "build:2024-05-10"),
            ("NVD", "NVD 1408", 8, "4.001.0000000.1.R", "build:2024-05-10")],
    "MHDX": [("MHDX", "MHDX 3004-C", 4, "4.001.0000000.1.R", "build:2024-05-10"),
             ("MHDX", "MHDX 1108", 8, "4.001.0000000.1.R", "build:2024-05-10")],
    "iMHDX": [("iMHDX", "iMHDX 3008", 8, "4.001.0000000.1.R", "build:2024-05-10"),
              ("iMHDX", "iMHDX 5116", 16, "4.001.0000000.1.R", "build:2024-05-10")],
}


def pick_model(brand: Brand, kind: DeviceKind, channels: int, family: str = "") -> tuple[str, str, int, str, str]:
    """Escolhe o menor modelo do catalogo que comporte ``channels`` canais."""
    if brand is Brand.HIKVISION:
        table = {DeviceKind.IPCAM: HIK_CAMERAS, DeviceKind.NVR: HIK_NVRS, DeviceKind.DVR: HIK_DVRS}[kind]
    elif kind is DeviceKind.IPCAM:
        table = INTELBRAS_CAMERAS
    else:
        table = INTELBRAS_RECORDERS[family or "NVD"]
    for row in table:
        if row[2] >= channels:
            return row
    return table[-1]


def default_realm(spec: DeviceSpec) -> str:
    """Realm do Digest por marca (ver comentarios: formatos nao confirmados em fonte oficial)."""
    if spec.brand is Brand.HIKVISION:
        if spec.is_recorder:
            return spec.model.split("/")[0]  # ex.: DS-7608NI-I2
        return f"IP Camera({spec.serial[-5:].upper()})"
    return f"Login to {spec.serial}"  # firmwares antigos: Device_<MAC sem ':'>


# ----------------------------------------------------------------------------- geradores
def _rng(seed: str) -> random.Random:
    return random.Random(seed)


def make_mac(brand: Brand, seed: str) -> str:
    """MAC localmente administrado (bit 0x02), para nao usar OUI de terceiros."""
    r = _rng("mac" + seed)
    prefix = "02:48:4b" if brand is Brand.HIKVISION else "02:49:42"
    return prefix + "".join(f":{r.randrange(256):02x}" for _ in range(3))


def make_serial(brand: Brand, model: str, seed: str, n: int = 1) -> str:
    """Serial plausivel. Hikvision: modelo+data+sufixo; Intelbras: 14 alfanumericos."""
    r = _rng("ser" + seed)
    today = datetime.now().strftime("%Y%m%d")
    if brand is Brand.HIKVISION:
        tail = "".join(r.choice(string.ascii_uppercase) for _ in range(4))
        return f"{model}{today}AAWR{tail}{n:04d}".replace(" ", "")
    return "".join(r.choice(string.ascii_uppercase + string.digits) for _ in range(14))


def make_device_id(brand: Brand, kind: DeviceKind, index: int) -> str:
    return f"{brand.value[:3]}{'cam' if kind is DeviceKind.IPCAM else kind.value}{index}"
