#!/usr/bin/env python
"""Helper script to kick off crawls from a local machine.

Usage:
    python scripts/run_crawler.py                                 # prompts for project id
    python scripts/run_crawler.py --project-id 3                  # non-interactive
    python scripts/run_crawler.py --env-file .env.local           # custom env file
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Tuple

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _configure_environment(env: str | None, env_file: str | None) -> None:
    if env_file:
        env_path = Path(env_file).expanduser().resolve()
        if not env_path.exists():
            raise SystemExit(f"Env file {env_path} was not found")
        os.environ["CHATBOT_ENV_FILE"] = str(env_path)
    elif "CHATBOT_ENV_FILE" not in os.environ:
        default_local = ROOT / ".env.local"
        if default_local.exists():
            os.environ.setdefault("CHATBOT_ENV_FILE", str(default_local))
    if env:
        os.environ["ENV"] = env


def _load_app_objects() -> Tuple:
    from app.db import SessionLocal  # noqa: WPS433 (import after env configured)
    from app.models import Project
    from app.services.crawler import CrawlConfig, crawl_project

    return SessionLocal, Project, CrawlConfig, crawl_project


def _prompt_int(prompt: str) -> int:
    while True:
        raw = input(prompt).strip()
        if not raw:
            continue
        try:
            return int(raw)
        except ValueError:
            print("Please enter a valid integer.")


def _prompt_text(prompt: str) -> str:
    while True:
        raw = input(prompt).strip()
        if raw:
            return raw
        print("A value is required.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the crawler for a specific project")
    parser.add_argument("--project-id", type=int, help="Project ID to crawl")
    parser.add_argument("--start-url", help="Override the starting URL (defaults to project primary_domain)")
    parser.add_argument("--max-pages", type=int, help="Limit number of pages to crawl")
    parser.add_argument("--max-depth", type=int, help="Limit crawl depth")
    parser.add_argument("--delay", type=float, help="Seconds to wait between requests")
    parser.add_argument("--concurrency", type=int, help="Number of concurrent fetches")
    parser.add_argument("--env", help="Named environment (maps to .env.<env>)")
    parser.add_argument("--env-file", help="Explicit env file path")
    args = parser.parse_args()

    _configure_environment(args.env, args.env_file)
    SessionLocal, Project, CrawlConfig, crawl_project = _load_app_objects()

    project_id = args.project_id or _prompt_int("Enter the project ID to crawl: ")

    with SessionLocal() as db:
        project: Project | None = db.get(Project, project_id)
        if not project:
            raise SystemExit(f"Project {project_id} was not found")

        start_url = args.start_url or project.primary_domain
        if not start_url:
            start_url = _prompt_text("Project has no primary domain. Provide a start URL: ")

        config = CrawlConfig()
        if args.max_pages is not None:
            config.max_pages = args.max_pages
        if args.max_depth is not None:
            config.max_depth = args.max_depth
        if args.delay is not None:
            config.min_request_interval = args.delay
        if args.concurrency is not None:
            config.max_concurrency = args.concurrency

        print(
            f"Starting crawl for project {project_id} (domain={start_url}) | "
            f"max_pages={config.max_pages} depth={config.max_depth}"
        )
        crawl_project(db, project, start_url, config)
        print("Crawl complete.")


if __name__ == "__main__":
    main()
