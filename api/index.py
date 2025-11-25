from __future__ import annotations

import sys
from pathlib import Path

from mangum import Mangum

# Ensure the project package is importable when running inside Vercel's serverless worker
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.main import app as fastapi_app  # noqa: E402  (import after sys.path mutation)

# Expose the FastAPI instance for local tooling (e.g., pytest, linters)
app = fastapi_app

# Vercel's Python runtime looks for a callable named `handler`
handler = Mangum(fastapi_app)
