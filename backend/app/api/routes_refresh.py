"""Refresh control, data status and health endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from .. import pipeline
from ..config import PROJECT_ROOT, settings
from ..ingest import SOURCES
from ..models import RefreshRun, Score
from . import util
from .schemas import (
    HealthOut,
    RefreshJobOut,
    RefreshRequest,
    RefreshStartOut,
    RefreshStatusOut,
)

router = APIRouter()

# A "running" row older than this with no refresh active in this process is
# treated as interrupted (e.g. the server was stopped mid-refresh).
STALE_AFTER = timedelta(minutes=45)
FRONTEND_INDEX = PROJECT_ROOT / "frontend" / "dist" / "index.html"


@router.post("/refresh", response_model=RefreshStartOut, status_code=202)
def start_refresh(body: Optional[RefreshRequest] = Body(None)) -> RefreshStartOut:
    sources = (body.sources if body else None) or None
    if sources:
        unknown = [s for s in sources if s not in SOURCES]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown source(s): {', '.join(unknown)}. Valid: {', '.join(SOURCES)}",
            )
    started = pipeline.refresh_in_background(sources)
    return RefreshStartOut(
        started=started,
        running=True if started else pipeline.is_running(),
        sources=sources or list(SOURCES),
        message="Refresh started." if started else "A refresh is already running.",
    )


@router.get("/refresh/status", response_model=RefreshStatusOut)
def refresh_status(session: Session = Depends(util.get_session)) -> RefreshStatusOut:
    now = datetime.utcnow()
    latest_ids = select(func.max(RefreshRun.id)).group_by(RefreshRun.job)
    latest = {
        r.job: r
        for r in session.execute(select(RefreshRun).where(RefreshRun.id.in_(latest_ids))).scalars()
    }
    last_ok = dict(
        session.execute(
            select(RefreshRun.job, func.max(RefreshRun.finished_at))
            .where(RefreshRun.status == "ok")
            .group_by(RefreshRun.job)
        ).all()
    )
    last_finished = session.execute(select(func.max(RefreshRun.finished_at))).scalar()

    in_process = pipeline.is_running()
    external = False
    jobs: list[RefreshJobOut] = []
    order = list(SOURCES) + ["scoring"]
    for job in order + sorted(j for j in latest if j not in order):
        run = latest.get(job)
        if run is None:
            jobs.append(RefreshJobOut(job=job, status="never"))
            continue
        status, message = run.status, run.message or ""
        if status == "running" and not in_process:
            if now - run.started_at > STALE_AFTER:
                status, message = "interrupted", "Run did not finish (server stopped mid-refresh?)."
            else:
                external = True  # e.g. `python -m app.cli refresh` in another process
        jobs.append(
            RefreshJobOut(
                job=job,
                status=status,
                started_at=run.started_at,
                finished_at=run.finished_at,
                rows=run.rows or 0,
                message=message,
                last_success_at=last_ok.get(job),
            )
        )
    ok_times = [t for t in last_ok.values() if t is not None]
    return RefreshStatusOut(
        running=in_process or external,
        server_time=now,
        last_success_at=max(ok_times) if ok_times else None,
        last_finished_at=last_finished,
        sources=list(SOURCES),
        jobs=jobs,
        auto_refresh_minutes=settings.refresh_interval_minutes,
    )


@router.get("/health", response_model=HealthOut)
def health(session: Session = Depends(util.get_session)) -> HealthOut:
    database, has_data = "ok", False
    try:
        session.execute(text("SELECT 1"))
        has_data = session.execute(select(Score.region).limit(1)).first() is not None
    except Exception:  # noqa: BLE001 - health must answer even when the DB is down
        database = "error"
    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        database=database,
        dialect=session.get_bind().dialect.name,
        has_data=has_data,
        server_time=datetime.utcnow(),
        frontend_built=FRONTEND_INDEX.exists(),
    )
