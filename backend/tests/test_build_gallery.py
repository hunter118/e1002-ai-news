from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from backend.build_gallery import build_gallery, discover_photos, unpack_e1002_4bpp
from backend.render import E6_COLORS, RAW_PAGE_SIZE


def _photo(path: Path, color: tuple[int, int, int], size: tuple[int, int] = (1200, 600)) -> None:
    Image.new("RGB", size, color).save(path)


def test_build_gallery_orders_photos_and_disables_auto_advance(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    _photo(photos / "10_last.png", (255, 255, 255))
    _photo(photos / "02_first.jpg", (0, 0, 0))
    config = tmp_path / "config.json"
    config.write_text('{"interval_seconds": 0}', encoding="utf-8")

    output = tmp_path / "output"
    manifest = build_gallery(photos, config, output)

    assert [page["source_name"] for page in manifest["pages"]] == ["02_first.jpg", "10_last.png"]
    assert manifest["interval_ms"] == 0
    assert manifest["page_count"] == 2
    assert all((output / page["url"]).stat().st_size == RAW_PAGE_SIZE for page in manifest["pages"])
    assert json.loads((output / "manifest.json").read_text())["generation_id"].startswith("github-")


def test_output_pixels_are_native_six_color_values(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    _photo(photos / "01_gradient.png", (127, 91, 203))
    config = tmp_path / "config.json"
    config.write_text('{"interval_seconds": 600}', encoding="utf-8")
    output = tmp_path / "output"

    build_gallery(photos, config, output)
    decoded = unpack_e1002_4bpp((output / "pages/page_1.epd").read_bytes())

    assert set(decoded.get_flattened_data()).issubset(set(E6_COLORS))
    assert decoded.size == (800, 480)


def test_existing_epd_can_be_used_as_a_migration_source(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    raw = bytes([0x0F]) * RAW_PAGE_SIZE
    (photos / "01_existing.epd").write_bytes(raw)
    config = tmp_path / "config.json"
    config.write_text('{"interval_seconds": 0}', encoding="utf-8")
    output = tmp_path / "output"

    manifest = build_gallery(photos, config, output)

    assert (output / "pages/page_1.epd").read_bytes() == raw
    assert manifest["pages"][0]["source_name"] == "01_existing.epd"


def test_invalid_interval_and_photo_limit_are_rejected(tmp_path: Path) -> None:
    photos = tmp_path / "photos"
    photos.mkdir()
    for index in range(21):
        _photo(photos / f"{index:02}.png", (255, 255, 255), (10, 10))
    with pytest.raises(ValueError, match="at most 20"):
        discover_photos(photos)

    empty = tmp_path / "empty"
    empty.mkdir()
    config = tmp_path / "config.json"
    config.write_text('{"interval_seconds": 5}', encoding="utf-8")
    with pytest.raises(ValueError, match="between 10 and 86400"):
        build_gallery(empty, config, tmp_path / "output")
