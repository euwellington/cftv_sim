from __future__ import annotations

from .base import TunnelProvider


def create_provider(ctx) -> TunnelProvider:  # noqa: ANN001
    kind = ctx.profile.public.provider
    if kind == "cloudflare":
        from .cloudflare import CloudflareTunnel
        return CloudflareTunnel(ctx)
    if kind == "ngrok":
        from .ngrok import NgrokTunnel
        return NgrokTunnel(ctx)
    if kind == "tailscale":
        from .tailscale import TailscaleTunnel
        return TailscaleTunnel(ctx)
    if kind == "bore":
        from .bore import BoreTunnel
        return BoreTunnel(ctx)
    if kind == "pinggy":
        from .pinggy import PinggyTunnel
        return PinggyTunnel(ctx)
    raise ValueError(f"provedor de tunel desconhecido: {kind}")
