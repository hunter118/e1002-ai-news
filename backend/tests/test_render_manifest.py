from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image

from backend.curate import deterministic_edition
from backend.models import Manifest, ManifestPage
from backend.render import E6_COLORS, HEIGHT, RAW_PAGE_SIZE, WIDTH, render_edition


def test_render_six_exact_pages(tmp_path: Path, source_stories) -> None:
    edition = deterministic_edition(source_stories)
    rendered = render_edition(edition, tmp_path, "2026-08-31")
    assert len(rendered) == 6
    allowed = set(E6_COLORS)
    for preview_path, raw_path in rendered:
        with Image.open(preview_path) as image:
            assert image.size == (WIDTH, HEIGHT)
            assert set(image.convert("RGB").get_flattened_data()).issubset(allowed)
        assert raw_path.stat().st_size == RAW_PAGE_SIZE


def test_manifest_has_six_valid_urls_and_hashes(tmp_path: Path, source_stories) -> None:
    rendered = render_edition(deterministic_edition(source_stories), tmp_path, "2026-08-31")
    pages = []
    for index, (_preview, raw) in enumerate(rendered, 1):
        pages.append(
            ManifestPage(
                index=index,
                url=f"pages/page_{index}.epd",
                preview_url=f"pages/page_{index}.png",
                sha256=hashlib.sha256(raw.read_bytes()).hexdigest(),
                size=raw.stat().st_size,
            )
        )
    manifest = Manifest(
        generated_at=datetime.now(ZoneInfo("Asia/Singapore")),
        source_issue="https://daily.juya.uk/issues/2026-08-31/",
        source_issues=["https://daily.juya.uk/issues/2026-08-31/"],
        generation_id="2026-08-31-0123456789ab",
        pages=pages,
    )
    assert manifest.schema_version == 1
    assert manifest.page_count == 6
    assert len(manifest.pages) == 6
    assert all(page.url.startswith("pages/") for page in manifest.pages)
