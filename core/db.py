"""Banco SQLite (SQLAlchemy 2.0): segmentos gravados, eventos e logs.

Todas as datas sao guardadas em UTC *naive* (sem tzinfo) para o SQLite;
use ``to_naive_utc`` ao consultar.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from sqlalchemy import DateTime, Float, Integer, String, Text, create_engine, delete, event, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def to_naive_utc(dt: datetime) -> datetime:
    """Converte qualquer datetime para UTC naive."""
    if dt.tzinfo is None:
        return dt
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class Segment(Base):
    """Um arquivo MP4 gravado (contínuo, movimento ou manual)."""

    __tablename__ = "segments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    channel: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="continuous")  # continuous|motion|manual
    start: Mapped[datetime] = mapped_column(DateTime, index=True)
    end: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    size: Mapped[int] = mapped_column(Integer, default=0)
    disk: Mapped[str] = mapped_column(String(16), default="hdd1", index=True)
    path: Mapped[str] = mapped_column(Text)


class EventRow(Base):
    """Evento persistido (movimento, perda de video, login, reboot...)."""

    __tablename__ = "events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    device_id: Mapped[str] = mapped_column(String(32), index=True)
    channel: Mapped[int] = mapped_column(Integer, default=0)
    type: Mapped[str] = mapped_column(String(32), index=True)
    state: Mapped[str] = mapped_column(String(16), default="")  # start|stop|...
    description: Mapped[str] = mapped_column(Text, default="")
    thumb: Mapped[str] = mapped_column(Text, default="")


class LogRow(Base):
    """Linha de log: Sistema / Operacao / Alarme / Excecao."""

    __tablename__ = "logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime, index=True)
    device_id: Mapped[str] = mapped_column(String(32), default="", index=True)
    category: Mapped[str] = mapped_column(String(16), index=True)  # system|operation|alarm|exception
    message: Mapped[str] = mapped_column(Text)
    user: Mapped[str] = mapped_column(String(64), default="")
    ip: Mapped[str] = mapped_column(String(64), default="")


LOG_CATEGORIES = ("system", "operation", "alarm", "exception")


class Database:
    """Acesso ao SQLite. Seguro para varias threads (uma sessao por operacao)."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) == ":memory:":
            url = "sqlite+pysqlite://"
            from sqlalchemy.pool import StaticPool
            self.engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
        else:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.engine = create_engine(f"sqlite+pysqlite:///{path}", connect_args={"check_same_thread": False})

            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi_conn, _):  # noqa: ANN001
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()

        Base.metadata.create_all(self.engine)
        self._Session = sessionmaker(self.engine, expire_on_commit=False)

    def session(self) -> Session:
        return self._Session()

    # ------------------------------------------------------------------ segmentos
    def add_segment(self, device_id: str, channel: int, kind: str, start: datetime, path: str,
                    end: datetime | None = None, size: int = 0, disk: str = "hdd1") -> int:
        with self.session() as s:
            seg = Segment(device_id=device_id, channel=channel, kind=kind, start=to_naive_utc(start),
                          end=to_naive_utc(end) if end else None, size=size, disk=disk, path=path)
            s.add(seg)
            s.commit()
            return seg.id

    def update_segment(self, seg_id: int, *, end: datetime | None = None, size: int | None = None) -> None:
        with self.session() as s:
            seg = s.get(Segment, seg_id)
            if seg:
                if end is not None:
                    seg.end = to_naive_utc(end)
                if size is not None:
                    seg.size = size
                s.commit()

    def segments_between(self, device_id: str, channel: int, start: datetime, end: datetime,
                         kinds: Iterable[str] | None = None, limit: int = 5000, offset: int = 0) -> list[Segment]:
        """Segmentos que se sobrepoem ao intervalo [start, end] (UTC), ordenados por inicio."""
        st, en = to_naive_utc(start), to_naive_utc(end)
        q = (select(Segment).where(Segment.device_id == device_id, Segment.channel == channel,
                                   Segment.start <= en,
                                   (Segment.end.is_(None)) | (Segment.end >= st))
             .order_by(Segment.start).limit(limit).offset(offset))
        if kinds:
            q = q.where(Segment.kind.in_(list(kinds)))
        with self.session() as s:
            return list(s.scalars(q))

    def get_segment_by_path(self, path: str) -> Segment | None:
        with self.session() as s:
            return s.scalars(select(Segment).where(Segment.path == path)).first()

    def days_with_recordings(self, device_id: str, channel: int, year: int, month: int) -> list[int]:
        """Dias do mes (UTC) com gravacao, para o calendario do portal."""
        first = datetime(year, month, 1)
        nxt = datetime(year + (month == 12), (month % 12) + 1, 1)
        with self.session() as s:
            rows = s.scalars(select(Segment.start).where(
                Segment.device_id == device_id, Segment.channel == channel,
                Segment.start >= first, Segment.start < nxt)).all()
        return sorted({r.day for r in rows})

    def total_size(self, disk: str | None = None, device_id: str | None = None) -> int:
        q = select(func.coalesce(func.sum(Segment.size), 0))
        if disk:
            q = q.where(Segment.disk == disk)
        if device_id:
            q = q.where(Segment.device_id == device_id)
        with self.session() as s:
            return int(s.scalar(q) or 0)

    def oldest_segments(self, disk: str, limit: int = 20, device_id: str | None = None) -> list[Segment]:
        """Mais antigos primeiro (para o overwrite por cota)."""
        q = select(Segment).where(Segment.disk == disk).order_by(Segment.start).limit(limit)
        if device_id:
            q = q.where(Segment.device_id == device_id)
        with self.session() as s:
            return list(s.scalars(q))

    def delete_segments(self, ids: Sequence[int]) -> None:
        if not ids:
            return
        with self.session() as s:
            s.execute(delete(Segment).where(Segment.id.in_(list(ids))))
            s.commit()

    def clear_disk(self, disk: str, device_id: str | None = None) -> list[str]:
        """Remove do indice todos os segmentos do disco ("Formatar"). Retorna os caminhos."""
        cond = [Segment.disk == disk] + ([Segment.device_id == device_id] if device_id else [])
        with self.session() as s:
            paths = list(s.scalars(select(Segment.path).where(*cond)))
            s.execute(delete(Segment).where(*cond))
            s.commit()
        return paths

    # ------------------------------------------------------------------ eventos
    def add_event(self, ts: datetime, device_id: str, channel: int, type: str, state: str = "",
                  description: str = "", thumb: str = "") -> int:
        with self.session() as s:
            row = EventRow(ts=to_naive_utc(ts), device_id=device_id, channel=channel, type=type,
                           state=state, description=description, thumb=thumb)
            s.add(row)
            s.commit()
            return row.id

    def events(self, device_id: str | None = None, channel: int | None = None,
               types: Iterable[str] | None = None, start: datetime | None = None,
               end: datetime | None = None, limit: int = 200, offset: int = 0) -> list[EventRow]:
        q = select(EventRow).order_by(EventRow.ts.desc()).limit(limit).offset(offset)
        if device_id:
            q = q.where(EventRow.device_id == device_id)
        if channel:
            q = q.where(EventRow.channel == channel)
        if types:
            q = q.where(EventRow.type.in_(list(types)))
        if start:
            q = q.where(EventRow.ts >= to_naive_utc(start))
        if end:
            q = q.where(EventRow.ts <= to_naive_utc(end))
        with self.session() as s:
            return list(s.scalars(q))

    # ------------------------------------------------------------------ logs
    def add_log(self, category: str, message: str, device_id: str = "", user: str = "", ip: str = "",
                ts: datetime | None = None) -> int:
        if category not in LOG_CATEGORIES:
            category = "system"
        with self.session() as s:
            row = LogRow(ts=to_naive_utc(ts) if ts else now_utc(), device_id=device_id,
                         category=category, message=message, user=user, ip=ip)
            s.add(row)
            s.commit()
            return row.id

    def logs(self, device_id: str | None = None, category: str | None = None, text: str | None = None,
             start: datetime | None = None, end: datetime | None = None,
             limit: int = 500, offset: int = 0) -> list[LogRow]:
        q = select(LogRow).order_by(LogRow.ts.desc()).limit(limit).offset(offset)
        if device_id:
            q = q.where(LogRow.device_id == device_id)
        if category:
            q = q.where(LogRow.category == category)
        if text:
            q = q.where(LogRow.message.ilike(f"%{text}%"))
        if start:
            q = q.where(LogRow.ts >= to_naive_utc(start))
        if end:
            q = q.where(LogRow.ts <= to_naive_utc(end))
        with self.session() as s:
            return list(s.scalars(q))

    def close(self) -> None:
        self.engine.dispose()
