"""Gravacao por evento: pre-evento (do ring buffer) + evento + pos-evento, concatenados em um MP4."""
from __future__ import annotations

import logging
import shutil
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from core import config
from .ring_buffer import RingBuffer

log = logging.getLogger(__name__)


class EventClip:
    """Um clipe de movimento em andamento."""

    def __init__(self, ring: RingBuffer, work: Path, final_dir: Path, pre_s: int, post_s: int) -> None:
        self.ring, self.final_dir = ring, final_dir
        self.pre_s, self.post_s = pre_s, post_s
        self.work = work
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        self.start_local = datetime.now() - timedelta(seconds=pre_s)
        self.stop_at: Optional[float] = None  # monotonic em que o pos-evento termina
        self.copied: set[int] = set()
        self.parts: list[Path] = []
        self._grab(initial=True)

    def _grab(self, initial: bool = False) -> None:
        segs = self.ring.closed()
        if initial:
            segs = segs[-max(1, self.pre_s // self.ring.seg_s):]
        for idx, p in segs:
            if idx in self.copied:
                continue
            dst = self.work / p.name
            try:
                shutil.copy2(p, dst)
            except OSError:
                continue
            self.copied.add(idx)
            self.parts.append(dst)

    def tick(self) -> None:
        self._grab()

    def motion_again(self) -> None:
        self.stop_at = None

    def motion_ended(self) -> None:
        self.stop_at = time.monotonic() + self.post_s

    def due(self) -> bool:
        return self.stop_at is not None and time.monotonic() >= self.stop_at

    def finish(self) -> Optional[Path]:
        """Concatena (copia de fluxo) em ``AAAA-MM-DD/HHMMSS_motion.mp4``. ``None`` se nada foi gravado."""
        time.sleep(self.ring.seg_s + 0.5)  # deixa fechar o ultimo segmento
        self._grab()
        if not self.parts:
            shutil.rmtree(self.work, ignore_errors=True)
            return None
        exe = config.bin_path("ffmpeg")
        out = self.final_dir / f"{self.start_local:%Y-%m-%d}" / f"{self.start_local:%H%M%S}_motion.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        lst = self.work / "list.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in self.parts), encoding="utf-8")
        r = subprocess.run([str(exe), "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0",
                            "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(out)],
                           capture_output=True, timeout=120)
        shutil.rmtree(self.work, ignore_errors=True)
        if r.returncode != 0 or not out.exists():
            log.warning("concat do evento falhou: %s", r.stderr[-200:])
            return None
        return out
