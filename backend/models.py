from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


class FeedIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    issue_date: date
    url: str
    published_at: datetime | None = None
    content_html: str


class SourceStory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    description: str
    source: str
    url: str
    original_section: str
    issue_date: date
    issue_url: str

    @field_validator("id", "title", "description", "source", "url", "original_section", "issue_url")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class CuratedStory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=8, max_length=64)
    title: str = Field(min_length=2, max_length=28)
    summary: str = Field(min_length=4, max_length=96)
    category: str = Field(min_length=2, max_length=14)
    source: str = Field(min_length=1, max_length=30)
    url: str
    importance: float = Field(ge=0.0, le=1.0)
    source_story_ids: list[str] = Field(min_length=1, max_length=5)

    @field_validator("id", "title", "summary", "category", "source", "url")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()


class CuratedEdition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stories: list[CuratedStory]


class ManifestPage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=1, le=6)
    url: str
    preview_url: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int
    width: int = 800
    height: int = 480
    format: str = "e1002-4bpp"


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    generated_at: datetime
    source_issue: str
    source_issues: list[str]
    generation_id: str
    page_count: int = 6
    pages: list[ManifestPage]

