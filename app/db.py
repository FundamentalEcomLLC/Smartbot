import logging
import time
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .config import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

_sql_logger = logging.getLogger("sql-profiler")


@event.listens_for(engine, "before_cursor_execute")
def _before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    context._query_start_time = time.perf_counter()
    _sql_logger.debug("SQL start | %s", (statement or "").strip().replace("\n", " ")[:400])


@event.listens_for(engine, "after_cursor_execute")
def _after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
    duration_ms = (time.perf_counter() - getattr(context, "_query_start_time", time.perf_counter())) * 1000
    rowcount = cursor.rowcount if cursor else -1
    _sql_logger.info("SQL done | %.2f ms | rows=%s", duration_ms, rowcount)


@contextmanager
def db_session() -> Generator:
    """Provide a transactional scope around a series of operations."""

    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
