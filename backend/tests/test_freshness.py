from __future__ import annotations

import json
from datetime import date

import pytest

from backend.freshness import decide_freshness, newest_issue


def _rss(*days: str) -> bytes:
    items = "".join(
        f"<item><title>{day}</title><link>https://daily.juya.uk/issues/{day}/</link></item>"
        for day in days
    )
    return f"<rss><channel>{items}</channel></rss>".encode()


def _manifest(day: str) -> bytes:
    return json.dumps({"source_issue": f"https://daily.juya.uk/issues/{day}/"}).encode()


def test_newest_issue_uses_dates_not_feed_order() -> None:
    assert newest_issue(_rss("2026-08-30", "2026-08-31")) == (
        date(2026, 8, 31),
        "https://daily.juya.uk/issues/2026-08-31/",
    )


def test_waits_until_today_is_published() -> None:
    decision = decide_freshness(_rss("2026-08-30"), _manifest("2026-08-30"), date(2026, 8, 31))
    assert decision.should_generate is False
    assert decision.reason == "today-not-published"


def test_skips_today_when_already_deployed() -> None:
    decision = decide_freshness(_rss("2026-08-31"), _manifest("2026-08-31"), date(2026, 8, 31))
    assert decision.should_generate is False
    assert decision.reason == "today-already-deployed"


def test_generates_new_today_issue_or_recovers_missing_manifest() -> None:
    stale = decide_freshness(_rss("2026-08-31"), _manifest("2026-08-30"), date(2026, 8, 31))
    missing = decide_freshness(_rss("2026-08-31"), None, date(2026, 8, 31))
    assert stale.should_generate is True
    assert missing.should_generate is True


def test_rejects_feed_without_dated_issue_url() -> None:
    with pytest.raises(ValueError):
        newest_issue(b"<rss><channel><item><title>unknown</title></item></channel></rss>")
