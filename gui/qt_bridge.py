"""Ponte EventBus -> sinais Qt: entrega eventos de qualquer thread na thread da UI."""
from __future__ import annotations

from PyQt6.QtCore import QObject, pyqtSignal

from core.event_bus import Event, EventBus


class QtEventBridge(QObject):
    """Emite ``event`` (queued) para cada evento publicado no EventBus."""

    event = pyqtSignal(object)

    def __init__(self, bus: EventBus, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._unsub = bus.subscribe(self._on_event)

    def _on_event(self, ev: Event) -> None:
        self.event.emit(ev)  # sinal entre threads = entrega enfileirada na thread do receptor

    def close(self) -> None:
        self._unsub()
