"""Deployment smoke test to ensure the production database is ready."""

from __future__ import annotations

import sys
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text

ROOT_DIR = Path(__file__).resolve().parents[1]


def run_alembic_current() -> None:
    cfg = Config(str(ROOT_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT_DIR / "alembic"))
    command.current(cfg)


def run_schema_probe() -> None:
    sys.path.insert(0, str(ROOT_DIR))
    from app.db import SessionLocal  # imported after sys.path mutation

    with SessionLocal() as session:
        session.execute(text("SELECT 1 FROM projects LIMIT 1"))


def main() -> None:
    run_alembic_current()
    run_schema_probe()
    print("Smoke test passed: migrations applied and projects table reachable.")


if __name__ == "__main__":
    main()
