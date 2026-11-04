"""Caminhos, configuracao padrao e localizacao de binarios externos."""
from __future__ import annotations

import copy
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
PROFILES_DIR = CONFIG_DIR / "profiles"
BIN_DIR = ROOT / "bin"
STORAGE_DIR = ROOT / "storage"
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
ASSETS_DIR = ROOT / "assets"

IS_WINDOWS = sys.platform.startswith("win")


def ensure_dirs() -> None:
    """Cria as pastas de trabalho se ainda nao existirem."""
    for d in (PROFILES_DIR, BIN_DIR, STORAGE_DIR, DATA_DIR, DATA_DIR / "thumbs", LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Mescla ``override`` sobre ``base`` recursivamente, sem alterar os originais."""
    out = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_defaults() -> dict[str, Any]:
    """Le ``config/default.json``."""
    with open(CONFIG_DIR / "default.json", encoding="utf-8") as fh:
        return json.load(fh)


def load_settings(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """Configuracao efetiva = default.json + ``overrides`` (ex.: do perfil)."""
    cfg = load_defaults()
    return deep_merge(cfg, overrides) if overrides else cfg


def bin_path(name: str) -> Path | None:
    """Localiza um binario externo: primeiro em ``bin/``, depois no PATH."""
    exe = f"{name}.exe" if IS_WINDOWS else name
    local = BIN_DIR / exe
    if local.exists():
        return local
    found = shutil.which(name)
    return Path(found) if found else None


def storage_root(settings: dict[str, Any]) -> Path:
    """Raiz de armazenamento (relativa ao projeto, se nao for absoluta)."""
    p = Path(settings.get("storage", {}).get("root", "storage"))
    return p if p.is_absolute() else ROOT / p


def is_admin() -> bool:
    """True se o processo roda com privilegio elevado (para portas < 1024)."""
    if IS_WINDOWS:
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            return False
    return hasattr(os, "geteuid") and os.geteuid() == 0
