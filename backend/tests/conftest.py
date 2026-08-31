from __future__ import annotations

from datetime import date

import pytest

from backend.models import SourceStory


@pytest.fixture
def source_stories() -> list[SourceStory]:
    return [
        SourceStory(
            id=f"src-{index:012d}",
            title=f"AI 新闻标题 {index}",
            description=f"这是第 {index} 条真实来源新闻的简明描述，包含必要事实和数字 {index}。",
            source=f"Source {index}",
            url=f"https://example.com/story-{index}",
            original_section="产品应用" if index % 2 else "模型与研究",
            issue_date=date(2026, 8, 31),
            issue_url="https://daily.juya.uk/issues/2026-08-31/",
        )
        for index in range(1, 31)
    ]

