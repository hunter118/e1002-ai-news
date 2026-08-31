from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import urllib.parse
import urllib.request
from pathlib import Path

WIDTH = 800
HEIGHT = 480
PAGE_BYTES = WIDTH * HEIGHT // 2
MAX_PAGES = 20
VALID_NIBBLES = frozenset((0x0, 0x2, 0x6, 0xB, 0xD, 0xF))
USER_AGENT = "E1002-Gallery-Sync/1.0"


def _download(url: str, timeout: int = 30) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read(), response.headers.get_content_type()


def _validate_page(data: bytes, expected_hash: str) -> None:
    if len(data) != PAGE_BYTES:
        raise ValueError(f"Gallery page must be {PAGE_BYTES} bytes, got {len(data)}")
    actual_hash = hashlib.sha256(data).hexdigest()
    if actual_hash != expected_hash:
        raise ValueError(f"Gallery page SHA-256 mismatch: {actual_hash} != {expected_hash}")
    used_nibbles = {nibble for byte in data for nibble in (byte >> 4, byte & 0x0F)}
    if not used_nibbles.issubset(VALID_NIBBLES):
        raise ValueError(f"Gallery page contains invalid E1002 color codes: {sorted(used_nibbles)}")


def sync_gallery(source_url: str, output_dir: Path) -> dict[str, object]:
    manifest_bytes, _ = _download(source_url)
    source = json.loads(manifest_bytes)
    source_parts = urllib.parse.urlsplit(source_url)
    source_root = urllib.parse.urlunsplit((source_parts.scheme, source_parts.netloc, "/", "", ""))
    if source.get("schema_version") != 1 or source.get("kind") != "gallery":
        raise ValueError("Unsupported gallery manifest")
    pages = source.get("pages")
    if not isinstance(pages, list) or source.get("page_count") != len(pages):
        raise ValueError("Gallery page count does not match its page list")
    if not 0 <= len(pages) <= MAX_PAGES:
        raise ValueError(f"Gallery must contain 0 to {MAX_PAGES} pages")
    interval_ms = source.get("interval_ms")
    if not isinstance(interval_ms, int) or (interval_ms != 0 and not 10_000 <= interval_ms <= 86_400_000):
        raise ValueError("Gallery interval must be disabled or between 10 seconds and 24 hours")
    generation_id = source.get("generation_id")
    if not isinstance(generation_id, str) or not generation_id:
        raise ValueError("Gallery generation ID is missing")

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="e1002-gallery-", dir=output_dir.parent))
    try:
        staging_pages = staging / "pages"
        staging_pages.mkdir()
        mirrored_pages: list[dict[str, object]] = []
        for index, page in enumerate(pages, start=1):
            if not isinstance(page, dict) or page.get("index") != index:
                raise ValueError(f"Invalid gallery page entry {index}")
            source_page_url = page.get("url")
            expected_hash = page.get("sha256")
            if not isinstance(source_page_url, str) or not isinstance(expected_hash, str):
                raise ValueError(f"Gallery page {index} is missing URL or hash")
            if len(expected_hash) != 64 or page.get("size") != PAGE_BYTES:
                raise ValueError(f"Gallery page {index} metadata is invalid")
            page_bytes, _ = _download(urllib.parse.urljoin(source_root, source_page_url))
            _validate_page(page_bytes, expected_hash.lower())
            raw_path = staging_pages / f"page_{index}.epd"
            raw_path.write_bytes(page_bytes)

            mirrored = {
                "index": index,
                "url": f"pages/page_{index}.epd",
                "sha256": expected_hash.lower(),
                "size": PAGE_BYTES,
                "width": WIDTH,
                "height": HEIGHT,
                "format": "e1002-4bpp",
            }
            mirrored_pages.append(mirrored)

        mirrored_manifest: dict[str, object] = {
            "schema_version": 1,
            "kind": "gallery",
            "generation_id": generation_id,
            "page_count": len(mirrored_pages),
            "interval_ms": interval_ms,
            "pages": mirrored_pages,
        }
        (staging / "manifest.json").write_text(
            json.dumps(mirrored_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (staging / ".nojekyll").write_text("", encoding="utf-8")
        if output_dir.exists():
            shutil.rmtree(output_dir)
        staging.replace(output_dir)
        return mirrored_manifest
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Mirror and validate the E1002 gallery for device delivery")
    parser.add_argument("--source", required=True, help="Public Sites gallery manifest URL")
    parser.add_argument("--output", type=Path, required=True, help="Gallery branch checkout directory")
    args = parser.parse_args()
    manifest = sync_gallery(args.source, args.output)
    print(f"Validated gallery generation {manifest['generation_id']} with {manifest['page_count']} pages")


if __name__ == "__main__":
    main()
