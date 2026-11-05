"""Servidores HTTP (uvicorn) em uma unica thread com loop asyncio proprio."""
from __future__ import annotations

import asyncio
import logging
import threading
from dataclasses import dataclass
from typing import Any, Optional

import uvicorn

log = logging.getLogger(__name__)


@dataclass
class Listener:
    name: str
    app: Any
    host: str
    port: int
    server: Optional[uvicorn.Server] = None
    error: str = ""


class ServerThread:
    def __init__(self) -> None:
        self.listeners: list[Listener] = []
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._ready = threading.Event()

    def add(self, name: str, app: Any, host: str, port: int) -> None:
        self.listeners.append(Listener(name, app, host, port))

    def start(self, timeout: float = 15.0) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name="uvicorn")
        self._thread.start()
        self._ready.wait(timeout)

    async def _serve_one(self, ls: Listener) -> None:
        cfg = uvicorn.Config(ls.app, host=ls.host, port=ls.port, log_config=None, access_log=False,
                             lifespan="off", ws_ping_interval=20)
        ls.server = uvicorn.Server(cfg)
        ls.server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        try:
            await ls.server.serve()
        except SystemExit:  # uvicorn chama sys.exit(1) se nao conseguir abrir a porta
            ls.error = f"nao foi possivel abrir {ls.host}:{ls.port} (porta ocupada?)"
            log.error(ls.error)

    def _run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        tasks = [self.loop.create_task(self._serve_one(ls)) for ls in self.listeners]

        async def wait_started() -> None:
            for _ in range(100):
                if all((ls.server and ls.server.started) or ls.error for ls in self.listeners):
                    break
                await asyncio.sleep(0.1)
            self._ready.set()

        self.loop.create_task(wait_started())
        try:
            self.loop.run_until_complete(asyncio.gather(*tasks, return_exceptions=True))
        finally:
            self._ready.set()

    def errors(self) -> list[str]:
        return [ls.error for ls in self.listeners if ls.error]

    def stop(self) -> None:
        for ls in self.listeners:
            if ls.server:
                ls.server.should_exit = True
        if self._thread:
            self._thread.join(timeout=5)
