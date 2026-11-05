"""Playback RTSP: concatena segmentos gravados e publica em um path temporario do MediaMTX."""
from __future__ import annotations

import logging
import subprocess
import tempfile
import threading
import time
import urllib.request
import uuid
from pathlib import Path

from core import config
from core.db import Database
from core.models import DeviceSpec

log = logging.getLogger(__name__)


class PlaybackService:
    def __init__(self, db: Database, rtsp_port: int, api_port: int) -> None:
        self.db, self.rtsp_port, self.api_port = db, rtsp_port, api_port
        self.sessions: dict[str, subprocess.Popen] = {}

    def plan(self, spec: DeviceSpec, channel: int, start_utc, end_utc):
        """Segmentos do trecho e o deslocamento (s) ate ``start_utc`` dentro do primeiro."""
        from core.db import to_naive_utc
        segs = [s for s in self.db.segments_between(spec.id, channel, start_utc, end_utc) if s.end is not None]
        if not segs:
            return [], 0.0
        off = max(0.0, (to_naive_utc(start_utc) - segs[0].start).total_seconds())
        return segs, off

    async def start(self, spec: DeviceSpec, res) -> str:  # noqa: ANN001
        import asyncio
        return await asyncio.get_running_loop().run_in_executor(None, self._start, spec, res)

    def _start(self, spec: DeviceSpec, res) -> str:  # noqa: ANN001
        from datetime import datetime, timedelta
        start = res.start_utc or datetime.utcnow() - timedelta(hours=1)
        end = res.end_utc or start + timedelta(hours=24)
        segs, off = self.plan(spec, res.channel, start, end)
        if not segs:
            raise LookupError("sem gravacao no periodo")
        exe = config.bin_path("ffmpeg")
        lst = Path(tempfile.gettempdir()) / f"pb_{uuid.uuid4().hex}.txt"
        lst.write_text("".join(f"file '{Path(s.path).as_posix()}'\n" for s in segs), encoding="utf-8")
        name = f"{spec.id}_pb_{uuid.uuid4().hex[:8]}"
        cmd = [str(exe), "-hide_banner", "-loglevel", "error", "-re", "-f", "concat", "-safe", "0", "-ss", f"{off:.2f}",
               "-i", str(lst), "-c", "copy", "-rtsp_transport", "tcp", "-f", "rtsp",
               f"rtsp://127.0.0.1:{self.rtsp_port}/{name}"]
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.sessions[name] = proc
        threading.Thread(target=self._reap, args=(name, proc, lst), daemon=True).start()
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:  # espera o path ficar pronto
            if proc.poll() is not None:
                raise LookupError("falha ao iniciar playback")
            if self._ready(name):
                return name
            time.sleep(0.25)
        return name

    def _ready(self, name: str) -> bool:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{self.api_port}/v3/paths/get/{name}", timeout=1) as r:
                return b'"ready":true' in r.read().replace(b" ", b"")
        except Exception:  # noqa: BLE001
            return False

    def _reap(self, name: str, proc: subprocess.Popen, lst: Path) -> None:
        proc.wait()
        self.sessions.pop(name, None)
        lst.unlink(missing_ok=True)

    def stop_all(self) -> None:
        for p in list(self.sessions.values()):
            p.terminate()
