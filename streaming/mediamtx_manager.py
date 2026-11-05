"""Gera o mediamtx.yml e controla o processo MediaMTX (somente loopback)."""
from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Optional

from core import config
from core.ports import is_port_free

log = logging.getLogger(__name__)


def render_config(rtsp: int, webrtc: int, hls: int, api: int, webrtc_udp: int = 0,
                  enable_webrtc: bool = False, enable_hls: bool = False) -> str:
    """mediamtx.yml. WebRTC/HLS ficam desligados por padrao (o portal usa MJPEG) para evitar conflitos de porta."""
    return f"""# Gerado automaticamente por cftv_sim - nao edite
logLevel: warn
rtsp: yes
rtspAddress: 127.0.0.1:{rtsp}
rtspTransports: [tcp]
rtmp: no
srt: no
webrtc: {'yes' if enable_webrtc else 'no'}
webrtcAddress: 127.0.0.1:{webrtc}
webrtcAllowOrigins: ['*']
webrtcLocalUDPAddress: 127.0.0.1:{webrtc_udp or webrtc + 1}
webrtcLocalTCPAddress: ''
hls: {'yes' if enable_hls else 'no'}
hlsAddress: 127.0.0.1:{hls}
hlsAllowOrigins: ['*']
api: yes
apiAddress: 127.0.0.1:{api}
metrics: no
pprof: no
playback: no
authMethod: internal
authInternalUsers:
  - user: any
    ips: ['127.0.0.1', '::1']
    permissions:
      - action: publish
      - action: read
      - action: api
paths:
  all_others:
"""


class MediaMtxManager:
    def __init__(self, ports: dict, workdir: Path | None = None) -> None:
        self.rtsp = self._free(int(ports.get("mediamtx_rtsp_internal", 18554)))
        self.webrtc = self._free(int(ports.get("mediamtx_webrtc", 8889)))
        self.hls = self._free(int(ports.get("mediamtx_hls", 8888)), self.webrtc)
        self.api = self._free(int(ports.get("mediamtx_api", 9997)))
        self.workdir = workdir or (config.DATA_DIR / "mediamtx")
        self.proc: Optional[subprocess.Popen] = None
        self.error = ""

    @staticmethod
    def _free(port: int, *avoid: int) -> int:
        """Usa a porta pedida; se estiver ocupada (ex.: MediaMTX antigo ainda rodando), pega a proxima livre."""
        from core.ports import find_free_port
        return port if is_port_free(port, "127.0.0.1") and port not in avoid else find_free_port(port + 1, "127.0.0.1", avoid)

    @property
    def available(self) -> bool:
        return config.bin_path("mediamtx") is not None

    def start(self) -> bool:
        exe = config.bin_path("mediamtx")
        if not exe:
            self.error = "mediamtx nao encontrado (rode tools/fetch_binaries.py)"
            return False
        self.workdir.mkdir(parents=True, exist_ok=True)
        cfg = self.workdir / "mediamtx.yml"
        cfg.write_text(render_config(self.rtsp, self.webrtc, self.hls, self.api), encoding="utf-8")
        self.proc = subprocess.Popen([str(exe), str(cfg)], stdout=open(config.LOG_DIR / "mediamtx.log", "ab"),
                                     stderr=subprocess.STDOUT, cwd=str(self.workdir))
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                tail = ""
                try:
                    tail = (config.LOG_DIR / "mediamtx.log").read_text(encoding="utf-8", errors="ignore").strip().splitlines()[-1]
                except (OSError, IndexError):
                    pass
                self.error = f"mediamtx encerrou (codigo {self.proc.returncode}): {tail}"
                return False
            if not is_port_free(self.rtsp, "127.0.0.1"):
                return True
            time.sleep(0.2)
        self.error = "mediamtx nao abriu a porta RTSP interna"
        return False

    def stop(self) -> None:
        p, self.proc = self.proc, None
        if p:
            p.terminate()
            try:
                p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                p.kill()

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None
