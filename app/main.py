import logging
import os
import sys
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.append(str(ROOT_DIR))

from app.config import get_settings
from app.routers.admin import admin_router
from app.routers.api import api_router

settings = get_settings()
debug_mode = settings.env.lower() in {"development", "test"}


def _configure_logging() -> None:
    """Ensure server + background tasks emit structured logs."""

    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        force=True,
    )
    logging.getLogger("uvicorn").setLevel(log_level)
    logging.getLogger("uvicorn.access").setLevel("WARNING")
    logging.getLogger("request").setLevel(log_level)
    logging.getLogger("app.services.crawler").setLevel(log_level)
    logging.getLogger("sql-profiler").setLevel(os.environ.get("SQL_LOG_LEVEL", log_level))
    logging.getLogger("httpx").setLevel(os.environ.get("HTTPX_LOG_LEVEL", "WARNING").upper())


_configure_logging()

app = FastAPI(title="Website Crawler + AI Chatbot", debug=debug_mode)


@app.middleware("http")
async def log_requests(request, call_next):
    logger = logging.getLogger("request")
    logger.info("--> %s %s from %s", request.method, request.url.path, request.client.host if request.client else "?")
    start = time.perf_counter()
    response = await call_next(request)
    duration = (time.perf_counter() - start) * 1000
    logger.info("<-- %s %s %s %.2fms", request.method, request.url.path, response.status_code, duration)
    return response

origin_candidates: list[str] = []
if settings.cors_allow_origins:
    origin_candidates.extend(
        origin.strip()
        for origin in settings.cors_allow_origins.split(",")
        if origin.strip()
    )
if debug_mode:
    origin_candidates.extend(
        [
            "http://localhost:3000",
            "http://localhost:5173",
            "http://127.0.0.1:3000",
            "http://127.0.0.1:5173",
        ]
    )
if not origin_candidates:
    origin_candidates.append(settings.app_base_url)

allowed_origins = list(dict.fromkeys(origin_candidates))

app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_path), name="static")
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

app.include_router(api_router, prefix="/api")
app.include_router(admin_router, prefix="/admin")


@app.get("/", response_class=HTMLResponse)
async def index():
    return "<h1>Website Crawler + AI Chatbot Platform</h1><p>Visit /admin to log in.</p>"


if __name__ == "__main__":
    import uvicorn

    reload_flag = os.environ.get("ENABLE_RELOAD", "0").lower() in {"1", "true", "yes"}
    uvicorn.run(
        "app.main:app",
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", 8000)),
        reload=reload_flag,
    )
