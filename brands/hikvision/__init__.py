"""Hikvision: ISAPI (XML) + aliases PSIA (firmware legado). Autenticacao Digest."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from xml.sax.saxutils import escape

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, Response, StreamingResponse

from brands.base import DeviceAuthMiddleware, can_control
from core.event_bus import EventType
from core.db import now_utc
from core.fmt import utc_to_local

NS = "http://www.hikvision.com/ver20/XMLSchema"
HDR = '<?xml version="1.0" encoding="UTF-8"?>\n'
ALERT_CODES = {
    EventType.MOTION_START: ("VMD", "active"), EventType.MOTION_STOP: ("VMD", "inactive"),
    EventType.VIDEO_LOSS: ("videoloss", "active"), EventType.VIDEO_RESTORED: ("videoloss", "inactive"),
}


def xml(body: str, status: int = 200) -> Response:
    return Response(HDR + body, status_code=status, media_type="application/xml")


def status_xml(code: int, text: str, sub: str = "OK", url: str = "") -> Response:
    return xml(f'<ResponseStatus version="2.0" xmlns="{NS}"><requestURL>{escape(url)}</requestURL>'
               f'<statusCode>{code}</statusCode><statusString>{escape(text)}</statusString>'
               f'<subStatusCode>{sub}</subStatusCode></ResponseStatus>', 200 if code == 1 else 400)


def create_app(ctx, dev) -> FastAPI:  # noqa: ANN001
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    spec = dev.spec
    app.add_middleware(DeviceAuthMiddleware, ctx=ctx, dev=dev)

    def cam_for(stream_id: str):
        n = int(stream_id)
        ch, st = (n // 100, (n % 100) - 1) if n >= 100 else (n, 0)
        return dev.cameras.get(ch), ch, max(0, min(1, st))

    # ------------------------------------------------------------------ sistema
    def device_info() -> Response:
        return xml(
            f'<DeviceInfo version="2.0" xmlns="{NS}"><deviceName>{escape(spec.name)}</deviceName>'
            f"<deviceID>{spec.serial[:8].lower()}-0000-0000-0000-{spec.mac.replace(':', '')}</deviceID>"
            f"<model>{escape(spec.model)}</model><serialNumber>{spec.serial}</serialNumber>"
            f"<macAddress>{spec.mac}</macAddress><firmwareVersion>{spec.firmware}</firmwareVersion>"
            f"<firmwareReleasedDate>{spec.firmware_build}</firmwareReleasedDate>"
            f"<encoderVersion>V5.0</encoderVersion><deviceType>{'NVR' if spec.is_recorder else 'IPC'}</deviceType>"
            f"<telecontrolID>255</telecontrolID></DeviceInfo>")

    def sys_status() -> Response:
        import psutil
        return xml(
            f'<DeviceStatus version="2.0" xmlns="{NS}"><currentDeviceTime>'
            f"{utc_to_local(now_utc()):%Y-%m-%dT%H:%M:%S}</currentDeviceTime>"
            f"<deviceUpTime>{int(dev.uptime())}</deviceUpTime><CPUList><CPU><cpuUtilization>"
            f"{int(psutil.cpu_percent())}</cpuUtilization></CPU></CPUList><MemoryList><Memory>"
            f"<memoryUsage>{int(psutil.virtual_memory().used / 1048576)}</memoryUsage><memoryAvailable>"
            f"{int(psutil.virtual_memory().available / 1048576)}</memoryAvailable></Memory></MemoryList>"
            f"</DeviceStatus>")

    for base in ("/ISAPI", "/PSIA"):
        app.add_api_route(f"{base}/System/deviceInfo", lambda: device_info(), methods=["GET"])
        app.add_api_route(f"{base}/System/status", lambda: sys_status(), methods=["GET"])
    app.add_api_route("/ISAPI/System/time", lambda: xml(
        f'<Time version="2.0" xmlns="{NS}"><timeMode>NTP</timeMode><localTime>'
        f"{utc_to_local(now_utc()):%Y-%m-%dT%H:%M:%S}+00:00</localTime><timeZone>CST+3:00:00</timeZone></Time>"),
        methods=["GET"])

    @app.put("/ISAPI/System/reboot")
    async def reboot(request: Request):
        if not can_control(dev, request):
            return status_xml(4, "Invalid Operation", "notSupport", request.url.path)
        ctx.reboot_system(dev.id, user=request.state.user)
        return status_xml(1, "OK", url=request.url.path)

    @app.put("/ISAPI/ContentMgmt/record/control/manual/{action}/tracks/{tid}")
    async def manual_record(action: str, tid: int, request: Request):
        """Gravacao manual (ISAPI): start/stop no canal tid//100 (ex.: track 101 = canal 1)."""
        if not can_control(dev, request):
            return status_xml(4, "Invalid Operation", "notSupport", request.url.path)
        ch = tid // 100 or 1
        if action not in ("start", "stop") or ch not in dev.cameras:
            return status_xml(4, "Invalid Operation", "invalidID", request.url.path)
        if not ctx.set_recording(dev.id, ch, action == "start"):
            return status_xml(4, "Invalid Operation", "deviceBusy", request.url.path)
        return status_xml(1, "OK", url=request.url.path)

    @app.get("/ISAPI/Security/users")
    async def users():
        items = "".join(f"<User><id>{i + 1}</id><userName>{escape(u.username)}</userName>"
                        f"<userLevel>{'Administrator' if u.role == 'admin' else 'Operator' if u.role == 'operator' else 'Viewer'}</userLevel></User>"
                        for i, u in enumerate(dev.users.users))
        return xml(f'<UserList version="2.0" xmlns="{NS}">{items}</UserList>')

    # ------------------------------------------------------------------ streaming
    def channel_xml(ch: int, st: int) -> str:
        cam = dev.cameras[ch]
        v = cam.spec.main if st == 0 else cam.spec.sub
        return (f"<StreamingChannel><id>{ch * 100 + st + 1}</id><channelName>{escape(cam.spec.name)}</channelName>"
                f"<enabled>true</enabled><Transport><rtspPortNo>{spec.rtsp_port}</rtspPortNo></Transport>"
                f"<Video><enabled>true</enabled><videoInputChannelID>{ch}</videoInputChannelID>"
                f"<videoCodecType>{v.codec}</videoCodecType><videoScanType>progressive</videoScanType>"
                f"<videoResolutionWidth>{v.width}</videoResolutionWidth><videoResolutionHeight>{v.height}</videoResolutionHeight>"
                f"<videoQualityControlType>{v.bitrate_control}</videoQualityControlType>"
                f"<constantBitRate>{v.bitrate_kbps}</constantBitRate><maxFrameRate>{v.fps * 100}</maxFrameRate></Video>"
                f"<Audio><enabled>false</enabled></Audio></StreamingChannel>")

    @app.get("/ISAPI/Streaming/channels")
    @app.get("/PSIA/Streaming/channels")
    async def channels():
        items = "".join(channel_xml(c, s) for c in sorted(dev.cameras) for s in (0, 1))
        return xml(f'<StreamingChannelList version="2.0" xmlns="{NS}">{items}</StreamingChannelList>')

    @app.get("/ISAPI/Streaming/channels/{sid}")
    @app.get("/PSIA/Streaming/channels/{sid}")
    async def channel_one(sid: str):
        cam, ch, st = cam_for(sid)
        if cam is None:
            return status_xml(4, "Invalid Operation", "notSupport")
        return xml(channel_xml(ch, st).replace("<StreamingChannel>", f'<StreamingChannel version="2.0" xmlns="{NS}">', 1))

    @app.get("/ISAPI/Streaming/channels/{sid}/picture")
    @app.get("/PSIA/Streaming/channels/{sid}/picture")
    async def picture(sid: str):
        cam, ch, st = cam_for(sid)
        jpg = cam.snapshot_jpeg("main" if st == 0 else "sub") if cam else None
        if jpg is None:
            return status_xml(4, "Device Error", "deviceError")
        return Response(jpg, media_type="image/jpeg")

    @app.get("/ISAPI/Streaming/status")
    async def stream_status():
        return xml(f'<StreamingStatus version="2.0" xmlns="{NS}"><totalStreamingSessions>0</totalStreamingSessions></StreamingStatus>')

    # ------------------------------------------------------------------ PTZ
    @app.get("/ISAPI/PTZCtrl/channels/{ch}/capabilities")
    async def ptz_caps(ch: int):
        return xml(f'<PTZChanelCap version="2.0" xmlns="{NS}"><continuousPanTiltSpace><XRange><Min>-100</Min><Max>100</Max></XRange>'
                   f'<YRange><Min>-100</Min><Max>100</Max></YRange></continuousPanTiltSpace></PTZChanelCap>')

    @app.put("/ISAPI/PTZCtrl/channels/{ch}/continuous")
    async def ptz_continuous(ch: int, request: Request):
        cam = dev.cameras.get(ch)
        if cam is None or not can_control(dev, request):
            return status_xml(4, "Invalid Operation", "notSupport", request.url.path)
        body = (await request.body()).decode("utf-8", "ignore")

        def tag(n: str) -> float:
            m = re.search(rf"<{n}>\s*(-?\d+(?:\.\d+)?)\s*</{n}>", body)
            return float(m.group(1)) / 100.0 if m else 0.0
        cam.ptz_continuous(tag("pan"), tag("tilt"), tag("zoom"))
        return status_xml(1, "OK", url=request.url.path)

    @app.put("/ISAPI/PTZCtrl/channels/{ch}/homePosition/goto")
    async def ptz_home(ch: int, request: Request):
        cam = dev.cameras.get(ch)
        if cam:
            cam.ptz_home()
        return status_xml(1, "OK", url=request.url.path)

    # ------------------------------------------------------------------ armazenamento
    @app.get("/ISAPI/ContentMgmt/Storage/hdd")
    async def hdd():
        items = ""
        for i, d in enumerate(ctx.storage.all_info(), start=1):
            if d.device_id != dev.id:
                continue
            items += (f"<hdd><id>{i}</id><hddName>{d.disk_id}</hddName><hddPath></hddPath><hddType>SATA</hddType>"
                      f"<status>{'ok' if d.status == 'ok' else 'error'}</status><capacity>{d.capacity // 1048576}</capacity>"
                      f"<freeSpace>{d.free // 1048576}</freeSpace><property>RW</property></hdd>")
        return xml(f'<hddList version="2.0" xmlns="{NS}">{items}</hddList>')

    @app.post("/ISAPI/ContentMgmt/search")
    async def search(request: Request):
        body = (await request.body()).decode("utf-8", "ignore")
        tid = re.search(r"<trackID>(\d+)</trackID>", body)
        track = int(tid.group(1)) if tid else 101
        ch = track // 100 or 1
        st = re.search(r"<startTime>([^<]+)</startTime>", body)
        en = re.search(r"<endTime>([^<]+)</endTime>", body)
        mx = re.search(r"<maxResults>(\d+)</maxResults>", body)
        pos = re.search(r"<searchResultPosition>(\d+)</searchResultPosition>", body)

        def p(v, default):
            if not v:
                return default
            return datetime.fromisoformat(v.group(1).replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)
        start, end = p(st, datetime(2000, 1, 1)), p(en, datetime(2100, 1, 1))
        segs = [s for s in ctx.db.segments_between(dev.id, ch, start, end) if s.end]
        offset = int(pos.group(1)) if pos else 0
        limit = int(mx.group(1)) if mx else 50
        page = segs[offset:offset + limit]
        fmt = "%Y-%m-%dT%H:%M:%SZ"
        items = "".join(
            f"<searchMatchItem><sourceID>{{{dev.id}-{ch}}}</sourceID><trackID>{ch * 100 + 1}</trackID>"
            f"<timeSpan><startTime>{s.start.strftime(fmt)}</startTime><endTime>{s.end.strftime(fmt)}</endTime></timeSpan>"
            f"<mediaSegmentDescriptor><contentType>video</contentType><codecType>H.264</codecType>"
            f"<playbackURI>rtsp://{ctx.host()}:{spec.rtsp_port}/Streaming/tracks/{ch * 100 + 1}?starttime={s.start:%Y%m%dT%H%M%SZ}"
            f"&amp;endtime={s.end:%Y%m%dT%H%M%SZ}</playbackURI></mediaSegmentDescriptor>"
            f"<metadataDescriptor><recordType>{'CMR' if s.kind == 'continuous' else 'VMD' if s.kind == 'motion' else 'manual'}</recordType></metadataDescriptor>"
            f"</searchMatchItem>" for s in page)
        more = "true" if offset + limit < len(segs) else "false"
        return xml(f'<CMSearchResult version="2.0" xmlns="{NS}"><searchID>{{0}}</searchID>'
                   f"<responseStatus>true</responseStatus><responseStatusStrg>{'MORE' if more == 'true' else 'OK'}</responseStatusStrg>"
                   f"<numOfMatches>{len(page)}</numOfMatches><matchList>{items}</matchList></CMSearchResult>")

    # ------------------------------------------------------------------ eventos
    @app.get("/ISAPI/Event/notification/alertStream")
    async def alert_stream(request: Request):
        q, unsub = ctx.bus.subscribe_async(asyncio.get_running_loop(), list(ALERT_CODES))

        async def gen():
            try:
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), 10)
                    except asyncio.TimeoutError:
                        ev = None
                    if await request.is_disconnected():
                        break
                    if ev is None or ev.device_id != dev.id:
                        if ev is None:  # keep-alive
                            body = (f'<EventNotificationAlert version="2.0" xmlns="{NS}"><eventType>videoloss</eventType>'
                                    f"<eventState>inactive</eventState><eventDescription>heartbeat</eventDescription></EventNotificationAlert>")
                        else:
                            continue
                    else:
                        et, state = ALERT_CODES[ev.type]
                        body = (f'<EventNotificationAlert version="2.0" xmlns="{NS}"><ipAddress>{ctx.host()}</ipAddress>'
                                f"<portNo>{spec.http_port}</portNo><protocolType>HTTP</protocolType><macAddress>{spec.mac}</macAddress>"
                                f"<channelID>{ev.channel}</channelID><dateTime>{ev.ts:%Y-%m-%dT%H:%M:%S}Z</dateTime>"
                                f"<activePostCount>1</activePostCount><eventType>{et}</eventType><eventState>{state}</eventState>"
                                f"<eventDescription>{et} alarm</eventDescription></EventNotificationAlert>")
                    yield (f"--boundary\r\nContent-Type: application/xml; charset=UTF-8\r\nContent-Length: {len(body)}\r\n\r\n{body}\r\n").encode()
            finally:
                unsub()
        return StreamingResponse(gen(), media_type="multipart/mixed; boundary=boundary")

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return f"<html><head><title>{escape(spec.model)}</title></head><body><h3>{escape(spec.model)}</h3><p>Hikvision web service (simulador)</p></body></html>"

    return app
