from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .models import FeedIssue

RSS_URL = "https://daily.juya.uk/rss.xml"
SINGAPORE = ZoneInfo("Asia/Singapore")
CONTENT_TAG = "{http://purl.org/rss/1.0/modules/content/}encoded"
LOGGER = logging.getLogger(__name__)


class FeedError(RuntimeError):
    pass


def fetch_rss(url: str = RSS_URL, timeout: float = 30.0) -> bytes:
    retry = Retry(
        total=3,
        connect=3,
        read=3,
        status=3,
        backoff_factor=0.8,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    with requests.Session() as session:
        session.mount("https://", HTTPAdapter(max_retries=retry))
        response = session.get(
            url,
            timeout=timeout,
            headers={"User-Agent": "e1002-ai-news/1.0 (+daily display generator)"},
        )
        response.raise_for_status()
        content = response.content
    if not content.strip():
        raise FeedError("Juya RSS response was empty")
    return content


def load_rss_file(path: str | Path) -> bytes:
    return Path(path).read_bytes()


def _issue_date(title: str, published: datetime | None) -> date:
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", title)
    if match:
        return date.fromisoformat(match.group(1))
    if published is not None:
        return published.astimezone(SINGAPORE).date()
    raise FeedError(f"Cannot determine issue date from title: {title!r}")


def parse_rss(xml_data: bytes) -> list[FeedIssue]:
    try:
        root = ET.fromstring(xml_data)
    except ET.ParseError as exc:
        raise FeedError(f"Malformed Juya RSS: {exc}") from exc

    issues: list[FeedIssue] = []
    for item in root.findall("./channel/item"):
        title = (item.findtext("title") or "").strip()
        url = (item.findtext("link") or item.findtext("guid") or "").strip()
        published_text = (item.findtext("pubDate") or "").strip()
        published = None
        if published_text:
            try:
                published = parsedate_to_datetime(published_text)
            except (TypeError, ValueError):
                LOGGER.warning("Ignoring malformed pubDate for %s: %s", title, published_text)
        content = (item.findtext(CONTENT_TAG) or item.findtext("description") or "").strip()
        if not title or not url or not content:
            LOGGER.warning("Skipping malformed RSS item: title=%r url=%r content=%s", title, url, bool(content))
            continue
        try:
            day = _issue_date(title, published)
        except FeedError as exc:
            LOGGER.warning("Skipping RSS item: %s", exc)
            continue
        issues.append(
            FeedIssue(
                title=title,
                issue_date=day,
                url=url,
                published_at=published,
                content_html=content,
            )
        )

    if not issues:
        raise FeedError("Juya RSS contained no usable issues")
    issues.sort(key=lambda item: (item.issue_date, item.published_at or datetime.min.replace(tzinfo=SINGAPORE)), reverse=True)
    return issues


def choose_primary_issue(issues: list[FeedIssue], target_date: date | None = None) -> tuple[int, bool]:
    target_date = target_date or datetime.now(SINGAPORE).date()
    for index, issue in enumerate(issues):
        if issue.issue_date == target_date:
            LOGGER.info("Juya issue selected: %s (%s)", issue.issue_date, issue.url)
            return index, False
    newest = issues[0]
    LOGGER.warning(
        "No Juya issue for Singapore date %s; falling back to newest issue %s (%s)",
        target_date,
        newest.issue_date,
        newest.url,
    )
    return 0, True
