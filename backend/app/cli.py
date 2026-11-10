"""Command line entry point.

    python -m app.cli init-db                 create schema + tables
    python -m app.cli refresh                 run all sources, then score
    python -m app.cli refresh fx_prices cot   run only the named sources, then score
    python -m app.cli score                   recompute today's scores only
    python -m app.cli backfill 2023-01-01     weekly historical scores since a date
"""
from __future__ import annotations

import logging
import sys
from datetime import date


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if not argv:
        print(__doc__)
        return 1
    cmd, args = argv[0], argv[1:]

    from . import pipeline
    from .db import init_db, session_scope

    if cmd == "init-db":
        init_db()
        print("Database ready.")
    elif cmd == "refresh":
        runs = pipeline.refresh(args or None)
        for r in runs:
            print(f"{r.job:15s} {r.status:6s} rows={r.rows:<7d} {r.message[:140]}")
        return 0 if all(r.status == "ok" for r in runs) else 2
    elif cmd == "score":
        init_db()
        r = pipeline.run_scoring()
        print(f"{r.job} {r.status} rows={r.rows} {r.message}")
        return 0 if r.status == "ok" else 2
    elif cmd == "backfill":
        from . import scoring

        init_db()
        start = date.fromisoformat(args[0]) if args else date(date.today().year - 3, 1, 1)
        with session_scope() as session:
            n = scoring.backfill(session, start=start, end=date.today())
        print(f"Backfilled {n} score rows since {start}.")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
