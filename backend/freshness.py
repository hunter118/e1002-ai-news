from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

RSS_URL = "https://daily.juya.uk/rss.xml"
MANIFEST_URL = "https://hunter118.github.io/e1002-ai-news/manifest.json"
SINGAPORE = ZoneInfo("Asia/Singapore")


@dataclass(frozen=True)
class FreshnessDecision:
    should_generate: bool
    reason: str
    issue_date: date | None
    issue_url: str | None


def _item_date(item: ET.Element) -> date | None:
    title = (item.findtext("title") or "").strip()
    match = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", title)
    if match:
        return date.fromisoformat(match.group(1))
    published_text = (item.findtext("pubDate") or "").strip()
    if not published_text:
        return None
    return parsedate_to_datetime(published_text).astimezone(SINGAPORE).date()


def newest_issue(xml_data: bytes) -> tuple[date, str]:
    root = ET.fromstring(xml_data)
    issues: list[tuple[date, str]] = []
    for item in root.findall("./channel/item"):
        issue_date = _item_date(item)
        url = (item.findtext("link") or item.findtext("guid") or "").strip()
        if issue_date is not None and url:
            issues.append((issue_date, url))
    if not issues:
        raise ValueError("Juya RSS contained no dated issue URLs")
    return max(issues, key=lambda value: value[0])


def decide_freshness(
    rss_data: bytes, manifest_data: bytes | None, today: date
) -> FreshnessDecision:
    issue_date, issue_url = newest_issue(rss_data)
    if issue_date != today:
        return FreshnessDecision(False, "today-not-published", issue_date, issue_url)

    source_issue = None
    if manifest_data:
        try:
            source_issue = json.loads(manifest_data)["source_issue"]
        except (json.JSONDecodeError, KeyError, TypeError):
            pass
    if source_issue == issue_url:
        return FreshnessDecision(False, "today-already-deployed", issue_date, issue_url)
    return FreshnessDecision(True, "new-today-issue", issue_date, issue_url)


def fetch(url: str, attempts: int = 3, timeout: float = 30.0) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "e1002-ai-news-freshness/1.0"})
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception as exc:  # Network errors should fail closed after bounded retries.
            last_error = exc
            if attempt + 1 < attempts:
                time.sleep(0.8 * (attempt + 1))
    raise RuntimeError(f"Failed to fetch {url}: {last_error}") from last_error


def main() -> None:
    parser = argparse.ArgumentParser(description="Decide whether today's Juya edition needs generation")
    parser.add_argument("--rss-url", default=RSS_URL)
    parser.add_argument("--manifest-url", default=MANIFEST_URL)
    parser.add_argument("--today", type=date.fromisoformat)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()

    rss_data = fetch(args.rss_url)
    try:
        manifest_data = fetch(args.manifest_url)
    except RuntimeError as exc:
        print(f"Current manifest unavailable ({exc}); allowing a recovery generation")
        manifest_data = None
    today = args.today or datetime.now(SINGAPORE).date()
    decision = decide_freshness(rss_data, manifest_data, today)
    print(
        f"Freshness: should_generate={str(decision.should_generate).lower()} "
        f"reason={decision.reason} latest_issue={decision.issue_date} url={decision.issue_url}"
    )
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as output:
            output.write(f"should_generate={str(decision.should_generate).lower()}\n")
            output.write(f"reason={decision.reason}\n")


if __name__ == "__main__":
    main()
