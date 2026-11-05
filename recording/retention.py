"""Retencao: aplica a cota dos discos virtuais (overwrite dos mais antigos)."""
from __future__ import annotations

from .storage import StorageManager


def apply(storage: StorageManager) -> int:
    total = 0
    for dev_id, spec in storage.devices.items():
        for disk in spec.disks:
            total += storage.enforce_quota(dev_id, disk.id)
    return total
