"""Armazenamento: discos virtuais com cota em GB, status ok/full/error e overwrite."""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from core.db import Database
from core.event_bus import EventBus, EventType
from core.models import DeviceSpec

log = logging.getLogger(__name__)
GB = 1024 ** 3
HIGH_WATER = 0.95  # acima disso o overwrite apaga os segmentos mais antigos
LOW_WATER = 0.90


@dataclass
class DiskInfo:
    device_id: str
    disk_id: str
    capacity: int  # bytes
    used: int
    status: str  # ok | full | error

    @property
    def free(self) -> int:
        return max(0, self.capacity - self.used)

    @property
    def percent(self) -> float:
        return 100.0 * self.used / self.capacity if self.capacity else 0.0


class StorageManager:
    """Gerencia os HDDs virtuais de todos os equipamentos."""

    def __init__(self, db: Database, root: Path, devices: list[DeviceSpec], bus: EventBus) -> None:
        self.db = db
        self.root = Path(root)
        self.bus = bus
        self.devices = {d.id: d for d in devices}

    # ------------------------------------------------------------------ caminhos
    def disk_dir(self, device_id: str, disk_id: str = "hdd1") -> Path:
        """``storage/<hdd>/<marca>`` (um equipamento por marca no perfil padrao)."""
        return self.root / disk_id / self.devices[device_id].brand.value

    def channel_dir(self, device_id: str, channel: int, disk_id: str = "hdd1") -> Path:
        return self.disk_dir(device_id, disk_id) / f"ch{channel:02d}"

    # ------------------------------------------------------------------ consulta
    def info(self, device_id: str, disk_id: str = "hdd1") -> DiskInfo:
        spec = self.devices[device_id]
        disk = next(d for d in spec.disks if d.id == disk_id)
        cap = int(disk.capacity_gb * GB)
        used = self.db.total_size(disk=disk_id, device_id=device_id)
        status = disk.status
        if status == "ok" and not spec.recording.overwrite and used >= cap * HIGH_WATER:
            status = "full"
        return DiskInfo(device_id, disk_id, cap, used, status)

    def all_info(self) -> list[DiskInfo]:
        return [self.info(d.id, k.id) for d in self.devices.values() for k in d.disks]

    def can_record(self, device_id: str, disk_id: str = "hdd1") -> bool:
        i = self.info(device_id, disk_id)
        return i.status == "ok"

    # ------------------------------------------------------------------ operacoes
    def set_capacity(self, device_id: str, disk_id: str, capacity_gb: float) -> None:
        disk = next(d for d in self.devices[device_id].disks if d.id == disk_id)
        disk.capacity_gb = max(0.1, float(capacity_gb))

    def set_status(self, device_id: str, disk_id: str, status: str) -> None:
        """Permite simular falha de disco (``error``) para testar o equipamento."""
        disk = next(d for d in self.devices[device_id].disks if d.id == disk_id)
        disk.status = status  # type: ignore[assignment]
        self.bus.publish(EventType.STORAGE_STATUS, device_id, 0, disk=disk_id, status=status)

    def format_disk(self, device_id: str, disk_id: str = "hdd1") -> int:
        """"Formata" o disco virtual: apaga arquivos e indice. Retorna arquivos removidos."""
        paths = self.db.clear_disk(disk_id, device_id)
        removed = 0
        for p in paths:
            try:
                Path(p).unlink(missing_ok=True)
                removed += 1
            except OSError:
                log.warning("nao foi possivel apagar %s", p)
        base = self.disk_dir(device_id, disk_id)
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        self.set_status(device_id, disk_id, "ok")
        self.db.add_log("operation", f"Disco {disk_id} formatado ({removed} arquivos)", device_id, "GUI")
        return removed

    def enforce_quota(self, device_id: str, disk_id: str = "hdd1") -> int:
        """Overwrite: apaga os segmentos mais antigos ate cair abaixo de LOW_WATER.

        Sem overwrite, marca o disco como ``full``. Retorna segmentos apagados.
        """
        spec = self.devices[device_id]
        info = self.info(device_id, disk_id)
        if info.used < info.capacity * HIGH_WATER:
            return 0
        if not spec.recording.overwrite:
            self.set_status(device_id, disk_id, "full")
            return 0
        deleted = 0
        used = info.used
        while used > info.capacity * LOW_WATER:
            batch = self.db.oldest_segments(disk_id, 20, device_id)
            if not batch:
                break
            ids = []
            for seg in batch:
                try:
                    Path(seg.path).unlink(missing_ok=True)
                except OSError:
                    pass
                used -= seg.size
                ids.append(seg.id)
                deleted += 1
                if used <= info.capacity * LOW_WATER:
                    break
            self.db.delete_segments(ids)
        if deleted:
            self.db.add_log("system", f"Overwrite: {deleted} segmentos antigos removidos", device_id)
        return deleted
