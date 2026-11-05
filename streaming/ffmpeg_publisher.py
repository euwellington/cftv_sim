"""FFmpegPublisher: envia os quadros de uma VirtualCamera ao MediaMTX via RTSP (publish).

Um publisher por (equipamento, canal, stream).  Reinicia o processo `ffmpeg` quando a
camera volta de um reboot ou quando resolucao/fps/bitrate/codec mudam.  ``output`` pode
ser um arquivo (usado nos testes, sem MediaMTX).
"""
from __future__ import annotations

import logging
import subprocess
import threading
import time
from typing import Optional

from core import config
from core.virtual_camera import VirtualCamera

log = logging.getLogger(__name__)


def build_cmd(ffmpeg: str, w: int, h: int, fps: int, kbps: int, codec: str, cbr: bool, output: str) -> list[str]:
    cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostats", "-progress", "pipe:1",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-"]
    if codec == "H.265":
        cmd += ["-c:v", "libx265", "-preset", "veryfast", "-x265-params", f"keyint={fps * 2}:log-level=error",
                "-tag:v", "hvc1"]
    elif codec == "MJPEG":
        cmd += ["-c:v", "mjpeg", "-q:v", "5"]
    else:
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency", "-g", str(fps * 2),
                "-profile:v", "main"]
    if codec != "MJPEG":
        cmd += ["-b:v", f"{kbps}k", "-maxrate", f"{kbps}k" if cbr else f"{int(kbps * 1.5)}k",
                "-bufsize", f"{kbps * 2}k", "-pix_fmt", "yuv420p"]
    if output.startswith("rtsp://"):
        cmd += ["-rtsp_transport", "tcp", "-f", "rtsp", output]
    else:
        cmd += ["-y", output]
    return cmd


class FFmpegPublisher:
    def __init__(self, cam: VirtualCamera, stream: str, output: str, is_available=lambda: True) -> None:
        self.cam, self.stream, self.output = cam, stream, output
        self.is_available = is_available
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self.restarts = 0
        self.last_error = ""

    def _cfg(self):
        c = self.cam.spec.main if self.stream == "main" else self.cam.spec.sub
        return (c.width, c.height, c.fps, c.bitrate_kbps, c.codec, c.bitrate_control == "CBR")

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=f"pub-{self.cam.key}-{self.stream}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._kill()
        if self._thread:
            self._thread.join(timeout=3)

    def _kill(self) -> None:
        p, self._proc = self._proc, None
        if p:
            try:
                p.stdin.close()  # type: ignore[union-attr]
            except Exception:  # noqa: BLE001
                pass
            try:
                p.terminate()
                p.wait(timeout=2)
            except Exception:  # noqa: BLE001
                p.kill()

    def _progress_reader(self, proc: subprocess.Popen) -> None:
        try:
            for raw in proc.stdout:  # type: ignore[union-attr]
                line = raw.decode("utf-8", "ignore").strip()
                if line.startswith("bitrate=") and "kbits" in line:
                    try:
                        self.cam.stats["bitrate_kbps"] = float(line.split("=")[1].replace("kbits/s", ""))
                    except ValueError:
                        pass
        except Exception:  # noqa: BLE001
            pass

    def _spawn(self, cfg) -> Optional[subprocess.Popen]:  # noqa: ANN001
        ffmpeg = config.bin_path("ffmpeg")
        if not ffmpeg:
            self.last_error = "ffmpeg nao encontrado"
            return None
        w, h, fps, kbps, codec, cbr = cfg
        cmd = build_cmd(str(ffmpeg), w, h, fps, kbps, codec, cbr, self.output)
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                bufsize=0)
        threading.Thread(target=self._progress_reader, args=(proc,), daemon=True).start()
        return proc

    def _run(self) -> None:
        cfg = None
        seq = 0
        next_t = time.monotonic()
        while not self._stop.is_set():
            ok = self.cam.is_online and self.is_available()
            if not ok:
                if self._proc:
                    self._kill()
                cfg = None
                time.sleep(0.3)
                continue
            new_cfg = self._cfg()
            if self._proc is None or new_cfg != cfg or self._proc.poll() is not None:
                if self._proc is not None:
                    self._kill()
                    self.restarts += 1
                    time.sleep(1.0)
                cfg = new_cfg
                try:
                    self._proc = self._spawn(cfg)
                except OSError as exc:
                    self.last_error = str(exc)
                    self._proc = None
                if self._proc is None:
                    time.sleep(2.0)
                    continue
                next_t = time.monotonic()
            w, h, fps = cfg[0], cfg[1], cfg[2]
            frame = self.cam.get_frame(self.stream)
            if frame is None:
                time.sleep(0.05)
                continue
            if frame.shape[1] != w or frame.shape[0] != h:
                import cv2
                frame = cv2.resize(frame, (w, h))
            try:
                self._proc.stdin.write(frame.tobytes())  # type: ignore[union-attr]
            except (BrokenPipeError, OSError, ValueError):
                self._kill()
                continue
            next_t += 1.0 / fps
            delay = next_t - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -1.0:
                next_t = time.monotonic()
        self._kill()
