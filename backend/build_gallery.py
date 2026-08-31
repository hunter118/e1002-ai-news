from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path
from typing import Final

from PIL import Image, ImageOps

from .render import E6_CODES, E6_COLORS, HEIGHT, RAW_PAGE_SIZE, WIDTH, pack_e1002_4bpp

MAX_PAGES: Final = 20
SUPPORTED_SUFFIXES: Final = frozenset({".jpg", ".jpeg", ".png", ".webp", ".epd"})
CODE_TO_RGB: Final = dict(zip(E6_CODES, E6_COLORS, strict=True))


def _natural_key(path: Path) -> list[tuple[int, int | str]]:
    parts = re.split(r"(\d+)", path.name.casefold())
    return [(0, int(part)) if part.isdigit() else (1, part) for part in parts]


def discover_photos(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise ValueError(f"Photo directory does not exist: {directory}")
    photos = sorted(
        (path for path in directory.iterdir() if path.is_file() and path.suffix.casefold() in SUPPORTED_SUFFIXES),
        key=_natural_key,
    )
    if len(photos) > MAX_PAGES:
        raise ValueError(f"Gallery supports at most {MAX_PAGES} photos, got {len(photos)}")
    return photos


def load_interval_ms(config_path: Path) -> int:
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid gallery config: {config_path}") from exc
    seconds = config.get("interval_seconds")
    if not isinstance(seconds, int) or isinstance(seconds, bool):
        raise ValueError("gallery/config.json interval_seconds must be an integer")
    if seconds != 0 and not 10 <= seconds <= 86_400:
        raise ValueError("interval_seconds must be 0, or between 10 and 86400")
    return seconds * 1000


def _rgb_on_white(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode in ("RGBA", "LA") or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        return Image.alpha_composite(background, rgba).convert("RGB")
    return image.convert("RGB")


def render_photo(source: Path) -> Image.Image:
    if source.suffix.casefold() == ".epd":
        return unpack_e1002_4bpp(source.read_bytes())
    with Image.open(source) as opened:
        rgb = _rgb_on_white(opened)
        fitted = ImageOps.fit(
            rgb,
            (WIDTH, HEIGHT),
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
    palette = Image.new("P", (1, 1))
    flat_palette = [channel for color in E6_COLORS for channel in color]
    palette.putpalette(flat_palette + [0] * (768 - len(flat_palette)))
    return fitted.quantize(palette=palette, dither=Image.Dither.FLOYDSTEINBERG).convert("RGB")


def unpack_e1002_4bpp(data: bytes) -> Image.Image:
    if len(data) != RAW_PAGE_SIZE:
        raise ValueError(f"E1002 page must be {RAW_PAGE_SIZE} bytes, got {len(data)}")
    pixels: list[tuple[int, int, int]] = []
    for byte in data:
        for code in (byte >> 4, byte & 0x0F):
            try:
                pixels.append(CODE_TO_RGB[code])
            except KeyError as exc:
                raise ValueError(f"Unknown E1002 color code: 0x{code:X}") from exc
    image = Image.new("RGB", (WIDTH, HEIGHT))
    image.putdata(pixels)
    return image


def build_gallery(photos_dir: Path, config_path: Path, output_dir: Path) -> dict[str, object]:
    photos = discover_photos(photos_dir)
    interval_ms = load_interval_ms(config_path)

    generation = hashlib.sha256()
    generation.update(f"interval_ms={interval_ms}\n".encode())
    for source in photos:
        generation.update(source.name.encode("utf-8"))
        generation.update(b"\0")
        generation.update(source.read_bytes())
    generation_id = f"github-{generation.hexdigest()[:16]}"

    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="e1002-gallery-build-", dir=output_dir.parent))
    try:
        pages_dir = staging / "pages"
        previews_dir = staging / "previews"
        pages_dir.mkdir()
        previews_dir.mkdir()
        manifest_pages: list[dict[str, object]] = []
        for index, source in enumerate(photos, start=1):
            image = render_photo(source)
            raw = pack_e1002_4bpp(image)
            raw_path = pages_dir / f"page_{index}.epd"
            raw_path.write_bytes(raw)
            page: dict[str, object] = {
                "index": index,
                "url": f"pages/page_{index}.epd",
                "sha256": hashlib.sha256(raw).hexdigest(),
                "size": RAW_PAGE_SIZE,
                "width": WIDTH,
                "height": HEIGHT,
                "format": "e1002-4bpp",
                "source_name": source.name,
            }
            if source.suffix.casefold() != ".epd":
                preview_path = previews_dir / f"page_{index}.png"
                image.save(preview_path, format="PNG", optimize=True)
                page["preview_url"] = f"previews/page_{index}.png"
            manifest_pages.append(page)

        manifest: dict[str, object] = {
            "schema_version": 1,
            "kind": "gallery",
            "generation_id": generation_id,
            "page_count": len(manifest_pages),
            "interval_ms": interval_ms,
            "pages": manifest_pages,
        }
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (staging / ".nojekyll").write_text("", encoding="utf-8")
        if output_dir.exists():
            shutil.rmtree(output_dir)
        staging.replace(output_dir)
        return manifest
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a GitHub-managed E1002 photo gallery")
    parser.add_argument("--photos", type=Path, default=Path("gallery/photos"))
    parser.add_argument("--config", type=Path, default=Path("gallery/config.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_gallery(args.photos, args.config, args.output)
    print(
        f"Built gallery generation {manifest['generation_id']} "
        f"with {manifest['page_count']} photos"
    )


if __name__ == "__main__":
    main()
