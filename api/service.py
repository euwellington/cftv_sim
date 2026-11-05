"""Servico de API: portal web + uma porta HTTP por equipamento (ISAPI Hikvision / CGI Intelbras)."""
from __future__ import annotations

import logging

from core.models import Brand

from .server import ServerThread

log = logging.getLogger(__name__)


class ApiService:
    name = "api"

    def __init__(self, ctx) -> None:  # noqa: ANN001
        self.ctx = ctx
        self.srv = ServerThread()

    def start(self) -> None:
        from web.app import create_portal
        bind = self.ctx.profile.devices[0].bind_ip if self.ctx.profile.devices else "0.0.0.0"
        self.srv.add("portal", create_portal(self.ctx), bind, self.ctx.profile.web_port)
        for dev in self.ctx.manager.list():
            if dev.spec.brand is Brand.HIKVISION:
                from brands.hikvision import create_app
            else:
                from brands.intelbras import create_app
            dev.brand_api = create_app(self.ctx, dev)
            self.srv.add(dev.id, dev.brand_api, dev.spec.bind_ip, dev.spec.http_port)
        self.srv.start()
        errs = self.srv.errors()
        if errs:
            raise RuntimeError("; ".join(errs))

    def stop(self) -> None:
        self.srv.stop()


def create_service(ctx) -> ApiService:  # noqa: ANN001
    return ApiService(ctx)
