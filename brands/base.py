"""Base das APIs de marca: autenticacao Digest (Basic opcional), bloqueio e 503 durante reboot."""
from __future__ import annotations

from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from api.digest_auth import DigestAuthenticator
from core.event_bus import EventType
from core.security import LoginLimiter


def client_ip(request: Request) -> str:
    ip = request.client.host if request.client else "?"
    if ip in ("127.0.0.1", "::1"):  # tunel local: usa o IP real informado pelo proxy
        fwd = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        return fwd or ip
    return ip


class DeviceAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, ctx, dev, open_paths: tuple[str, ...] = ()) -> None:  # noqa: ANN001
        super().__init__(app)
        self.ctx, self.dev, self.open_paths = ctx, dev, open_paths
        sec = ctx.settings.get("security", {})
        self.limiter = LoginLimiter(sec.get("lockout_attempts", 5), sec.get("lockout_window_s", 900),
                                    sec.get("lockout_duration_s", 1800))
        self.auth = DigestAuthenticator(dev.spec.realm, dev.users.digest_ha1, allow_basic=dev.spec.allow_basic,
                                        basic_verify=lambda u, p: dev.users.verify(u, p) is not None)

    async def dispatch(self, request: Request, call_next) -> Response:  # noqa: ANN001
        if not self.dev.is_available:
            return PlainTextResponse("Service Unavailable (rebooting)", status_code=503)
        if request.url.path in self.open_paths:
            return await call_next(request)
        ip = client_ip(request)
        ok, _wait = self.limiter.check(f"ip:{ip}")
        if not ok:
            self.ctx.bus.publish(EventType.LOGIN_LOCKED, self.dev.id, 0, ip=ip, proto="http")
            return PlainTextResponse("Locked: too many failed logins", status_code=403)
        target = request.url.path + (("?" + request.url.query) if request.url.query else "")
        res = self.auth.verify(request.headers.get("authorization"), request.method, target)
        if not res.ok:
            if res.credentials_sent and not res.stale:
                locked = self.limiter.fail(f"ip:{ip}")
                self.ctx.bus.publish(EventType.LOGIN_LOCKED if locked else EventType.LOGIN_FAIL, self.dev.id, 0,
                                     ip=ip, user=res.username, proto="http")
            r = PlainTextResponse("Unauthorized", status_code=401)
            for c in self.auth.challenge(stale=res.stale):
                r.headers.append("WWW-Authenticate", c)
            return r
        self.limiter.success(f"ip:{ip}")
        request.state.user = res.username
        self.ctx.bus.publish(EventType.LOGIN_OK, self.dev.id, 0, ip=ip, user=res.username, proto="http") \
            if request.method != "GET" else None
        return await call_next(request)


def role_of(dev, username: str) -> str:
    u = dev.users.get(username)
    return u.role if u else "viewer"


def can_control(dev: Any, request: Request) -> bool:
    return role_of(dev, getattr(request.state, "user", "")) in ("admin", "operator")
