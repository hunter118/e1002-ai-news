from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Iterable
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from .models import FeedIssue, SourceStory

LOGGER = logging.getLogger(__name__)


def _source_name(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower().removeprefix("www.")
    known = {
        "openai.com": "OpenAI",
        "anthropic.com": "Anthropic",
        "github.com": "GitHub",
        "youtube.com": "YouTube",
        "bilibili.com": "哔哩哔哩",
        "techcrunch.com": "TechCrunch",
        "theinformation.com": "The Information",
    }
    for domain, label in known.items():
        if host == domain or host.endswith("." + domain):
            return label
    if host in {"x.com", "twitter.com"}:
        handle = parsed.path.strip("/").split("/", 1)[0]
        return f"@{handle}" if handle else "X"
    return host or "Juya"


def _clean_title(node: Tag) -> str:
    link = node.find("a", href=True)
    title = link.get_text(" ", strip=True) if link else node.get_text(" ", strip=True)
    return re.sub(r"\s*#\d+\s*$", "", title).strip()


def _description_after(node: Tag) -> str:
    paragraphs: list[str] = []
    for sibling in node.next_siblings:
        if isinstance(sibling, Tag) and sibling.name in {"h2", "h3", "hr"}:
            break
        if not isinstance(sibling, Tag):
            continue
        if sibling.name == "blockquote":
            text = sibling.get_text(" ", strip=True)
            if text:
                return text
        if sibling.name == "p":
            text = sibling.get_text(" ", strip=True)
            if text and text != "相关链接：" and not text.startswith("提示"):
                paragraphs.append(text)
    return paragraphs[0] if paragraphs else ""


def parse_issue(issue: FeedIssue) -> list[SourceStory]:
    soup = BeautifulSoup(issue.content_html, "html.parser")
    section = "未分类"
    stories: list[SourceStory] = []
    for node in soup.find_all(["h2", "h3"]):
        if node.name == "h2":
            heading = node.get_text(" ", strip=True)
            if heading and heading != "概览":
                section = heading
            continue

        code = node.find("code")
        link = node.find("a", href=True)
        if code is None or link is None or not re.search(r"#\d+", code.get_text(" ", strip=True)):
            continue
        title = _clean_title(node)
        url = str(link.get("href", "")).strip()
        description = _description_after(node)
        if not title or not url or not description:
            LOGGER.warning("Skipping malformed story in %s: title=%r url=%r", issue.issue_date, title, url)
            continue
        digest = hashlib.sha1(f"{url}\n{title}".encode("utf-8")).hexdigest()[:16]
        stories.append(
            SourceStory(
                id=f"src-{digest}",
                title=title,
                description=description,
                source=_source_name(url),
                url=url,
                original_section=section,
                issue_date=issue.issue_date,
                issue_url=issue.url,
            )
        )
    LOGGER.info("Parsed %d stories from Juya issue %s", len(stories), issue.issue_date)
    return stories


def _normalized_title(title: str) -> str:
    return re.sub(r"[\W_]+", "", title, flags=re.UNICODE).lower()


def deduplicate_sources(stories: Iterable[SourceStory]) -> list[SourceStory]:
    unique: list[SourceStory] = []
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    for story in stories:
        normalized_url = story.url.rstrip("/")
        normalized_title = _normalized_title(story.title)
        if normalized_url in seen_urls or normalized_title in seen_titles:
            continue
        seen_urls.add(normalized_url)
        seen_titles.add(normalized_title)
        unique.append(story)
    return unique


def collect_candidate_stories(
    issues: list[FeedIssue],
    primary_index: int,
    minimum: int = 18,
    target_pool: int = 30,
    max_issues: int = 7,
) -> tuple[list[SourceStory], list[FeedIssue]]:
    candidates: list[SourceStory] = []
    used_issues: list[FeedIssue] = []
    ordered = issues[primary_index:] + issues[:primary_index]
    for issue in ordered[:max_issues]:
        parsed = parse_issue(issue)
        if not parsed:
            continue
        candidates = deduplicate_sources([*candidates, *parsed])
        used_issues.append(issue)
        if len(candidates) >= target_pool:
            break
    if len(candidates) < minimum:
        raise ValueError(f"Only {len(candidates)} unique real stories available; need at least {minimum}")
    if len(used_issues) > 1:
        LOGGER.warning(
            "Primary issue had fewer than the target pool; supplemented only from %d recent RSS issues: %s",
            len(used_issues),
            ", ".join(str(issue.issue_date) for issue in used_issues),
        )
    LOGGER.info("Number of raw candidate stories after deterministic deduplication: %d", len(candidates))
    return candidates, used_issues

