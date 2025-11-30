from __future__ import annotations

import sys
from pathlib import Path

from mangum import Mangum

# Ensure the project package is importable when running inside Vercel's serverless worker
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))


# --- Run Alembic migrations on cold start (Vercel serverless) ---
import subprocess
import os
import threading

_alembic_lock = threading.Lock()
_alembic_ran = False

def run_alembic_once():
    global _alembic_ran
    with _alembic_lock:
        if not _alembic_ran:
            try:
                subprocess.run([
                    sys.executable, "-m", "alembic", "upgrade", "head"
                ], check=True, cwd=str(ROOT_DIR), env=os.environ.copy())
                _alembic_ran = True
            except Exception as exc:
                print(f"[Alembic] Migration failed: {exc}", file=sys.stderr)
                # Optionally, raise or log

run_alembic_once()

from app.main import app as fastapi_app  # noqa: E402  (import after sys.path mutation)

# Expose the FastAPI instance for local tooling (e.g., pytest, linters)
app = fastapi_app

# Vercel's Python runtime looks for a callable named `handler`
handler = Mangum(fastapi_app)
