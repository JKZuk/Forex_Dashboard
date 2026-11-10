"""Database engine, session factory and a dialect-portable upsert.

Production target is the existing local MySQL server (schema `fx_dashboard`).
Everything also runs on SQLite (set DATABASE_URL) so tests need no server.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from typing import Iterable, Iterator, Sequence

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = settings.database_url
        kwargs = {"pool_pre_ping": True, "future": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        else:
            kwargs["pool_recycle"] = 3600
        _engine = create_engine(url, **kwargs)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def SessionLocal() -> Session:
    get_engine()
    return _SessionLocal()


@contextmanager
def session_scope() -> Iterator[Session]:
    """Commit on success, roll back on error."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create the schema (MySQL only) and all tables. Safe to call repeatedly."""
    from . import models  # noqa: F401  (registers tables on Base.metadata)

    url = settings.database_url
    if url.startswith("mysql") and not settings.database_url_override:
        if not re.fullmatch(r"[A-Za-z0-9_]+", settings.db_name):
            raise ValueError(f"Unsafe DB_NAME: {settings.db_name!r}")
        server = create_engine(settings.server_url, future=True)
        with server.begin() as conn:
            conn.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS `{settings.db_name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
        server.dispose()
    Base.metadata.create_all(get_engine())


def upsert(
    session: Session,
    model,
    rows: Iterable[dict],
    update_cols: Sequence[str] | None = None,
    chunk_size: int = 500,
) -> int:
    """Insert rows, updating non-key columns when the primary key already exists.

    Works on MySQL (ON DUPLICATE KEY UPDATE) and SQLite (ON CONFLICT DO UPDATE).
    Every row dict must contain the same keys. Returns the number of rows sent.
    """
    rows = list(rows)
    if not rows:
        return 0
    table = model.__table__
    pk_cols = [c.name for c in table.primary_key.columns]
    if update_cols is None:
        update_cols = [k for k in rows[0].keys() if k not in pk_cols]
    dialect = session.get_bind().dialect.name

    for i in range(0, len(rows), chunk_size):
        chunk = rows[i : i + chunk_size]
        if dialect == "mysql":
            from sqlalchemy.dialects.mysql import insert

            stmt = insert(table)
            if update_cols:
                stmt = stmt.on_duplicate_key_update({c: stmt.inserted[c] for c in update_cols})
            else:
                stmt = stmt.prefix_with("IGNORE")
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert

            stmt = insert(table)
            if update_cols:
                stmt = stmt.on_conflict_do_update(
                    index_elements=pk_cols, set_={c: stmt.excluded[c] for c in update_cols}
                )
            else:
                stmt = stmt.on_conflict_do_nothing(index_elements=pk_cols)
        else:
            raise NotImplementedError(f"upsert not implemented for dialect {dialect}")
        session.execute(stmt, chunk)
    return len(rows)
