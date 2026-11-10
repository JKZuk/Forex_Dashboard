"""FastAPI application: REST API under /api + the built dashboard at /.

Run from backend/:  .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
(or ./run.sh from the project root).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import SQLAlchemyError

from . import pipeline
from .api import api_router
from .config import PROJECT_ROOT, settings
from .db import init_db

log = logging.getLogger("app")
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"

_NO_FRONTEND = """<!doctype html><meta charset="utf-8"><title>FX Sentiment Dashboard</title>
<body style="font-family:system-ui;margin:3rem;max-width:40rem">
<h1>Frontend not built</h1>
<p>The API is running (<a href="/api/health">/api/health</a>, <a href="/api/docs">/api/docs</a>),
but <code>frontend/dist</code> does not exist. Run <code>./run.sh</code> from the project root,
or <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>, then reload.</p></body>"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
    except Exception:  # noqa: BLE001 - keep serving so /api/health can report the problem
        log.exception("init_db failed; check the database settings in .env")
    scheduler = None
    if settings.refresh_interval_minutes > 0:
        from apscheduler.schedulers.background import BackgroundScheduler

        scheduler = BackgroundScheduler(timezone="UTC")
        scheduler.add_job(
            pipeline.refresh,
            "interval",
            minutes=settings.refresh_interval_minutes,
            id="auto_refresh",
            max_instances=1,
            coalesce=True,
        )
        scheduler.start()
        log.info("auto refresh every %s min", settings.refresh_interval_minutes)
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


app = FastAPI(
    title="FX Sentiment Dashboard",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)


@app.exception_handler(SQLAlchemyError)
async def _db_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
    log.error("database error on %s: %s", request.url.path, exc)
    return JSONResponse(
        status_code=503,
        content={"detail": "Database unavailable. Check the database settings in .env."},
    )


app.include_router(api_router, prefix="/api")


@app.api_route("/api/{path:path}", include_in_schema=False)
async def _api_not_found(path: str) -> JSONResponse:
    # Without this, unknown /api paths would fall through to the static mount.
    return JSONResponse(status_code=404, content={"detail": f"No such endpoint: /api/{path}"})


if (FRONTEND_DIST / "index.html").exists():
    # Hash-based routing in the frontend, so no SPA fallback is needed.
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
else:

    @app.get("/", include_in_schema=False)
    async def _no_frontend() -> HTMLResponse:
        return HTMLResponse(_NO_FRONTEND)
