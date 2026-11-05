"""Buffer circular de segmentos curtos (2 s, MPEG-TS) para o pre-evento (~10 s).

O ffmpeg grava ``r00000001.ts`` ... em um diretorio temporario; um limpador remove os mais antigos.
Um segmento so e considerado *fechado* quando existe um com indice maior.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

from .continuous import RawFfmpeg

_RING = re.compile(r"r(\d+)\.ts$")


class RingBuffer:
    def __init__(self, directory: Path, w: int, h: int, fps: int, seg_s: int = 2, keep_s: int = 40) -> None:
        self.dir = directory
        self.seg_s = seg_s
        self.keep = max(8, keep_s // seg_s)
        if self.dir.exists():
            shutil.rmtree(self.dir, ignore_errors=True)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ff = RawFfmpeg(["-f", "segment", "-segment_time", str(seg_s), "-segment_format", "mpegts",
                             "-reset_timestamps", "1", str(self.dir / "r%08d.ts")], w, h, fps)

    def feed(self, frame) -> bool:  # noqa: ANN001
        return self.ff.feed(frame)

    def closed(self) -> list[tuple[int, Path]]:
        """Segmentos fechados (ordenados)."""
        items = []
        for p in self.dir.glob("r*.ts"):
            m = _RING.match(p.name)
            if m:
                items.append((int(m.group(1)), p))
        items.sort()
        return items[:-1]  # o ultimo ainda esta sendo escrito

    def prune(self) -> None:
        items = sorted(int(_RING.match(p.name).group(1)) for p in self.dir.glob("r*.ts") if _RING.match(p.name))
        for idx in items[:-self.keep]:
            (self.dir / f"r{idx:08d}.ts").unlink(missing_ok=True)

    def close(self) -> None:
        self.ff.close()
        shutil.rmtree(self.dir, ignore_errors=True)
