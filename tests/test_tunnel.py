import pytest

from core.app_context import AppContext
from core.device_manager import build_profile
from tunnel.base import TunnelError
from tunnel.factory import create_provider


@pytest.fixture()
def ctx(tmp_path):
    prof = build_profile(brand_choice="hikvision", mode="camera", camera_count=1, bind_ip="127.0.0.1",
                         admin_password="Str0ngPassw0rd9xQ", settings={"capture": {"force_test_pattern": True}})
    return AppContext(prof, str(tmp_path / "t.db"))


@pytest.mark.parametrize("kind,cls", [("cloudflare", "CloudflareTunnel"), ("ngrok", "NgrokTunnel"), ("tailscale", "TailscaleTunnel")])
def test_factory(ctx, kind, cls):
    ctx.profile.public.provider = kind
    assert type(create_provider(ctx)).__name__ == cls


def test_missing_binary_gives_clear_error(ctx, monkeypatch):
    monkeypatch.setattr("core.config.bin_path", lambda n: None)
    ctx.profile.public.provider = "cloudflare"
    with pytest.raises(TunnelError, match="cloudflared"):
        ctx.start_tunnel()
