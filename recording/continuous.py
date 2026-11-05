"""Escritores FFmpeg: segmentos MP4 de N segundos (continuo/manual) alimentados por quadros crus.

Os MP4 usam fragmentacao (``frag_keyframe+empty_moov``): arquivos em escrita ja sao reproduziveis.
Nome: ``chNN/AAAA-MM-DD/HHMMSS.mp4`` (continuo), ``HHMMSS_manual.mp4`` (hora local do host).
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

import numpy as np

from core import config

log = logging.getLogger(__name__)
MP4_OPTS = "movflags=frag_keyframe+empty_moov+default_base_moof"


def _enc(codec_fps: int) -> list[str]:
    return ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-g", str(codec_fps * 2),
            "-pix_fmt", "yuv420p", "-crf", "28"]


class RawFfmpeg:
    """Processo ffmpeg que recebe BGR24 em stdin."""

    def __init__(self, args_after_encode: list[str], w: int, h: int, fps: int) -> None:
        exe = config.bin_path("ffmpeg")
        if not exe:
            raise FileNotFoundError("ffmpeg nao encontrado")
        self.w, self.h, self.fps = w, h, fps
        cmd = [str(exe), "-hide_banner", "-loglevel", "error", "-nostats", "-f", "rawvideo", "-pix_fmt", "bgr24",
               "-s", f"{w}x{h}", "-r", str(fps), "-i", "-", *_enc(fps), *args_after_encode]
        self.proc: Optional[subprocess.Popen] = subprocess.Popen(
            cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, bufsize=0)

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def feed(self, frame: np.ndarray) -> bool:
        if not self.alive:
            return False
        try:
            self.proc.stdin.write(frame.tobytes())  # type: ignore[union-attr]
            return True
        except (BrokenPipeError, OSError, ValueError):
            return False

    def close(self, timeout: float = 5.0) -> None:
        p, self.proc = self.proc, None
        if not p:
            return
        try:
            p.stdin.close()  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            pass
        try:
            p.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            p.kill()


def ensure_day_dirs(channel_dir: Path) -> None:
    """Cria as pastas de hoje e de amanha (o muxer nao cria pastas; cobre a virada do dia)."""
    from datetime import datetime, timedelta
    now = datetime.now()
    for d in (now, now + timedelta(days=1)):
        (channel_dir / f"{d:%Y-%m-%d}").mkdir(parents=True, exist_ok=True)


def segment_writer(channel_dir: Path, w: int, h: int, fps: int, segment_s: int, suffix: str = "") -> RawFfmpeg:
    """Grava segmentos de ``segment_s`` s alinhados ao relogio em ``channel_dir/AAAA-MM-DD/HHMMSS{suffix}.mp4``."""
    ensure_day_dirs(channel_dir)
    pattern = str(channel_dir / f"%Y-%m-%d/%H%M%S{suffix}.mp4")
    return RawFfmpeg(["-f", "segment", "-segment_time", str(segment_s), "-segment_atclocktime", "1",
                      "-strftime", "1", "-reset_timestamps", "1",
                      "-segment_format", "mp4", "-segment_format_options", MP4_OPTS, pattern], w, h, fps)
