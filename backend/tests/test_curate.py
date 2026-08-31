from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from backend.curate import curate_stories, deterministic_edition, validate_edition
from backend.models import CuratedEdition, CuratedStory


def _curated(source, index: int) -> CuratedStory:
    return CuratedStory(
        id=f"draft-{index:08d}",
        title=f"精选 AI 新闻 {index}",
        summary=f"第 {index} 条新闻的精炼摘要。",
        category="模型发布" if index % 2 else "开发生态",
        source=source.source,
        url=source.url,
        importance=1.0 - index / 100,
        source_story_ids=[source.id],
    )


def test_valid_18_stories(source_stories) -> None:
    edition = CuratedEdition(stories=[_curated(source, index) for index, source in enumerate(source_stories[:18], 1)])
    result = validate_edition(edition, source_stories)
    assert len(result.stories) == 18
    assert len({story.id for story in result.stories}) == 18


def test_wrong_story_count_is_deterministically_repaired(source_stories) -> None:
    edition = CuratedEdition(stories=[_curated(source, index) for index, source in enumerate(source_stories[:9], 1)])
    result = validate_edition(edition, source_stories)
    assert len(result.stories) == 18
    assert result.stories[-1].url in {story.url for story in source_stories}


def test_duplicate_source_ids_are_repaired(source_stories) -> None:
    first = _curated(source_stories[0], 1)
    duplicate = first.model_copy(update={"id": "draft-duplicate", "title": "重复事件"})
    result = validate_edition(CuratedEdition(stories=[first, duplicate]), source_stories)
    assert len(result.stories) == 18
    assert len({source_id for story in result.stories for source_id in story.source_story_ids}) >= 18


def test_invalid_json() -> None:
    with pytest.raises(ValidationError):
        CuratedEdition.model_validate_json("{not valid json")


def test_excessively_long_title() -> None:
    with pytest.raises(ValidationError):
        CuratedStory(
            id="draft-12345678",
            title="太长" * 20,
            summary="有效摘要内容",
            category="研究",
            source="来源",
            url="https://example.com",
            importance=0.5,
            source_story_ids=["src-1"],
        )


def test_missing_category() -> None:
    payload = {
        "id": "draft-12345678",
        "title": "有效标题",
        "summary": "有效摘要内容",
        "source": "来源",
        "url": "https://example.com",
        "importance": 0.5,
        "source_story_ids": ["src-1"],
    }
    with pytest.raises(ValidationError):
        CuratedStory.model_validate_json(json.dumps(payload, ensure_ascii=False))


def test_openai_invalid_output_retries_and_fails(source_stories) -> None:
    class Responses:
        calls = 0

        def parse(self, **_kwargs):
            self.calls += 1
            raise ValueError("invalid structured output")

    class Client:
        responses = Responses()

    with pytest.raises(RuntimeError, match="after 2 attempts"):
        curate_stories(source_stories, client=Client(), attempts=2)
    assert Client.responses.calls == 2


def test_deterministic_edition_is_exact(source_stories) -> None:
    edition = deterministic_edition(source_stories)
    assert len(edition.stories) == 18

