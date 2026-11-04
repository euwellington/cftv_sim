"""Execucao de tarefas pesadas fora da thread da UI (QThreadPool)."""
from __future__ import annotations

import logging
import traceback
from typing import Any, Callable

from PyQt6.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

log = logging.getLogger(__name__)


class _Signals(QObject):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)


class Worker(QRunnable):
    """Executa ``fn(*args)`` em thread do pool e emite ``done``/``failed`` na thread da UI."""

    def __init__(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()

    def run(self) -> None:
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as exc:  # noqa: BLE001
            log.error("worker falhou: %s", traceback.format_exc())
            self.signals.failed.emit(f"{type(exc).__name__}: {exc}")
        else:
            self.signals.done.emit(result)


# referencias fortes: sem isso o QObject de sinais pode ser coletado antes do emit
_alive: set[Worker] = set()


def run_async(fn: Callable[..., Any], *args: Any, on_done: Callable[[Any], None] | None = None,
              on_error: Callable[[str], None] | None = None, **kwargs: Any) -> Worker:
    """Agenda ``fn`` no pool global. Os callbacks rodam na thread da UI."""
    w = Worker(fn, *args, **kwargs)
    _alive.add(w)

    def _finish(cb):  # noqa: ANN001, ANN202
        def inner(arg):  # noqa: ANN001, ANN202
            _alive.discard(w)
            if cb:
                cb(arg)
        return inner

    w.signals.done.connect(_finish(on_done))
    w.signals.failed.connect(_finish(on_error))
    QThreadPool.globalInstance().start(w)
    return w
