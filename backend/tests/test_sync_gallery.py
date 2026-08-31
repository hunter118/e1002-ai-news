from __future__ import annotations

import hashlib
import json

import pytest

from backend import sync_gallery as gallery_sync
from backend.sync_gallery import PAGE_BYTES, _validate_page


def test_validate_gallery_page_accepts_only_native_e1002_codes() -> None:
    page = bytes([0x02, 0x6B, 0xDF, 0xF0]) * (PAGE_BYTES // 4)
    _validate_page(page, hashlib.sha256(page).hexdigest())


def test_validate_gallery_page_rejects_unknown_nibble() -> None:
    page = bytes([0x01]) * PAGE_BYTES
    with pytest.raises(ValueError, match="invalid E1002 color codes"):
        _validate_page(page, hashlib.sha256(page).hexdigest())


def test_validate_gallery_page_rejects_wrong_hash() -> None:
    page = bytes([0x00]) * PAGE_BYTES
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _validate_page(page, "f" * 64)


def test_sync_gallery_preserves_disabled_auto_advance(monkeypatch, tmp_path) -> None:
    source = {
        "schema_version": 1,
        "kind": "gallery",
        "generation_id": "paused",
        "page_count": 0,
        "interval_ms": 0,
        "pages": [],
    }
    monkeypatch.setattr(
        gallery_sync,
        "_download",
        lambda _url: (json.dumps(source).encode(), "application/json"),
    )
    output = tmp_path / "mirror"
    mirrored = gallery_sync.sync_gallery("https://gallery.example/api/gallery/manifest", output)
    assert mirrored["interval_ms"] == 0
    assert json.loads((output / "manifest.json").read_text())["interval_ms"] == 0
