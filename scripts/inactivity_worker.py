#!/usr/bin/env python
"""Background helper that sends inactivity warnings and auto-closes chats."""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings  # noqa: E402
from app.db import db_session  # noqa: E402
from app.enums import ConversationStatus  # noqa: E402
from app.models import Conversation, Project  # noqa: E402
from app.services.conversation_lifecycle import (  # noqa: E402
    close_conversation,
    record_system_message,
)

logger = logging.getLogger(__name__)
settings = get_settings()


def _issue_warnings(db) -> int:
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(seconds=settings.chat_inactivity_warning_seconds)
    conversations = (
        db.query(Conversation)
        .filter(
            Conversation.status == ConversationStatus.ACTIVE,
            Conversation.last_user_message_at.isnot(None),
            Conversation.last_user_message_at <= threshold,
            Conversation.inactivity_warning_sent_at.is_(None),
        )
        .all()
    )

    issued = 0
    for conversation in conversations:
        record_system_message(
            db,
            conversation,
            settings.chat_inactivity_warning_message,
            commit=False,
        )
        conversation.inactivity_warning_sent_at = now
        conversation.status = ConversationStatus.WARNING
        db.add(conversation)
        db.flush()
        db.commit()
        issued += 1
        logger.info("Warning sent | conversation=%s", conversation.id)
    return issued


def _close_expired(db) -> int:
    now = datetime.now(timezone.utc)
    threshold = now - timedelta(seconds=settings.chat_inactivity_grace_seconds)
    rows = (
        db.query(Conversation, Project)
        .join(Project, Project.id == Conversation.project_id)
        .filter(
            Conversation.status == ConversationStatus.WARNING,
            Conversation.inactivity_warning_sent_at.isnot(None),
            Conversation.inactivity_warning_sent_at <= threshold,
        )
        .all()
    )

    closed = 0
    for conversation, project in rows:
        message = settings.chat_inactivity_close_message
        if close_conversation(
            db,
            project,
            conversation,
            reason="auto_inactivity",
            latest_message=message,
            closing_message=message,
        ):
            closed += 1
            logger.info("Conversation auto-closed | conversation=%s", conversation.id)
    return closed


def process_once() -> tuple[int, int]:
    with db_session() as db:
        warnings = _issue_warnings(db)
        closures = _close_expired(db)
    return warnings, closures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor and close inactive chats")
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep running instead of exiting after one pass",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=30.0,
        help="Seconds to wait between passes when looping",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level.upper()), format="%(asctime)s %(levelname)s %(message)s")

    def _run_cycle() -> None:
        warnings, closures = process_once()
        logger.info("Cycle complete | warnings=%s closed=%s", warnings, closures)

    if not args.loop:
        _run_cycle()
        return

    try:
        while True:
            _run_cycle()
            time.sleep(max(args.interval, 1.0))
    except KeyboardInterrupt:
        logger.info("Stopping inactivity worker")


if __name__ == "__main__":
    main()
