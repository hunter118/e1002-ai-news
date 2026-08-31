from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .curate import curate_stories, deterministic_edition
from .fetch_juya import RSS_URL, choose_primary_issue, fetch_rss, load_rss_file, parse_rss
from .models import Manifest, ManifestPage
from .parse_issue import collect_candidate_stories
from .render import HEIGHT, PAGE_COUNT, RAW_PAGE_SIZE, WIDTH, render_edition

LOGGER = logging.getLogger(__name__)
SINGAPORE = ZoneInfo("Asia/Singapore")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _generation_id(issue_date: str, curated_payload: dict[str, object]) -> str:
    canonical = json.dumps(curated_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    return f"{issue_date}-{digest}"


def generate(
    rss_file: str | None = None,
    output_dir: Path | None = None,
    use_openai: bool = True,
) -> Manifest:
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    xml_data = load_rss_file(rss_file) if rss_file else fetch_rss(RSS_URL)
    issues = parse_rss(xml_data)
    primary_index, _ = choose_primary_issue(issues)
    primary = issues[primary_index]
    candidates, source_issues = collect_candidate_stories(issues, primary_index)

    build_dir = PROJECT_ROOT / "build"
    _write_json(build_dir / "raw_stories.json", [story.model_dump(mode="json") for story in candidates])
    if use_openai:
        if not os.getenv("OPENAI_API_KEY"):
            raise RuntimeError("OPENAI_API_KEY is required in the environment")
        edition = curate_stories(candidates)
    else:
        LOGGER.warning("Using deterministic source-only edition; this mode is for local rendering/tests only")
        edition = deterministic_edition(candidates)
    curated_payload = edition.model_dump(mode="json")
    _write_json(build_dir / "curated.json", curated_payload)

    public_dir = output_dir or PROJECT_ROOT / "public"
    public_dir.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix="e1002-pages-", dir=public_dir.parent))
    try:
        staging_pages = staging_root / "pages"
        rendered = render_edition(edition, staging_pages, primary.issue_date.isoformat())
        pages: list[ManifestPage] = []
        for index, (preview_path, raw_path) in enumerate(rendered, start=1):
            if raw_path.stat().st_size != RAW_PAGE_SIZE:
                raise ValueError(f"Page {index} has invalid raw size")
            pages.append(
                ManifestPage(
                    index=index,
                    url=f"pages/page_{index}.epd",
                    preview_url=f"pages/page_{index}.png",
                    sha256=_sha256(raw_path),
                    size=raw_path.stat().st_size,
                    width=WIDTH,
                    height=HEIGHT,
                )
            )
        now = datetime.now(SINGAPORE)
        manifest = Manifest(
            generated_at=now,
            source_issue=primary.url,
            source_issues=[issue.url for issue in source_issues],
            generation_id=_generation_id(primary.issue_date.isoformat(), curated_payload),
            pages=pages,
        )
        if manifest.page_count != PAGE_COUNT or len(manifest.pages) != PAGE_COUNT:
            raise ValueError("Manifest must contain exactly six pages")
        _write_json(staging_root / "manifest.json", manifest.model_dump(mode="json"))

        destination_pages = public_dir / "pages"
        if destination_pages.exists():
            shutil.rmtree(destination_pages)
        shutil.move(str(staging_pages), str(destination_pages))
        shutil.move(str(staging_root / "manifest.json"), str(public_dir / "manifest.json"))
        LOGGER.info("Manifest generated: %s", manifest.generation_id)
        return manifest
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate the E1002 AI Daily edition")
    parser.add_argument("--rss-file", help="Use a local RSS fixture instead of the network")
    parser.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "public")
    parser.add_argument("--no-openai", action="store_true", help="Local rendering/test mode only")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    generate(rss_file=args.rss_file, output_dir=args.output_dir, use_openai=not args.no_openai)


if __name__ == "__main__":
    main()

