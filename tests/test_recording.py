import time

from core.app_context import AppContext
from core.device_manager import build_profile
from recording.service import RecordingService
from streaming.ffmpeg_publisher import FFmpegPublisher


def test_continuous_recording_is_indexed(tmp_path):
    prof = build_profile(brand_choice="intelbras", mode="camera", camera_count=1, bind_ip="127.0.0.1",
                         admin_password="Str0ngPassw0rd9xQ",
                         settings={"capture": {"force_test_pattern": True}, "storage": {"root": str(tmp_path / "st")}})
    prof.devices[0].recording.segment_s = 4
    ctx = AppContext(prof, str(tmp_path / "t.db"))
    ctx.manager.start()
    svc = RecordingService(ctx)
    svc.start()
    try:
        time.sleep(9)
    finally:
        svc.stop()
        ctx.manager.stop()
    segs = ctx.db.segments_between(prof.devices[0].id, 1, __import__("datetime").datetime(2000, 1, 1),
                                   __import__("datetime").datetime(2100, 1, 1))
    assert segs and all(s.size > 0 for s in segs)
    assert any(s.end for s in segs)


def test_publisher_writes_file(tmp_path):
    prof = build_profile(brand_choice="hikvision", mode="camera", camera_count=1, bind_ip="127.0.0.1",
                         admin_password="Str0ngPassw0rd9xQ", settings={"capture": {"force_test_pattern": True}})
    ctx = AppContext(prof, str(tmp_path / "t.db"))
    ctx.manager.start()
    out = tmp_path / "o.mp4"
    cam = ctx.manager.camera(prof.devices[0].id, 1)
    pub = FFmpegPublisher(cam, "sub", str(out))
    pub.start()
    time.sleep(4)
    pub.stop()
    ctx.manager.stop()
    assert out.exists() and out.stat().st_size > 1000
