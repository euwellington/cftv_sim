#!/usr/bin/env python3
"""Baixa os binarios externos do cftv_sim para ``bin/``.

Binarios: MediaMTX, FFmpeg e cloudflared (ngrok e tailscale sao opcionais e
instalados pelo usuario).  Usa apenas a biblioteca padrao, para rodar antes
do ``pip install``.

Uso:
    python tools/fetch_binaries.py            # baixa o que faltar
    python tools/fetch_binaries.py --force    # rebaixa tudo
    python tools/fetch_binaries.py --only mediamtx cloudflared

Seguranca: os downloads vem de HTTPS (GitHub Releases / gyan.dev) e o SHA-256 de
cada arquivo e impresso ao final para conferencia manual.  Se o binario ja
estiver no PATH (ex.: ffmpeg do sistema) ele e reaproveitado.
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import platform
import shutil
import stat
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = ROOT / "bin"
IS_WIN = sys.platform.startswith("win")
EXE = ".exe" if IS_WIN else ""
UA = {"User-Agent": "cftv-sim-installer/1.0"}


def _arch() -> str:
    m = platform.machine().lower()
    if m in ("x86_64", "amd64"):
        return "amd64"
    if m in ("aarch64", "arm64"):
        return "arm64"
    if m.startswith("armv7"):
        return "armv7"
    return m


def _os() -> str:
    if IS_WIN:
        return "windows"
    if sys.platform == "darwin":
        return "darwin"
    return "linux"


def http_get(url: str, desc: str = "") -> bytes:
    """Baixa uma URL inteira em memoria, com barra de progresso simples."""
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        buf = io.BytesIO()
        done = 0
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            buf.write(chunk)
            done += len(chunk)
            if total:
                pct = done * 100 // total
                print(f"\r  {desc or url[-40:]}: {pct:3d}% ({done // 1024} KB)", end="", flush=True)
        print()
        return buf.getvalue()


def github_latest_assets(repo: str) -> list[dict]:
    raw = http_get(f"https://api.github.com/repos/{repo}/releases/latest", f"api {repo}")
    return json.loads(raw)["assets"]


def pick_asset(assets: list[dict], *needles: str, suffixes: tuple[str, ...] = ()) -> dict:
    for a in assets:
        name = a["name"].lower()
        if all(n in name for n in needles) and (not suffixes or name.endswith(suffixes)):
            return a
    raise RuntimeError(f"asset nao encontrado: {needles} em {[a['name'] for a in assets][:8]}...")


def make_executable(p: Path) -> None:
    if not IS_WIN:
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def extract_member(archive: bytes, filename: str, wanted: str, dest: Path) -> None:
    """Extrai um unico arquivo (``wanted`` = nome base) de um zip/tar.gz."""
    if filename.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(archive)) as z:
            for n in z.namelist():
                if Path(n).name == wanted:
                    dest.write_bytes(z.read(n))
                    return
    else:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as t:
            for m in t.getmembers():
                if m.isfile() and Path(m.name).name == wanted:
                    f = t.extractfile(m)
                    assert f is not None
                    dest.write_bytes(f.read())
                    return
    raise RuntimeError(f"{wanted} nao encontrado em {filename}")


def fetch_mediamtx(force: bool) -> str | None:
    dest = BIN / f"mediamtx{EXE}"
    if dest.exists() and not force:
        print("[ok] mediamtx ja existe")
        return None
    arch = _arch()
    assets = github_latest_assets("bluenviron/mediamtx")
    ext = ".zip" if IS_WIN else ".tar.gz"
    a = pick_asset(assets, _os(), arch, suffixes=(ext,))
    data = http_get(a["browser_download_url"], a["name"])
    extract_member(data, a["name"], f"mediamtx{EXE}", dest)
    make_executable(dest)
    print(f"[ok] mediamtx -> {dest}")
    return f"{a['name']}  sha256={hashlib.sha256(data).hexdigest()}"


def fetch_cloudflared(force: bool) -> str | None:
    dest = BIN / f"cloudflared{EXE}"
    if dest.exists() and not force:
        print("[ok] cloudflared ja existe")
        return None
    arch = _arch()
    assets = github_latest_assets("cloudflare/cloudflared")
    if IS_WIN:
        a = pick_asset(assets, "windows", arch, suffixes=(".exe",))
        data = http_get(a["browser_download_url"], a["name"])
        dest.write_bytes(data)
    elif _os() == "darwin":
        a = pick_asset(assets, "darwin", arch, suffixes=(".tgz",))
        data = http_get(a["browser_download_url"], a["name"])
        extract_member(data, a["name"], "cloudflared", dest)
    else:
        a = pick_asset(assets, "linux", arch)
        # alguns assets sao .deb/.rpm: queremos o binario puro
        for cand in assets:
            n = cand["name"].lower()
            if n == f"cloudflared-linux-{arch}":
                a = cand
                break
        data = http_get(a["browser_download_url"], a["name"])
        dest.write_bytes(data)
    make_executable(dest)
    print(f"[ok] cloudflared -> {dest}")
    return f"{a['name']}  sha256={hashlib.sha256(data).hexdigest()}"


def fetch_bore(force: bool) -> str | None:
    """bore (https://github.com/ekzhang/bore): cliente de tunel TCP gratuito (servidor publico bore.pub, sem conta)."""
    dest = BIN / f"bore{EXE}"
    if dest.exists() and not force:
        print("[ok] bore ja existe")
        return None
    arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(_arch(), _arch())
    assets = github_latest_assets("ekzhang/bore")
    if IS_WIN:
        a = pick_asset(assets, "windows", arch, suffixes=(".zip",))
    elif _os() == "darwin":
        a = pick_asset(assets, "apple-darwin", arch, suffixes=(".tar.gz",))
    else:
        a = pick_asset(assets, "linux", arch, suffixes=(".tar.gz",))
    data = http_get(a["browser_download_url"], a["name"])
    extract_member(data, a["name"], f"bore{EXE}", dest)
    make_executable(dest)
    print(f"[ok] bore -> {dest}")
    return f"{a['name']}  sha256={hashlib.sha256(data).hexdigest()}"


def fetch_ffmpeg(force: bool) -> str | None:
    dest = BIN / f"ffmpeg{EXE}"
    if dest.exists() and not force:
        print("[ok] ffmpeg ja existe em bin/")
        return None
    if not force and shutil.which("ffmpeg"):
        print(f"[ok] ffmpeg do sistema: {shutil.which('ffmpeg')}")
        return None
    if IS_WIN:
        urls = [
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
            "https://github.com/BtbN/FFmpeg-Builds/releases/latest/download/ffmpeg-master-latest-win64-gpl.zip",
        ]
        last: Exception | None = None
        for url in urls:
            try:
                data = http_get(url, "ffmpeg (windows)")
                extract_member(data, "x.zip", "ffmpeg.exe", dest)
                try:
                    extract_member(data, "x.zip", "ffprobe.exe", BIN / "ffprobe.exe")
                except RuntimeError:
                    pass
                print(f"[ok] ffmpeg -> {dest}")
                return f"{url}  sha256={hashlib.sha256(data).hexdigest()}"
            except Exception as exc:  # noqa: BLE001 - tenta o proximo espelho
                last = exc
                print(f"  falhou ({exc}); tentando outro espelho")
        raise RuntimeError(f"nao foi possivel baixar o ffmpeg: {last}")
    if _os() == "linux":
        arch = {"amd64": "amd64", "arm64": "arm64"}.get(_arch())
        if not arch:
            raise RuntimeError("arquitetura sem build estatico automatico: instale o ffmpeg pelo gerenciador de pacotes")
        url = f"https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-{arch}-static.tar.xz"
        data = http_get(url, "ffmpeg (linux static)")
        extract_member(data, "x.tar.xz", "ffmpeg", dest)
        try:
            extract_member(data, "x.tar.xz", "ffprobe", BIN / "ffprobe")
            make_executable(BIN / "ffprobe")
        except RuntimeError:
            pass
        make_executable(dest)
        print(f"[ok] ffmpeg -> {dest}")
        return f"{url}  sha256={hashlib.sha256(data).hexdigest()}"
    raise RuntimeError("macOS: instale com `brew install ffmpeg`")


FETCHERS = {"mediamtx": fetch_mediamtx, "ffmpeg": fetch_ffmpeg, "cloudflared": fetch_cloudflared, "bore": fetch_bore}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="rebaixa mesmo se ja existir")
    ap.add_argument("--only", nargs="+", choices=sorted(FETCHERS), help="baixa apenas estes")
    args = ap.parse_args()

    BIN.mkdir(exist_ok=True)
    hashes: list[str] = []
    failed: list[str] = []
    for name in args.only or list(FETCHERS):
        try:
            h = FETCHERS[name](args.force)
            if h:
                hashes.append(h)
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            print(f"[ERRO] {name}: {exc}")
    if hashes:
        print("\nSHA-256 dos downloads (confira se quiser):")
        for h in hashes:
            print("  ", h)
    if failed:
        print(f"\nFalharam: {', '.join(failed)}. O app abre sem eles, mas os recursos "
              "correspondentes (streaming / tunel) ficam indisponiveis ate instala-los.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
