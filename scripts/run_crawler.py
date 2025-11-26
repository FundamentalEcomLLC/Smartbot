#!/usr/bin/env python
"""Helper script to kick off crawls from a local machine.

Usage:
    python scripts/run_crawler.py               # prompts for project id
    python scripts/run_crawler.py --project-id 3 # non-interactive
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.db import SessionLocal  # noqa: E402
from app.models import Project  # noqa: E402
from app.services.crawler import CrawlConfig, crawl_project  # noqa: E402


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
    args = parser.parse_args()

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
