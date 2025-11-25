from logging import getLogger
from pathlib import Path

from alembic import command
from alembic.config import Config

from app.config import get_settings

logger = getLogger(__name__)
ROOT_DIR = Path(__file__).resolve().parents[1]


def ensure_database_ready() -> None:
    """Make sure the database can satisfy application requirements."""

    try:
        _run_migrations()
    except Exception as exc:  # pragma: no cover - startup failure should surface immediately
        logger.exception("Database bootstrap failed")
        raise RuntimeError("Database bootstrap failed") from exc


def _run_migrations() -> None:
    """Apply Alembic migrations up to the latest revision."""

    settings = get_settings()
    alembic_cfg = Config(str(ROOT_DIR / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(ROOT_DIR / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", settings.database_url)
    command.upgrade(alembic_cfg, "head")
    logger.info("Alembic migrations are up to date")
