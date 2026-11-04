"""OSD (on-screen display) no estilo de cada fabricante.

* Hikvision: data/hora no canto superior esquerdo ``MM-DD-AAAA Ddd HH:MM:SS``
  e nome do canal no canto inferior direito.
* Intelbras: data/hora no canto superior direito ``DD/MM/AAAA HH:MM:SS`` e
  titulo no canto inferior esquerdo.
* Selo ``CAM N | MARCA | MODELO`` e indicador ``REC`` piscando.

Os formatos exatos de cada marca vem de conhecimento proprio (nao confirmados
em fonte oficial): ficam centralizados aqui para ajuste facil.
A fonte Hershey do OpenCV so desenha ASCII, entao acentos sao removidos.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import datetime

import cv2
import numpy as np

WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")  # independente de locale
FONT = cv2.FONT_HERSHEY_SIMPLEX
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
RED = (40, 40, 235)  # BGR


def ascii_only(text: str) -> str:
    """Remove acentos e caracteres nao ASCII (a fonte Hershey nao os desenha)."""
    norm = unicodedata.normalize("NFKD", text)
    return "".join(c for c in norm if ord(c) < 128)


def hikvision_datetime(dt: datetime) -> str:
    """Ex.: ``11-04-2026 Wed 19:31:05``."""
    return f"{dt:%m-%d-%Y} {WEEKDAYS[dt.weekday()]} {dt:%H:%M:%S}"


def intelbras_datetime(dt: datetime) -> str:
    """Ex.: ``04/11/2026 19:31:05``."""
    return f"{dt:%d/%m/%Y %H:%M:%S}"


@dataclass
class OsdStyle:
    brand: str  # "hikvision" | "intelbras"
    channel: int
    channel_name: str
    model: str
    show_datetime: bool = True
    show_name: bool = True
    show_badge: bool = True


def draw_text(img: np.ndarray, text: str, org: tuple[int, int], scale: float,
              color: tuple[int, int, int] = WHITE, thickness: int = 2) -> tuple[int, int]:
    """Texto com contorno preto. Retorna (largura, altura) do texto."""
    (w, h), base = cv2.getTextSize(text, FONT, scale, thickness)
    cv2.putText(img, text, org, FONT, scale, BLACK, thickness + 3, cv2.LINE_AA)
    cv2.putText(img, text, org, FONT, scale, color, thickness, cv2.LINE_AA)
    return w, h + base


def _measure(text: str, scale: float, thickness: int) -> tuple[int, int]:
    (w, h), base = cv2.getTextSize(text, FONT, scale, thickness)
    return w, h + base


def render_osd(frame: np.ndarray, style: OsdStyle, now: datetime, recording: bool = False) -> np.ndarray:
    """Desenha o OSD **in-place** em ``frame`` (que deve ser uma copia propria)."""
    h, w = frame.shape[:2]
    scale = max(0.4, h / 720.0 * 0.75)
    th = max(1, int(round(scale * 2)))
    m = int(14 * h / 720.0) + 6  # margem
    hik = style.brand == "hikvision"

    if style.show_datetime:
        txt = hikvision_datetime(now) if hik else intelbras_datetime(now)
        tw, tht = _measure(txt, scale, th)
        x = m if hik else w - tw - m
        draw_text(frame, txt, (x, m + tht - 6), scale, WHITE, th)

    if style.show_name:
        name = ascii_only(style.channel_name) if hik else ascii_only(style.channel_name).upper()
        tw, _ = _measure(name, scale, th)
        x = w - tw - m if hik else m
        draw_text(frame, name, (x, h - m), scale, WHITE, th)

    if style.show_badge:
        badge = f"CAM {style.channel} | {style.brand.upper()} | {ascii_only(style.model)}"
        bs = scale * 0.7
        bth = max(1, int(round(bs * 2)))
        bw, bh = _measure(badge, bs, bth)
        bx = (w - bw) // 2
        by = h - m
        overlay = frame[by - bh - 4:by + 6, bx - 8:bx + bw + 8]
        if overlay.size:
            overlay[:] = (overlay * 0.45).astype(np.uint8)  # faixa escurecida atras do selo
        draw_text(frame, badge, (bx, by), bs, (220, 220, 220), bth)

    if recording and int(now.timestamp() * 2) % 2 == 0:  # pisca a 1 Hz
        r = max(6, int(8 * h / 720.0))
        rx = (w - 3 * r - m - _measure("REC", scale, th)[0]) if hik else (m + r)
        ry = m + r + (2 if hik else _measure("00", scale, th)[1] + 8)
        cv2.circle(frame, (rx, ry), r, RED, -1, cv2.LINE_AA)
        draw_text(frame, "REC", (rx + r + 6, ry + r - 1), scale * 0.9, WHITE, th)
    return frame


def render_rebooting(frame: np.ndarray, remaining_s: float, name: str = "") -> np.ndarray:
    """Tela escura com "Rebooting..." (ASCII) e contagem regressiva, in-place."""
    h, w = frame.shape[:2]
    frame[:] = (frame * 0.12).astype(np.uint8)
    scale = max(0.6, h / 720.0 * 1.4)
    th = max(2, int(scale * 2))
    txt = "Rebooting..."
    tw, tht = _measure(txt, scale, th)
    cv2.putText(frame, txt, ((w - tw) // 2, h // 2), FONT, scale, WHITE, th, cv2.LINE_AA)
    sub = f"{ascii_only(name)}  {int(remaining_s) + 1}s"
    sw, _ = _measure(sub, scale * 0.5, max(1, th // 2))
    cv2.putText(frame, sub, ((w - sw) // 2, h // 2 + tht + 10), FONT, scale * 0.5, (180, 180, 180),
                max(1, th // 2), cv2.LINE_AA)
    return frame
