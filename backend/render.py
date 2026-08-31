from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
from typing import Final

from PIL import Image, ImageDraw, ImageFont

from .models import CuratedEdition, CuratedStory

LOGGER = logging.getLogger(__name__)
WIDTH: Final = 800
HEIGHT: Final = 480
PAGE_COUNT: Final = 6
STORIES_PER_PAGE: Final = 3
RAW_PAGE_SIZE: Final = WIDTH * HEIGHT // 2

# Native Spectra 6 colors and the nibble codes expected by Seeed's E1002 EPaper driver.
E6_COLORS: Final[list[tuple[int, int, int]]] = [
    (255, 255, 255),  # white  -> 0x0
    (29, 185, 84),    # green  -> 0x2
    (229, 57, 53),    # red    -> 0x6
    (255, 216, 0),    # yellow -> 0xB
    (0, 76, 255),     # blue   -> 0xD
    (0, 0, 0),        # black  -> 0xF
]
E6_CODES: Final[list[int]] = [0x0, 0x2, 0x6, 0xB, 0xD, 0xF]
BLACK = E6_COLORS[5]
WHITE = E6_COLORS[0]
ACCENTS = [E6_COLORS[4], E6_COLORS[2], E6_COLORS[1]]


def find_cjk_font() -> str:
    configured = os.getenv("CJK_FONT_PATH")
    candidates = [
        configured,
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise FileNotFoundError("No Chinese-capable font found; install fonts-noto-cjk or set CJK_FONT_PATH")


def _font(path: str, size: int, index: int = 0) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size, index=index)


def _text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return box[2] - box[0]


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    closing_punctuation = "，。；：！？、,.!?;:)）】》」』"
    for char in " ".join(text.split()):
        candidate = current + char
        if current and _text_width(draw, candidate, font) > width:
            # Avoid a Chinese/Latin closing punctuation mark stranded at line start.
            if char in closing_punctuation and len(current.rstrip()) > 1:
                trimmed = current.rstrip()
                lines.append(trimmed[:-1].rstrip())
                current = trimmed[-1] + char
            else:
                lines.append(current.rstrip())
                current = char.lstrip()
        else:
            current = candidate
    if current:
        lines.append(current.rstrip())
    return lines or [""]


def _ellipsis(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, width: int) -> str:
    if _text_width(draw, text, font) <= width:
        return text
    clipped = text
    while clipped and _text_width(draw, clipped + "…", font) > width:
        clipped = clipped[:-1]
    return clipped.rstrip() + "…"


def _category_color(category: str) -> tuple[int, int, int]:
    digest = hashlib.sha1(category.encode("utf-8")).digest()[0]
    return ACCENTS[digest % len(ACCENTS)]


def _draw_story(
    draw: ImageDraw.ImageDraw,
    story: CuratedStory,
    top: int,
    font_path: str,
) -> None:
    # Yellow is too low-contrast for small Chinese type. Blue, red and green
    # remain useful category accents; body text and metadata stay black.
    accent = _category_color(story.category)
    category_font = _font(font_path, 17)
    title_font = _font(font_path, 25)
    summary_font = _font(font_path, 18)
    source_font = _font(font_path, 14)
    left, right = 30, WIDTH - 30

    draw.rectangle((left, top + 10, left + 6, top + 32), fill=accent)
    draw.text((left + 15, top + 8), story.category, fill=accent, font=category_font)
    draw.text((right - 50, top + 9), f"{story.importance:.2f}", fill=BLACK, font=source_font, anchor="ra")

    title = _ellipsis(draw, story.title, title_font, right - left)
    draw.text((left, top + 36), title, fill=BLACK, font=title_font)

    summary_lines = _wrap(draw, story.summary, summary_font, right - left)
    for line_index, line in enumerate(summary_lines[:2]):
        if line_index == 1 and len(summary_lines) > 2:
            line = _ellipsis(draw, line + "…", summary_font, right - left)
        draw.text((left, top + 69 + line_index * 23), line, fill=BLACK, font=summary_font)

    source_text = _ellipsis(draw, f"来源  {story.source}", source_font, 260)
    draw.text((left, top + 119), source_text, fill=BLACK, font=source_font)
    draw.line((left, top + 140, right, top + 140), fill=BLACK, width=1)


def _palette_image() -> Image.Image:
    palette = Image.new("P", (1, 1))
    flat: list[int] = []
    for color in E6_COLORS:
        flat.extend(color)
    flat.extend([0] * (768 - len(flat)))
    palette.putpalette(flat)
    return palette


def quantize_e6(image: Image.Image) -> Image.Image:
    indexed = image.convert("RGB").quantize(palette=_palette_image(), dither=Image.Dither.NONE)
    return indexed.convert("RGB")


def pack_e1002_4bpp(image: Image.Image) -> bytes:
    if image.size != (WIDTH, HEIGHT):
        raise ValueError(f"Expected {WIDTH}x{HEIGHT}, got {image.size}")
    rgb_to_code = {color: code for color, code in zip(E6_COLORS, E6_CODES, strict=True)}
    pixels = list(image.convert("RGB").get_flattened_data())
    output = bytearray(RAW_PAGE_SIZE)
    for pixel_index in range(0, len(pixels), 2):
        try:
            high = rgb_to_code[pixels[pixel_index]]
            low = rgb_to_code[pixels[pixel_index + 1]]
        except KeyError as exc:
            raise ValueError(f"Image contains a non-Spectra-6 color: {exc.args[0]}") from exc
        output[pixel_index // 2] = (high << 4) | low
    return bytes(output)


def render_page(stories: list[CuratedStory], page_index: int, issue_date: str, font_path: str | None = None) -> Image.Image:
    if len(stories) != STORIES_PER_PAGE:
        raise ValueError("Each page must contain exactly three stories")
    if not 1 <= page_index <= PAGE_COUNT:
        raise ValueError("Page index out of range")
    font_path = font_path or find_cjk_font()
    image = Image.new("RGB", (WIDTH, HEIGHT), WHITE)
    draw = ImageDraw.Draw(image)
    header_font = _font(font_path, 29)
    meta_font = _font(font_path, 17)
    draw.text((29, 10), "AI DAILY", fill=BLACK, font=header_font)
    draw.text((WIDTH // 2, 23), issue_date.upper(), fill=BLACK, font=meta_font, anchor="mm")
    draw.text((WIDTH - 29, 23), f"{page_index} / {PAGE_COUNT}", fill=BLACK, font=meta_font, anchor="rm")
    draw.line((29, 51, WIDTH - 29, 51), fill=BLACK, width=2)
    for index, story in enumerate(stories):
        _draw_story(draw, story, 53 + index * 142, font_path)
    return quantize_e6(image)


def render_edition(
    edition: CuratedEdition,
    output_dir: str | Path,
    issue_date: str,
    font_path: str | None = None,
) -> list[tuple[Path, Path]]:
    if len(edition.stories) != PAGE_COUNT * STORIES_PER_PAGE:
        raise ValueError("Edition must contain exactly 18 stories")
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    rendered: list[tuple[Path, Path]] = []
    for page_index in range(1, PAGE_COUNT + 1):
        start = (page_index - 1) * STORIES_PER_PAGE
        page = render_page(edition.stories[start : start + STORIES_PER_PAGE], page_index, issue_date, font_path)
        preview_path = output / f"page_{page_index}.png"
        raw_path = output / f"page_{page_index}.epd"
        page.save(preview_path, format="PNG", optimize=True)
        raw = pack_e1002_4bpp(page)
        raw_path.write_bytes(raw)
        if raw_path.stat().st_size != RAW_PAGE_SIZE:
            raise ValueError(f"Invalid packed page size: {raw_path}")
        rendered.append((preview_path, raw_path))
    LOGGER.info("6 rendered pages validated at 800x480, three stories per page")
    return rendered
