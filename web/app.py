"""Portal web estilo VMS: login (CSRF + bloqueio), grade ao vivo (MJPEG), PTZ, gravacoes, eventos (WebSocket)."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import secrets
from datetime import datetime, timedelta
from pathlib import Path

import cv2
from fastapi import Depends, FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from core.db import to_naive_utc
from core.event_bus import EventType
from core.fmt import utc_to_local
from core.security import LoginLimiter, Session, SessionManager, check_csrf

log = logging.getLogger(__name__)
HERE = Path(__file__).parent
COOKIE = "cftv_session"
PRE = "cftv_pre"


def _ip(request) -> str:  # noqa: ANN001
    ip = request.client.host if request.client else "?"
    if ip in ("127.0.0.1", "::1"):
        fwd = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        return fwd or ip
    return ip


def _is_external(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
        return not (a.is_private or a.is_loopback or a.is_link_local)
    except ValueError:
        return False


def create_portal(ctx) -> FastAPI:  # noqa: ANN001
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html"]))
    sec = ctx.settings.get("security", {})
    sessions = SessionManager(sec.get("session_ttl_s", 28800))
    limiter = LoginLimiter(sec.get("lockout_attempts", 5), sec.get("lockout_window_s", 900),
                           sec.get("lockout_duration_s", 1800))
    devices = ctx.manager.list()

    def render(name: str, request: Request, **kw) -> HTMLResponse:
        s = getattr(request.state, "session", None)
        return HTMLResponse(env.get_template(name).render(session=s, csrf=s.csrf if s else "", ctx=ctx,
                                                          brand=_brand(), **kw))

    def _brand() -> str:
        bs = {d.spec.brand.value for d in devices}
        return next(iter(bs)) if len(bs) == 1 else "all"

    # ------------------------------------------------------------------ seguranca
    @app.middleware("http")
    async def guard(request: Request, call_next):  # noqa: ANN001
        ip = _ip(request)
        request.state.ip = ip
        request.state.session = sessions.get(request.cookies.get(COOKIE))
        resp = await call_next(request)
        h = resp.headers
        h["X-Content-Type-Options"] = "nosniff"
        h["X-Frame-Options"] = "DENY"
        h["Referrer-Policy"] = "no-referrer"
        h["Cache-Control"] = "no-store"
        h["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data: blob:; media-src 'self' blob:; "
                                        "style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
                                        "connect-src 'self' ws: wss:; frame-ancestors 'none'")
        if _is_external(ip) and not request.url.path.startswith(("/static", "/stream")):
            ctx.db.add_log("operation", f"Acesso externo {request.method} {request.url.path}", ip=ip,
                           user=getattr(request.state.session, "username", "") if request.state.session else "")
        return resp

    def need_session(request: Request) -> Session:
        s = request.state.session
        if s is None:
            raise HTTPException(status_code=401)
        return s

    def need_control(request: Request, s: Session = Depends(need_session)) -> Session:
        if s.role not in ("admin", "operator"):
            raise HTTPException(status_code=403, detail="sem permissao")
        if not check_csrf(s, request.headers.get("x-csrf-token")):
            raise HTTPException(status_code=403, detail="CSRF invalido")
        return s

    def cam_of(dev_id: str, ch: int):
        try:
            return ctx.manager.camera(dev_id, ch)
        except KeyError:
            raise HTTPException(status_code=404) from None

    # ------------------------------------------------------------------ login
    @app.exception_handler(HTTPException)
    async def http_exc(request: Request, exc: HTTPException):
        if exc.status_code == 401 and "text/html" in request.headers.get("accept", ""):
            return RedirectResponse("/login", status_code=303)
        return JSONResponse({"error": exc.detail or "erro"}, status_code=exc.status_code)

    @app.get("/login", response_class=HTMLResponse)
    async def login_page(request: Request):
        tok = secrets.token_urlsafe(16)
        r = render("login.html", request, pre=tok, error="")
        r.set_cookie(PRE, tok, httponly=True, samesite="strict", max_age=900)
        return r

    @app.post("/login")
    async def login(request: Request, username: str = Form(""), password: str = Form(""), pre: str = Form("")):
        ip = request.state.ip
        if not pre or pre != request.cookies.get(PRE):
            raise HTTPException(status_code=403, detail="CSRF invalido")
        ok_lock, wait = limiter.check(f"ip:{ip}")
        ok_lock2, wait2 = limiter.check(f"user:{username}")
        if not (ok_lock and ok_lock2):
            ctx.bus.publish(EventType.LOGIN_LOCKED, "", 0, ip=ip, user=username, proto="web")
            tok = secrets.token_urlsafe(16)
            r = render("login.html", request, pre=tok,
                       error=f"Muitas tentativas. Tente novamente em {int(max(wait, wait2) // 60) + 1} min.")
            r.status_code = 429
            r.set_cookie(PRE, tok, httponly=True, samesite="strict", max_age=900)
            return r
        user = None
        for d in devices:
            user = d.users.verify(username, password)
            if user:
                break
        if user is None or not user.enabled:
            locked = limiter.fail(f"ip:{ip}") | limiter.fail(f"user:{username}")
            ctx.bus.publish(EventType.LOGIN_LOCKED if locked else EventType.LOGIN_FAIL, "", 0, ip=ip, user=username, proto="web")
            ctx.db.add_log("alarm", f"Login web falhou ({username})", user=username, ip=ip)
            tok = secrets.token_urlsafe(16)
            await asyncio.sleep(0.6)  # atrasa forca bruta
            r = render("login.html", request, pre=tok, error="Usuario ou senha invalidos.")
            r.status_code = 401
            r.set_cookie(PRE, tok, httponly=True, samesite="strict", max_age=900)
            return r
        limiter.success(f"ip:{ip}")
        limiter.success(f"user:{username}")
        s = sessions.create(user.username, user.role, ip)
        ctx.bus.publish(EventType.LOGIN_OK, "", 0, ip=ip, user=username, proto="web")
        ctx.db.add_log("operation", "Login web", user=username, ip=ip)
        r = RedirectResponse("/live", status_code=303)
        r.set_cookie(COOKIE, s.token, httponly=True, samesite="strict", max_age=int(sessions.ttl),
                     secure=request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https")
        r.delete_cookie(PRE)
        return r

    @app.post("/logout")
    async def logout(request: Request, s: Session = Depends(need_session)):
        if not check_csrf(s, request.headers.get("x-csrf-token")):
            raise HTTPException(status_code=403)
        sessions.destroy(s.token)
        r = JSONResponse({"ok": True})
        r.delete_cookie(COOKIE)
        return r

    @app.get("/")
    async def index(request: Request):
        return RedirectResponse("/live" if request.state.session else "/login", status_code=303)

    # ------------------------------------------------------------------ paginas
    def cams() -> list[dict]:
        return [{"dev": d.id, "ch": ch, "name": c.name, "brand": d.spec.brand.value, "model": d.spec.model,
                 "device": d.spec.name} for d in devices for ch, c in sorted(d.cameras.items())]

    @app.get("/live", response_class=HTMLResponse)
    async def live(request: Request, s: Session = Depends(need_session)):
        return render("live.html", request, cams=cams(), page="live", can_control=s.role in ("admin", "operator"))

    @app.get("/playback", response_class=HTMLResponse)
    async def playback(request: Request, s: Session = Depends(need_session)):
        return render("playback.html", request, cams=cams(), page="playback", today=datetime.now().strftime("%Y-%m-%d"))

    @app.get("/events", response_class=HTMLResponse)
    async def events_page(request: Request, s: Session = Depends(need_session)):
        return render("events.html", request, page="events")

    # ------------------------------------------------------------------ video
    @app.get("/stream/{dev}/{ch}.mjpg")
    async def mjpg(dev: str, ch: int, request: Request, q: str = "sub", s: Session = Depends(need_session)):
        cam = cam_of(dev, ch)
        stream = "main" if q == "main" else "sub"
        fps = 10 if stream == "sub" else 15

        async def gen():
            last = 0
            while not await request.is_disconnected():
                frame = cam.get_frame(stream) if cam.is_online else None
                if frame is not None:
                    ok, buf = await asyncio.to_thread(cv2.imencode, ".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
                    if ok:
                        b = buf.tobytes()
                        yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(b)).encode() + b"\r\n\r\n" + b + b"\r\n"
                await asyncio.sleep(1.0 / fps)
        return StreamingResponse(gen(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/snapshot/{dev}/{ch}.jpg")
    async def snap(dev: str, ch: int, s: Session = Depends(need_session)):
        jpg = cam_of(dev, ch).snapshot_jpeg("main")
        if jpg is None:
            raise HTTPException(status_code=503, detail="camera indisponivel")
        return Response(jpg, media_type="image/jpeg", headers={"Content-Disposition": f'attachment; filename="{dev}_ch{ch}.jpg"'})

    # ------------------------------------------------------------------ API interna
    @app.get("/api/v1/status")
    async def status(s: Session = Depends(need_session)):
        return {"devices": ctx.manager.summary(), "public_url": ctx.public_url, "user": s.username, "role": s.role}

    @app.post("/api/v1/ptz")
    async def ptz(request: Request, s: Session = Depends(need_control)):
        b = await request.json()
        cam = cam_of(b["dev"], int(b["ch"]))
        if b.get("action") == "stop":
            cam.ptz_stop()
        elif b.get("action") == "home":
            cam.ptz_home()
        else:
            cam.ptz_continuous(float(b.get("x", 0)), float(b.get("y", 0)), float(b.get("z", 0)))
        return {"ok": True}

    @app.post("/api/v1/record")
    async def record(request: Request, s: Session = Depends(need_control)):
        b = await request.json()
        ok = ctx.set_recording(b["dev"], int(b["ch"]), bool(b["on"]))
        ctx.db.add_log("operation", f"Gravacao manual {'ligada' if b['on'] else 'desligada'} CH{b['ch']}", b["dev"], s.username, s.ip)
        return {"ok": ok}

    @app.post("/api/v1/reboot")
    async def reboot(request: Request, s: Session = Depends(need_control)):
        if s.role != "admin":
            raise HTTPException(status_code=403, detail="somente admin")
        b = await request.json()
        sec_ = ctx.reboot_camera(b["dev"], int(b["ch"]), s.username) if b.get("ch") else ctx.reboot_system(b["dev"], s.username)
        return {"ok": True, "seconds": round(sec_, 1)}

    @app.get("/api/v1/recordings")
    async def recordings(dev: str, ch: int, date: str, s: Session = Depends(need_session)):
        try:
            day = datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="data invalida") from None
        from core.fmt import local_to_utc
        st, en = local_to_utc(day), local_to_utc(day + timedelta(days=1))
        segs = ctx.db.segments_between(dev, ch, st, en)
        return [{"id": x.id, "kind": x.kind, "start": utc_to_local(x.start).strftime("%H:%M:%S"),
                 "end": utc_to_local(x.end).strftime("%H:%M:%S") if x.end else "", "size": x.size} for x in segs]

    @app.get("/rec/{seg_id}")
    async def rec_file(seg_id: int, s: Session = Depends(need_session)):
        from core.db import Segment
        with ctx.db.session() as ses:
            seg = ses.get(Segment, seg_id)
        if seg is None or not Path(seg.path).exists():
            raise HTTPException(status_code=404)
        return FileResponse(seg.path, media_type="video/mp4")

    @app.get("/api/v1/events")
    async def events_api(limit: int = 100, s: Session = Depends(need_session)):
        rows = ctx.db.events(limit=min(limit, 500))
        return [{"ts": utc_to_local(r.ts).strftime("%d/%m/%Y %H:%M:%S"), "device": r.device_id, "channel": r.channel,
                 "type": r.type, "state": r.state, "description": r.description} for r in rows]

    @app.websocket("/ws")
    async def ws(sock: WebSocket):
        sess = sessions.get(sock.cookies.get(COOKIE))
        if sess is None:
            await sock.close(code=4401)
            return
        await sock.accept()
        q, unsub = ctx.bus.subscribe_async(asyncio.get_running_loop(), [
            EventType.MOTION_START, EventType.MOTION_STOP, EventType.VIDEO_LOSS, EventType.VIDEO_RESTORED,
            EventType.CAMERA_REBOOTING, EventType.CAMERA_ONLINE, EventType.SYSTEM_REBOOTING, EventType.SYSTEM_ONLINE,
            EventType.RECORDING_START, EventType.RECORDING_STOP])
        try:
            while True:
                ev = await q.get()
                await sock.send_json(ev.to_dict())
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            unsub()

    return app
