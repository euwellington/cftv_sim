"""Indexador: varre ``storage/`` e mantem a tabela ``segments`` (inicio, fim, tamanho, tipo)."""
from __future__ import annotations

import logging
import re
import subprocess
import time
from datetime import datetime, timedelta
from pathlib import Path

from core import config
from core.db import Database
from core.fmt import local_to_utc
from core.models import DeviceSpec

log = logging.getLogger(__name__)
_NAME = re.compile(r"^(\d{6})(?:_(manual|motion))?\.mp4$")
SETTLE_S = 3.0  # arquivo sem alteracao por tanto tempo e considerado fechado


def parse_segment_path(p: Path) -> tuple[datetime, str] | None:
    """(inicio em UTC naive, tipo) a partir de ``.../AAAA-MM-DD/HHMMSS[_tipo].mp4``."""
    m = _NAME.match(p.name)
    if not m:
        return None
    try:
        local = datetime.strptime(f"{p.parent.name} {m.group(1)}", "%Y-%m-%d %H%M%S")
    except ValueError:
        return None
    return local_to_utc(local), m.group(2) or "continuous"


def probe_duration(path: Path) -> float | None:
    exe = config.bin_path("ffprobe")
    if not exe:
        return None
    try:
        r = subprocess.run([str(exe), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                           capture_output=True, text=True, timeout=15)
        return float(r.stdout.strip())
    except (ValueError, subprocess.SubprocessError, OSError):
        return None


class Indexer:
    def __init__(self, db: Database, channel_dir_fn, devices: list[DeviceSpec]) -> None:
        self.db, self.channel_dir, self.devices = db, channel_dir_fn, devices
        self._open: dict[str, int] = {}  # path -> id (ainda em escrita)

    def scan(self) -> int:
        """Registra novos arquivos e fecha os concluidos. Retorna quantos foram tocados."""
        touched = 0
        now = time.time()
        for dev in self.devices:
            for ch in dev.channels:
                for disk in dev.disks:
                    base = self.channel_dir(dev.id, ch.index, disk.id)
                    if not base.exists():
                        continue
                    for f in base.glob("*/*.mp4"):
                        parsed = parse_segment_path(f)
                        if not parsed:
                            continue
                        start, kind = parsed
                        key = str(f)
                        try:
                            st = f.stat()
                        except OSError:
                            continue
                        closed = now - st.st_mtime > SETTLE_S
                        if key not in self._open:
                            seg = self.db.get_segment_by_path(key)
                            if seg is None:
                                sid = self.db.add_segment(dev.id, ch.index, kind, start, key,
                                                          end=start + timedelta(seconds=max(1, now - st.st_ctime)) if not closed else None,
                                                          size=st.st_size, disk=disk.id)
                                touched += 1
                                if closed:
                                    self._finalize(sid, f, start, st.st_size)
                                else:
                                    self._open[key] = sid
                            elif seg.end is None and closed:
                                self._finalize(seg.id, f, start, st.st_size)
                                touched += 1
                        else:
                            sid = self._open[key]
                            if closed:
                                self._finalize(sid, f, start, st.st_size)
                                del self._open[key]
                                touched += 1
                            else:
                                self.db.update_segment(sid, size=st.st_size)
        return touched

    def _finalize(self, sid: int, f: Path, start: datetime, size: int) -> None:
        dur = probe_duration(f) or 0.0
        if dur <= 0:
            dur = max(1.0, f.stat().st_mtime - f.stat().st_ctime)
        self.db.update_segment(sid, end=start + timedelta(seconds=dur), size=size)
