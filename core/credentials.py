"""Senha do admin lembrada LOCALMENTE (data/credentials.json, chmod 600) para mostrar as URLs reais.

Uso de laboratorio: o arquivo fica fora do git (data/ esta no .gitignore). Apague-o para esquecer.
"""
from __future__ import annotations

import json
import os

from . import config


def _path():
    return config.DATA_DIR / "credentials.json"


def load(profile_name: str) -> str:
    try:
        return json.loads(_path().read_text(encoding="utf-8")).get(profile_name, "")
    except (OSError, ValueError):
        return ""


def save(profile_name: str, password: str) -> None:
    try:
        config.ensure_dirs()
        try:
            data = json.loads(_path().read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        data[profile_name] = password
        _path().write_text(json.dumps(data), encoding="utf-8")
        os.chmod(_path(), 0o600)
    except OSError:
        pass
