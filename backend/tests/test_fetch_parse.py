from __future__ import annotations

from datetime import date

from backend.fetch_juya import choose_primary_issue, parse_rss
from backend.models import FeedIssue
from backend.parse_issue import deduplicate_sources, parse_issue


def _rss(items: str) -> bytes:
    return f"""<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:content="http://purl.org/rss/1.0/modules/content/" version="2.0">
  <channel><title>橘鸦AI早报</title>{items}</channel>
</rss>""".encode()


def _item(day: str, content: str, include_url: bool = True) -> str:
    link = f"<link>https://daily.juya.uk/issues/{day}/</link>" if include_url else ""
    return f"""<item><title>{day}</title>{link}<pubDate>Mon, 31 Aug 2026 03:00:00 GMT</pubDate>
<content:encoded><![CDATA[{content}]]></content:encoded></item>"""


STORY_HTML = """
<h1>AI 早报</h1><h2>概览</h2><h3>产品应用</h3><ul><li>概览项</li></ul><hr>
<h2>产品应用</h2>
<h3><a href="https://openai.com/news/example">OpenAI 发布新模型</a> <code>#1</code></h3>
<blockquote>新模型在基准测试中提升 20%，官方已经发布技术说明。</blockquote>
<p>更长的正文。</p><hr>
<h3><a href="https://example.com/two">第二条新闻</a> <code>#2</code></h3>
<p>第二条新闻的正文描述。</p><hr>
"""


def test_successful_current_issue_and_newest_fallback() -> None:
    issues = parse_rss(_rss(_item("2026-08-31", STORY_HTML) + _item("2026-08-30", STORY_HTML)))
    index, fallback = choose_primary_issue(issues, date(2026, 8, 31))
    assert issues[index].issue_date == date(2026, 8, 31)
    assert fallback is False

    index, fallback = choose_primary_issue(issues, date(2026, 9, 1))
    assert index == 0
    assert fallback is True


def test_malformed_item_and_missing_url_are_skipped() -> None:
    xml = _rss(_item("2026-08-31", STORY_HTML) + _item("2026-08-30", STORY_HTML, include_url=False))
    issues = parse_rss(xml)
    assert len(issues) == 1


def test_parse_individual_stories_and_sections() -> None:
    issue = FeedIssue(
        title="2026-08-31",
        issue_date=date(2026, 8, 31),
        url="https://daily.juya.uk/issues/2026-08-31/",
        content_html=STORY_HTML,
    )
    stories = parse_issue(issue)
    assert len(stories) == 2
    assert stories[0].title == "OpenAI 发布新模型"
    assert stories[0].source == "OpenAI"
    assert stories[0].original_section == "产品应用"
    assert "20%" in stories[0].description


def test_duplicate_story_urls_and_titles_are_removed(source_stories) -> None:
    duplicate_url = source_stories[0].model_copy(update={"id": "src-duplicate-1", "title": "另一个标题"})
    duplicate_title = source_stories[1].model_copy(
        update={"id": "src-duplicate-2", "title": source_stories[0].title, "url": "https://other.example/item"}
    )
    result = deduplicate_sources([source_stories[0], duplicate_url, duplicate_title, source_stories[1]])
    assert [story.id for story in result] == [source_stories[0].id, source_stories[1].id]


def test_story_missing_optional_link_is_ignored_without_crashing() -> None:
    issue = FeedIssue(
        title="2026-08-31",
        issue_date=date(2026, 8, 31),
        url="https://daily.juya.uk/issues/2026-08-31/",
        content_html="<h2>产品</h2><h3>无链接新闻 <code>#1</code></h3><p>正文</p>",
    )
    assert parse_issue(issue) == []

