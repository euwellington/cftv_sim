"""QR code (PNG em bytes) de uma URL."""
from __future__ import annotations

import io

import qrcode


def qr_png(url: str, box: int = 8) -> bytes:
    img = qrcode.make(url, box_size=box, border=2)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
