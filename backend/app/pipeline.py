"""Refresh pipeline: run every ingest source, then recompute scores.

One failing source never blocks the others; each step is logged in refresh_runs.
"""
from __future__ import annotations

import importlib
import logging
import threading
import traceback
from datetime import datetime

from .db import SessionLocal, init_db
from .ingest import SOURCES, IngestResult
from .models import RefreshRun

log = logging.getLogger("pipeline")
_lock = threading.Lock()


def is_running() -> bool:
    return _lock.locked()


def _run_job(job: str, fn) -> RefreshRun:
    """Run fn(session) -> IngestResult in its own transaction and log the outcome."""
    session = SessionLocal()
    run = RefreshRun(job=job, started_at=datetime.utcnow(), status="running")
    session.add(run)
    session.commit()
    try:
        result = fn(session) or IngestResult()
        session.commit()
        run.status, run.rows, run.message = "ok", result.rows, result.message[:4000]
    except Exception as exc:  # noqa: BLE001 - a source failure must not stop the pipeline
        session.rollback()
        log.error("job %s failed: %s", job, traceback.format_exc())
        run.status, run.message = "error", f"{type(exc).__name__}: {exc}"[:4000]
    run.finished_at = datetime.utcnow()
    session.merge(run)
    session.commit()
    session.close()
    log.info("job %-15s %-5s rows=%s %s", job, run.status, run.rows, run.message[:200])
    return run


def run_scoring() -> RefreshRun:
    from . import scoring

    def _score(session):
        n = scoring.run(session)
        return IngestResult(rows=n, message="scores recomputed")

    return _run_job("scoring", _score)


def refresh(sources: list[str] | None = None, score: bool = True) -> list[RefreshRun]:
    """Run the pipeline. Returns the log rows. No-op if a refresh is already running."""
    if not _lock.acquire(blocking=False):
        return []
    try:
        init_db()
        runs = []
        for name in sources or SOURCES:
            module = importlib.import_module(f"app.ingest.{name}")
            runs.append(_run_job(name, module.run))
        if score:
            runs.append(run_scoring())
        return runs
    finally:
        _lock.release()


def refresh_in_background(sources: list[str] | None = None) -> bool:
    """Start a refresh on a daemon thread. Returns False if one is already running."""
    if is_running():
        return False
    threading.Thread(target=refresh, args=(sources,), daemon=True, name="refresh").start()
    return True
