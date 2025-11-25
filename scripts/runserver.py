"""Container/server entrypoint that optionally runs migrations before starting Gunicorn."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]


def _truthy(value: str | None) -> bool:
    return value is not None and value.lower() in {"1", "true", "yes", "on"}


def main() -> None:
    run_migrations = _truthy(os.environ.get("RUN_DB_MIGRATIONS", "0"))
    workers = os.environ.get("WORKERS", "4")
    host = os.environ.get("HOST", "0.0.0.0")
    port = os.environ.get("PORT", "8000")

    env = os.environ.copy()

    if run_migrations:
        print("→ Running Alembic migrations")
        subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            check=True,
            cwd=str(ROOT_DIR),
            env=env,
        )

    print("→ Starting Gunicorn")
    subprocess.run(
        [
            "gunicorn",
            "app.main:app",
            "-k",
            "uvicorn.workers.UvicornWorker",
            "-w",
            workers,
            "-b",
            f"{host}:{port}",
        ],
        check=True,
        cwd=str(ROOT_DIR),
        env=env,
    )


if __name__ == "__main__":
    main()
