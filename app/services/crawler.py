import logging
import time
from collections import deque
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Deque, Dict, List, Optional, Set, Tuple
from urllib import robotparser
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from ..db import SessionLocal
from ..enums import CrawlStatus, DocumentSourceType
from ..models import Document, Project
from .knowledge import index_document_chunks

logger = logging.getLogger(__name__)


@dataclass
class CrawlConfig:
    max_pages: int = 200
    max_depth: int = 3
    min_request_interval: float = 0.3  # seconds between hits to stay polite
    max_concurrency: int = 4  # concurrent fetches for near real-time ingest


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    cleaned = parsed._replace(fragment="")
    normalized = urlunparse(cleaned)
    if normalized.endswith("/"):
        normalized = normalized[:-1]
    return normalized


def same_domain(url: str, root: str) -> bool:
    return urlparse(url).netloc == urlparse(root).netloc


def extract_text(html: str) -> Tuple[str, Dict[str, str]]:
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()
    title = (soup.title.string or "").strip() if soup.title else ""
    description_tag = soup.find("meta", attrs={"name": "description"})
    description = description_tag["content"].strip() if description_tag and description_tag.has_attr("content") else ""
    headings = "\n".join(h.get_text(strip=True) for h in soup.find_all(["h1", "h2", "h3"]))
    paragraphs = "\n".join(p.get_text(separator=" ", strip=True) for p in soup.find_all("p"))
    raw_content = "\n".join(filter(None, [title, description, headings, paragraphs])).strip()
    metadata = {"title": title, "description": description}
    return raw_content, metadata


class _FetchThrottle:
    def __init__(self, min_interval: float):
        self._interval = max(0.05, min_interval)
        self._lock = Lock()
        self._next_allowed = 0.0

    def wait_turn(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                wait_time = self._next_allowed - now
                if wait_time <= 0:
                    self._next_allowed = now + self._interval
                    return
            time.sleep(wait_time)

    def backoff(self, delay: float) -> None:
        with self._lock:
            self._next_allowed = max(self._next_allowed, time.monotonic() + delay)


# Remove stale crawled document/chunks so repeated crawls keep the latest content.
def _delete_existing_document(db: Session, project_id: int, url: str) -> None:
    existing = (
        db.query(Document)
        .filter(
            Document.project_id == project_id,
            Document.source_type == DocumentSourceType.CRAWLED_PAGE,
            Document.url_or_name == url,
        )
        .all()
    )
    if not existing:
        return
    for doc in existing:
        db.delete(doc)
    db.flush()


def crawl_project(db: Session, project: Project, start_url: str, config: CrawlConfig) -> None:
    start_ts = time.monotonic()
    logger.info(
        "Starting crawl for project %s | domain=%s max_pages=%s max_depth=%s concurrency=%s interval=%.2fs",
        project.id,
        start_url,
        config.max_pages,
        config.max_depth,
        config.max_concurrency,
        config.min_request_interval,
    )
    project.crawl_status = CrawlStatus.RUNNING
    db.commit()

    visited: Set[str] = set()
    queue: Deque[Tuple[str, int]] = deque([(start_url, 0)])
    client = httpx.Client(timeout=20.0, follow_redirects=True)
    processed_pages = 0
    throttler = _FetchThrottle(config.min_request_interval)
    executor = ThreadPoolExecutor(max_workers=max(1, config.max_concurrency))
    in_flight = set()
    rp = robotparser.RobotFileParser()
    try:
        parsed = urlparse(start_url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp.set_url(robots_url)
        rp.read()
    except Exception:
        rp = None

    failed = False
    try:
        def submit_job(target_url: str, depth: int) -> None:
            future = executor.submit(_process_url, target_url, depth)
            in_flight.add(future)

        def _process_url(target_url: str, depth: int) -> Tuple[int, List[Tuple[str, int]]]:
            normalized = normalize_url(target_url)
            if rp and not rp.can_fetch("ChatbotCrawler", normalized):
                logger.info("Robots.txt disallows %s", normalized)
                return 0, []
            try:
                throttler.wait_turn()
                response = client.get(normalized, headers={"User-Agent": "ChatbotCrawler/1.0"})
                if response.status_code in {429, 503}:
                    retry_after = float(response.headers.get("Retry-After", "5"))
                    throttler.backoff(retry_after)
                    logger.warning("Server requested backoff %.1fs for %s", retry_after, normalized)
                    return 0, []
                if "text/html" not in response.headers.get("content-type", ""):
                    logger.debug("Skipping non-HTML resource %s (%s)", normalized, response.headers.get("content-type"))
                    return 0, []
                raw_content, metadata = extract_text(response.text)
                if not raw_content:
                    logger.debug("No meaningful text extracted from %s", normalized)
                    return 0, []
                with SessionLocal() as worker_db:
                    _delete_existing_document(worker_db, project.id, normalized)
                    document = Document(
                        project_id=project.id,
                        source_type=DocumentSourceType.CRAWLED_PAGE,
                        url_or_name=normalized,
                        raw_content=raw_content,
                        metadata_json={**metadata, "url": normalized},
                    )
                    worker_db.add(document)
                    worker_db.flush()
                    index_document_chunks(worker_db, project.id, document)
                    worker_db.commit()
                logger.info("Indexed %s (depth=%s)", normalized, depth)
                new_links: List[Tuple[str, int]] = []
                next_depth = depth + 1
                if next_depth <= config.max_depth:
                    soup = BeautifulSoup(response.text, "html.parser")
                    for link in soup.find_all("a", href=True):
                        target = urljoin(normalized + "/", link["href"])
                        new_links.append((target, next_depth))
                if new_links:
                    logger.debug("Discovered %s new links from %s", len(new_links), normalized)
                return 1, new_links
            except Exception as exc:  # noqa: BLE001
                logger.exception("Failed to crawl %s: %s", normalized, exc)
                return 0, []

        while (queue or in_flight) and processed_pages < config.max_pages:
            while (
                queue
                and len(in_flight) < config.max_concurrency
                and processed_pages + len(in_flight) < config.max_pages
            ):
                url, depth = queue.popleft()
                normalized = normalize_url(url)
                if normalized in visited or depth > config.max_depth:
                    continue
                if not same_domain(normalized, start_url):
                    continue
                visited.add(normalized)
                logger.debug(
                    "Queueing %s (depth=%s) | visited=%s queued=%s inflight=%s",
                    normalized,
                    depth,
                    len(visited),
                    len(queue),
                    len(in_flight),
                )
                submit_job(normalized, depth)

            if not in_flight:
                break

            done, _ = wait(in_flight, return_when=FIRST_COMPLETED)
            for future in done:
                in_flight.discard(future)
                processed, links = future.result()
                processed_pages += processed
                for target, depth in links:
                    if processed_pages >= config.max_pages:
                        break
                    norm_target = normalize_url(target)
                    if norm_target in visited:
                        continue
                    queue.append((norm_target, depth))
            logger.info(
                "Crawl progress | project=%s processed=%s queued=%s inflight=%s",
                project.id,
                processed_pages,
                len(queue),
                len(in_flight),
            )
    except Exception:
        failed = True
        logger.exception("Critical crawl failure for project %s", project.id)
    finally:
        client.close()
        executor.shutdown(wait=True)
        project.crawl_status = CrawlStatus.FAILED if failed else CrawlStatus.DONE
        project.last_crawled_at = datetime.now(timezone.utc)
        db.commit()
        duration = time.monotonic() - start_ts
        logger.info(
            "Crawl finished | project=%s status=%s pages=%s duration=%.1fs",
            project.id,
            project.crawl_status.value,
            processed_pages,
            duration,
        )
