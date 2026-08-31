from __future__ import annotations

import hashlib
import json
import logging
import os
from collections.abc import Sequence

from openai import OpenAI
from pydantic import ValidationError

from .models import CuratedEdition, CuratedStory, SourceStory

LOGGER = logging.getLogger(__name__)
DEFAULT_MODEL = "gpt-5.6-luna"
FINAL_STORY_COUNT = 18

SYSTEM_PROMPT = """你是严谨的 AI 新闻编辑。只处理用户提供的 Juya 新闻记录，不使用外部知识，不虚构事实。

工作要求：
- 去除明显重复，并可合并指向同一事件的记录；合并时列出所有 source_story_ids。
- 动态归类，不预设固定六类，也不要求每页对应一个分类。
- 按重要性、信息量和 AI 从业者相关度评分并排序。
- 选出恰好 18 条；标题与摘要使用简洁、克制的中文，保留关键数字和模型名。
- 每条 URL 必须原样取自输入记录；source_story_ids 必须全部来自输入记录。
- 不把传闻写成已确认事实，不使用夸张宣传语。
- 标题最多 28 个字符；摘要最多 96 个字符，通常为一到两句。
"""


def _story_payload(story: SourceStory) -> dict[str, object]:
    return {
        "id": story.id,
        "title": story.title,
        "description": story.description,
        "source": story.source,
        "url": story.url,
        "original_section": story.original_section,
        "issue_date": story.issue_date.isoformat(),
    }


def _stable_curated_id(source_ids: Sequence[str]) -> str:
    digest = hashlib.sha1("\n".join(sorted(source_ids)).encode("utf-8")).hexdigest()[:16]
    return f"news-{digest}"


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split()).strip()
    if len(text) <= limit:
        return text
    clipped = text[: limit - 1].rstrip("，。；、,.!！?？:： ")
    return clipped + "…"


def _normalize_and_repair(
    edition: CuratedEdition | None,
    candidates: Sequence[SourceStory],
) -> CuratedEdition:
    by_id = {story.id: story for story in candidates}
    by_url = {story.url: story for story in candidates}
    repaired: list[CuratedStory] = []
    used_source_ids: set[str] = set()
    used_urls: set[str] = set()

    drafts = sorted(edition.stories, key=lambda item: item.importance, reverse=True) if edition else []
    for draft in drafts:
        if draft.url not in by_url:
            LOGGER.warning("Discarding model story with non-source URL: %s", draft.url)
            continue
        if any(source_id not in by_id for source_id in draft.source_story_ids):
            LOGGER.warning("Discarding model story with unknown source_story_ids: %s", draft.source_story_ids)
            continue
        source_ids = list(dict.fromkeys(draft.source_story_ids))
        if draft.url in used_urls or any(source_id in used_source_ids for source_id in source_ids):
            continue
        canonical_source = by_url[draft.url].source
        repaired.append(
            draft.model_copy(
                update={
                    "id": _stable_curated_id(source_ids),
                    "source": canonical_source,
                    "source_story_ids": source_ids,
                }
            )
        )
        used_source_ids.update(source_ids)
        used_urls.add(draft.url)
        if len(repaired) == FINAL_STORY_COUNT:
            break

    # A deterministic, source-only repair guarantees an edition remains valid if the
    # model merges too aggressively or returns fewer than 18 usable records.
    for source in candidates:
        if len(repaired) == FINAL_STORY_COUNT:
            break
        if source.id in used_source_ids or source.url in used_urls:
            continue
        repaired.append(
            CuratedStory(
                id=_stable_curated_id([source.id]),
                title=_clip(source.title, 28),
                summary=_clip(source.description, 96),
                category=_clip(source.original_section or "AI 动态", 14),
                source=source.source,
                url=source.url,
                importance=max(0.01, 0.50 - len(repaired) * 0.01),
                source_story_ids=[source.id],
            )
        )
        used_source_ids.add(source.id)
        used_urls.add(source.url)

    if len(repaired) != FINAL_STORY_COUNT:
        raise ValueError(f"Cannot repair edition to exactly {FINAL_STORY_COUNT} real stories")
    if len({story.id for story in repaired}) != FINAL_STORY_COUNT:
        raise ValueError("Curated edition contains duplicate stable IDs")
    return CuratedEdition(stories=repaired)


def validate_edition(edition: CuratedEdition, candidates: Sequence[SourceStory]) -> CuratedEdition:
    repaired = _normalize_and_repair(edition, candidates)
    if len(repaired.stories) != FINAL_STORY_COUNT:
        raise ValueError("Curated edition must contain exactly 18 stories")
    return repaired


def curate_stories(
    candidates: Sequence[SourceStory],
    model: str | None = None,
    client: OpenAI | None = None,
    attempts: int = 3,
) -> CuratedEdition:
    if len(candidates) < FINAL_STORY_COUNT:
        raise ValueError("At least 18 real source stories are required")
    model = model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL)
    client = client or OpenAI()
    payload = [_story_payload(story) for story in candidates]
    base_user_prompt = (
        "请从以下真实来源记录中编辑今日显示内容。必须返回恰好 18 条。\n\n"
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )

    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            LOGGER.info("OpenAI curation attempt %d/%d using model %s", attempt, attempts, model)
            response = client.responses.parse(
                model=model,
                input=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": base_user_prompt},
                ],
                text_format=CuratedEdition,
            )
            parsed = response.output_parsed
            if parsed is None:
                raise ValueError("OpenAI response did not contain parsed structured output")
            result = validate_edition(parsed, candidates)
            LOGGER.info("Number after GPT deduplication/repair: %d", len(result.stories))
            LOGGER.info("18 final stories validated")
            return result
        except (ValidationError, ValueError, RuntimeError) as exc:
            last_error = exc
            LOGGER.warning("OpenAI curation attempt %d failed validation: %s", attempt, exc)
        except Exception as exc:  # SDK/network exceptions vary by OpenAI SDK version.
            last_error = exc
            LOGGER.warning("OpenAI curation attempt %d failed: %s", attempt, exc)

    raise RuntimeError(f"OpenAI curation failed after {attempts} attempts: {last_error}") from last_error


def deterministic_edition(candidates: Sequence[SourceStory]) -> CuratedEdition:
    """Source-only fallback used for tests and rendering development, never by CI publication."""
    return _normalize_and_repair(None, candidates)

