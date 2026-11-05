"""Intelbras (estilo Dahua): CGI HTTP key=value com Digest."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse

from brands.base import DeviceAuthMiddleware, can_control
from core.db import now_utc
from core.event_bus import EventType
from core.fmt import local_to_utc, utc_to_local

CODES = {
    EventType.MOTION_START: ("VideoMotion", "Start"), EventType.MOTION_STOP: ("VideoMotion", "Stop"),
    EventType.VIDEO_LOSS: ("VideoLoss", "Start"), EventType.VIDEO_RESTORED: ("VideoLoss", "Stop"),
}


def kv(d: dict) -> PlainTextResponse:
    return PlainTextResponse("".join(f"{k}={v}\r\n" for k, v in d.items()))


def create_app(ctx, dev) -> FastAPI:  # noqa: ANN001
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    spec = dev.spec
    app.add_middleware(DeviceAuthMiddleware, ctx=ctx, dev=dev)
    found: dict[str, list] = {}  # mediaFileFind: object -> resultados

    def chan(request: Request) -> int:
        try:
            return max(1, int(request.query_params.get("channel", "1")))
        except ValueError:
            return 1

    @app.get("/cgi-bin/magicBox.cgi")
    async def magic(request: Request):
        a = request.query_params.get("action", "")
        if a == "getSystemInfo":
            return kv({"serialNumber": spec.serial, "deviceType": spec.model, "processor": "ARM", "updateSerial": spec.model,
                       "hardwareVersion": "1.00", "appAutoStart": "true"})
        if a == "getDeviceType":
            return kv({"type": spec.model})
        if a == "getSerialNo":
            return kv({"sn": spec.serial})
        if a == "getMachineName":
            return kv({"name": spec.name})
        if a == "getSoftwareVersion":
            return kv({"version": f"{spec.firmware},build:{spec.firmware_build}"})
        if a == "getHardwareVersion":
            return kv({"version": "1.00"})
        if a == "getVendor":
            return kv({"vendor": "Intelbras"})
        if a == "getCurrentTime":
            return PlainTextResponse(f"result={utc_to_local(now_utc()):%Y-%m-%d %H:%M:%S}\r\n")
        if a == "reboot":
            if not can_control(dev, request):
                return PlainTextResponse("Error\r\n", status_code=403)
            ctx.reboot_system(dev.id, user=request.state.user)
            return PlainTextResponse("OK\r\n")
        return PlainTextResponse("Error\r\nBad Request!\r\n", status_code=400)

    @app.get("/cgi-bin/snapshot.cgi")
    async def snapshot(request: Request):
        cam = dev.cameras.get(chan(request))
        jpg = cam.snapshot_jpeg("main") if cam else None
        if jpg is None:
            return PlainTextResponse("Error\r\n", status_code=500)
        return Response(jpg, media_type="image/jpeg")

    @app.get("/cgi-bin/ptz.cgi")
    async def ptz(request: Request):
        cam = dev.cameras.get(chan(request))
        if cam is None or not can_control(dev, request):
            return PlainTextResponse("Error\r\n", status_code=403)
        q = request.query_params
        a, code = q.get("action", ""), q.get("code", "")
        if a == "stop":
            cam.ptz_stop()
        elif a in ("start", "continuous"):
            sp = min(8, max(1, int(q.get("arg2", "4") or 4))) / 8.0
            vec = {"Up": (0, sp, 0), "Down": (0, -sp, 0), "Left": (-sp, 0, 0), "Right": (sp, 0, 0),
                   "ZoomTele": (0, 0, sp), "ZoomWide": (0, 0, -sp), "LeftUp": (-sp, sp, 0), "RightUp": (sp, sp, 0),
                   "LeftDown": (-sp, -sp, 0), "RightDown": (sp, -sp, 0)}.get(code)
            if vec is None:
                return PlainTextResponse("Error\r\n", status_code=400)
            cam.ptz_continuous(*vec)
        return PlainTextResponse("OK\r\n")

    @app.get("/cgi-bin/configManager.cgi")
    async def config(request: Request):
        q = request.query_params
        name, a = q.get("name", ""), q.get("action", "")
        if a == "setConfig":
            # RecordMode[i].Mode: 0 = automatico, 1 = manual (gravando), 2 = desligado. i = canal - 1.
            if not can_control(dev, request):
                return PlainTextResponse("Error\r\nUnauthorized\r\n", status_code=403)
            for key, val in q.items():
                m = re.fullmatch(r"RecordMode\[(\d+)\]\.Mode", key)
                if m and int(m.group(1)) + 1 in dev.cameras:
                    ctx.set_recording(dev.id, int(m.group(1)) + 1, val == "1")
            return PlainTextResponse("OK\r\n")
        if a != "getConfig":
            return PlainTextResponse("OK\r\n")
        if name == "RecordMode":
            return kv({f"table.RecordMode[{ch - 1}].Mode": 1 if cam.recording and cam.recording_kind == "manual" else 0
                       for ch, cam in sorted(dev.cameras.items())})
        if name == "General":
            return kv({"table.General.MachineName": spec.name, "table.General.LocalNo": "8", "table.General.LockLoginTimes": "5"})
        if name == "Encode":
            out: dict = {}
            for ch, cam in sorted(dev.cameras.items()):
                i = ch - 1
                for kind, v in (("MainFormat[0]", cam.spec.main), ("ExtraFormat[0]", cam.spec.sub)):
                    pre = f"table.Encode[{i}].{kind}"
                    out[f"{pre}.Video.Compression"] = v.codec.replace(".", "")
                    out[f"{pre}.Video.BitRate"] = v.bitrate_kbps
                    out[f"{pre}.Video.FPS"] = v.fps
                    out[f"{pre}.Video.Width"] = v.width
                    out[f"{pre}.Video.Height"] = v.height
            return kv(out)
        if name == "ChannelTitle":
            return kv({f"table.ChannelTitle[{ch - 1}].Name": cam.spec.name for ch, cam in sorted(dev.cameras.items())})
        return PlainTextResponse("Error\r\n", status_code=400)

    @app.get("/cgi-bin/eventManager.cgi")
    async def events(request: Request):
        if request.query_params.get("action") != "attach":
            return PlainTextResponse("Error\r\n", status_code=400)
        q, unsub = ctx.bus.subscribe_async(asyncio.get_running_loop(), list(CODES))

        async def gen():
            try:
                yield b"--myboundary\r\nContent-Type: text/plain\r\nContent-Length: 9\r\n\r\nHeartbeat\r\n\r\n"
                while True:
                    try:
                        ev = await asyncio.wait_for(q.get(), 5)
                    except asyncio.TimeoutError:
                        ev = None
                    if await request.is_disconnected():
                        break
                    if ev is None:
                        body = "Heartbeat"
                    elif ev.device_id != dev.id:
                        continue
                    else:
                        code, act = CODES[ev.type]
                        body = f"Code={code};action={act};index={max(0, ev.channel - 1)}"
                    yield f"--myboundary\r\nContent-Type: text/plain\r\nContent-Length: {len(body)}\r\n\r\n{body}\r\n\r\n".encode()
            finally:
                unsub()
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=myboundary")

    @app.get("/cgi-bin/storageDevice.cgi")
    async def storage(request: Request):
        out: dict = {}
        for i, d in enumerate([x for x in ctx.storage.all_info() if x.device_id == dev.id]):
            p = f"list.info[{i}]"
            out.update({f"{p}.Name": f"/dev/sd{chr(97 + i)}", f"{p}.State": "Success" if d.status == "ok" else "Error",
                        f"{p}.Detail[0].TotalBytes": d.capacity, f"{p}.Detail[0].UsedBytes": d.used,
                        f"{p}.Detail[0].Path": f"/mnt/{d.disk_id}"})
        return kv(out)

    @app.get("/cgi-bin/userManager.cgi")
    async def users(request: Request):
        out = {}
        for i, u in enumerate(dev.users.users):
            out.update({f"users[{i}].Name": u.username, f"users[{i}].Group": "admin" if u.role == "admin" else "user",
                        f"users[{i}].Id": i + 1})
        return kv(out)

    @app.get("/cgi-bin/mediaFileFind.cgi")
    async def media_find(request: Request):
        q = request.query_params
        a = q.get("action", "")
        obj = q.get("object", "1")
        if a == "factory.create":
            return PlainTextResponse("result=1\r\n")
        if a == "findFile":
            ch = int(q.get("condition.Channel", "1") or 1)

            def pt(v: str, d):
                try:
                    return local_to_utc(datetime.strptime(v, "%Y-%m-%d %H:%M:%S"))
                except ValueError:
                    return d
            st = pt(q.get("condition.StartTime", ""), datetime(2000, 1, 1))
            en = pt(q.get("condition.EndTime", ""), datetime(2100, 1, 1))
            found[obj] = [s for s in ctx.db.segments_between(dev.id, ch, st, en) if s.end]
            return PlainTextResponse("OK\r\n")
        if a == "findNextFile":
            n = int(q.get("count", "100") or 100)
            rows = found.get(obj, [])
            page, found[obj] = rows[:n], rows[n:]
            out: dict = {"found": len(page)}
            for i, s in enumerate(page):
                f = "%Y-%m-%d %H:%M:%S"
                out.update({f"items[{i}].Channel": s.channel - 1 + 0, f"items[{i}].StartTime": utc_to_local(s.start).strftime(f),
                            f"items[{i}].EndTime": utc_to_local(s.end).strftime(f), f"items[{i}].Length": s.size,
                            f"items[{i}].Type": "dav", f"items[{i}].FilePath": s.path,
                            f"items[{i}].Flags[0]": "Event" if s.kind == "motion" else "Timing"})
            return kv(out)
        if a in ("close", "destroy"):
            found.pop(obj, None)
        return PlainTextResponse("OK\r\n")

    @app.get("/cgi-bin/{rest:path}")
    async def other(rest: str):
        return PlainTextResponse("Error\r\nBad Request!\r\n", status_code=404)

    @app.get("/", response_class=HTMLResponse)
    async def root():
        return f"<html><head><title>{spec.model}</title></head><body><h3>Intelbras {spec.model}</h3><p>Servico web (simulador)</p></body></html>"

    return app
