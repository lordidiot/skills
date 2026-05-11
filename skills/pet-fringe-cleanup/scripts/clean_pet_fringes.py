#!/usr/bin/env python3
"""Clean chroma-key fringe from a finalized hatch-pet spritesheet."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import sys

try:
    from PIL import Image, ImageChops, ImageFilter
except ImportError as exc:
    raise SystemExit("Pillow is required. Run with `uv run --with pillow python ...`.") from exc


COLUMNS = 8
ROWS = 9
CELL_WIDTH = 192
CELL_HEIGHT = 208
ROW_SPECS = [
    ("idle", 0, 6),
    ("running-right", 1, 8),
    ("running-left", 2, 8),
    ("waving", 3, 4),
    ("jumping", 4, 5),
    ("failed", 5, 8),
    ("waiting", 6, 6),
    ("running", 7, 6),
    ("review", 8, 6),
]


def parse_hex_color(value: str) -> tuple[int, int, int]:
    raw = value.strip().lstrip("#")
    if len(raw) != 6:
        raise SystemExit(f"invalid key color: {value}")
    return tuple(int(raw[i : i + 2], 16) for i in (0, 2, 4))


def load_key_color(run_dir: Path, override: str | None) -> tuple[int, int, int]:
    if override:
        return parse_hex_color(override)
    request_path = run_dir / "pet_request.json"
    if request_path.exists():
        request = json.loads(request_path.read_text(encoding="utf-8"))
        chroma = request.get("chroma_key")
        if isinstance(chroma, dict) and isinstance(chroma.get("hex"), str):
            return parse_hex_color(chroma["hex"])
    raise SystemExit("could not infer chroma key; pass --key-color '#RRGGBB'")


def load_pet_slug(run_dir: Path, override: str | None) -> str:
    if override:
        return override
    request_path = run_dir / "pet_request.json"
    if request_path.exists():
        request = json.loads(request_path.read_text(encoding="utf-8"))
        for key in ("slug", "pet_slug", "pet_name", "name"):
            value = request.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip().lower().replace(" ", "-")
    return run_dir.name.replace("-pet-run", "").lower()


def hue(rgb: tuple[int, int, int]) -> float:
    red, green, blue = (channel / 255.0 for channel in rgb)
    high = max(red, green, blue)
    low = min(red, green, blue)
    delta = high - low
    if delta == 0:
        return 0.0
    if high == red:
        value = ((green - blue) / delta) % 6
    elif high == green:
        value = (blue - red) / delta + 2
    else:
        value = (red - green) / delta + 4
    return value * 60.0


def saturation(rgb: tuple[int, int, int]) -> float:
    high = max(rgb)
    low = min(rgb)
    if high == 0:
        return 0.0
    return (high - low) / high


def hue_distance(a: float, b: float) -> float:
    diff = abs(a - b) % 360.0
    return min(diff, 360.0 - diff)


def channel_distance(a: tuple[int, int, int], b: tuple[int, int, int]) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]), abs(a[2] - b[2]))


def key_channels(key: tuple[int, int, int]) -> list[int]:
    high = max(key)
    return [index for index, value in enumerate(key) if value >= high - 16 and value >= 128]


def key_dominance(rgb: tuple[int, int, int], key: tuple[int, int, int]) -> float:
    channels = key_channels(key)
    if not channels:
        return 0.0
    values = [float(value) for value in rgb]
    spill = min(values[index] for index in channels)
    non_spill = max((values[index] for index in range(3) if index not in channels), default=0.0)
    return spill - non_spill


def is_key_like(
    rgb: tuple[int, int, int],
    key: tuple[int, int, int],
    *,
    key_hue: float,
    hue_threshold: float,
) -> bool:
    if channel_distance(rgb, key) <= 42:
        return True
    if key_dominance(rgb, key) >= 10:
        return True
    if saturation(rgb) >= 0.35 and hue_distance(hue(rgb), key_hue) <= hue_threshold:
        return True
    return False


def edge_mask(alpha: Image.Image) -> Image.Image:
    dilated = alpha.filter(ImageFilter.MaxFilter(5))
    eroded = alpha.filter(ImageFilter.MinFilter(3))
    outer = dilated.point(lambda value: 255 if value > 0 else 0)
    inner = eroded.point(lambda value: 255 if value > 0 else 0)
    return ImageChops.subtract(outer, inner)


def nearest_clean_color(
    image: Image.Image,
    x: int,
    y: int,
    key: tuple[int, int, int],
    key_hue: float,
    hue_threshold: float,
) -> tuple[int, int, int] | None:
    pixels = image.load()
    width, height = image.size
    best: tuple[int, int, int] | None = None
    best_score = 10_000.0
    for radius in range(1, 8):
        left = max(0, x - radius)
        right = min(width - 1, x + radius)
        top = max(0, y - radius)
        bottom = min(height - 1, y + radius)
        for yy in range(top, bottom + 1):
            for xx in range(left, right + 1):
                if xx != left and xx != right and yy != top and yy != bottom:
                    continue
                red, green, blue, alpha = pixels[xx, yy]
                rgb = (red, green, blue)
                if alpha < 80 or is_key_like(
                    rgb,
                    key,
                    key_hue=key_hue,
                    hue_threshold=hue_threshold,
                ):
                    continue
                score = (xx - x) ** 2 + (yy - y) ** 2 - alpha / 255.0
                if score < best_score:
                    best_score = score
                    best = rgb
        if best is not None:
            return best
    return None


def fallback_decontaminate(rgb: tuple[int, int, int], key: tuple[int, int, int]) -> tuple[int, int, int]:
    channels = list(rgb)
    spill_channels = key_channels(key)
    clean_channels = [index for index in range(3) if index not in spill_channels]
    if not spill_channels or not clean_channels:
        return rgb
    cap = max(channels[index] for index in clean_channels)
    for index in spill_channels:
        channels[index] = min(channels[index], cap)
    return tuple(channels)


def clean_cell(
    cell: Image.Image,
    key: tuple[int, int, int],
    *,
    hue_threshold: float,
) -> tuple[Image.Image, dict[str, int]]:
    image = cell.convert("RGBA")
    pixels = image.load()
    alpha = image.getchannel("A")
    edge = edge_mask(alpha)
    edge_pixels = edge.load()
    key_hue = hue(key)

    stats = {
        "key_like": 0,
        "removed": 0,
        "recolored": 0,
        "fallback_decontaminated": 0,
        "skipped_non_edge": 0,
        "untouched_visible": 0,
    }

    for y in range(image.height):
        for x in range(image.width):
            red, green, blue, alpha_value = pixels[x, y]
            if alpha_value == 0:
                if red or green or blue:
                    pixels[x, y] = (0, 0, 0, 0)
                continue
            if edge_pixels[x, y] == 0:
                stats["skipped_non_edge"] += 1
                stats["untouched_visible"] += 1
                continue
            rgb = (red, green, blue)
            if not is_key_like(rgb, key, key_hue=key_hue, hue_threshold=hue_threshold):
                stats["untouched_visible"] += 1
                continue

            stats["key_like"] += 1
            if alpha_value <= 32:
                pixels[x, y] = (0, 0, 0, 0)
                stats["removed"] += 1
                continue

            replacement = nearest_clean_color(image, x, y, key, key_hue, hue_threshold)
            if replacement is not None:
                pixels[x, y] = (*replacement, alpha_value)
                stats["recolored"] += 1
                continue

            if edge_pixels[x, y] > 0 or alpha_value < 255:
                pixels[x, y] = (*fallback_decontaminate(rgb, key), alpha_value)
                stats["fallback_decontaminated"] += 1
            else:
                stats["untouched_visible"] += 1

    return image, stats


def source_atlas_path(run_dir: Path, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    final_dir = run_dir / "final"
    backup = final_dir / "spritesheet-before-fringe-clean.png"
    current = final_dir / "spritesheet.png"
    if backup.exists():
        return backup
    if current.exists():
        backup.write_bytes(current.read_bytes())
        webp = final_dir / "spritesheet.webp"
        if webp.exists():
            (final_dir / "spritesheet-before-fringe-clean.webp").write_bytes(webp.read_bytes())
        return backup
    raise SystemExit(f"missing atlas: {current}")


def compose_atlas(cleaned_cells: dict[tuple[int, int], Image.Image]) -> Image.Image:
    atlas = Image.new("RGBA", (COLUMNS * CELL_WIDTH, ROWS * CELL_HEIGHT), (0, 0, 0, 0))
    for (row, column), cell in cleaned_cells.items():
        atlas.alpha_composite(cell, (column * CELL_WIDTH, row * CELL_HEIGHT))
    return atlas


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--source-atlas")
    parser.add_argument("--key-color")
    parser.add_argument("--pet-slug")
    parser.add_argument("--hue-threshold", type=float, default=18.0)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).expanduser().resolve()
    final_dir = run_dir / "final"
    qa_dir = run_dir / "qa"
    key = load_key_color(run_dir, args.key_color)
    source = source_atlas_path(run_dir, args.source_atlas)
    with Image.open(source) as opened:
        atlas = opened.convert("RGBA")
    if atlas.size != (COLUMNS * CELL_WIDTH, ROWS * CELL_HEIGHT):
        raise SystemExit(f"expected atlas {COLUMNS * CELL_WIDTH}x{ROWS * CELL_HEIGHT}, got {atlas.size}")

    cleaned_cells: dict[tuple[int, int], Image.Image] = {}
    rows = []
    for state, row, frame_count in ROW_SPECS:
        for column in range(frame_count):
            left = column * CELL_WIDTH
            top = row * CELL_HEIGHT
            cell = atlas.crop((left, top, left + CELL_WIDTH, top + CELL_HEIGHT))
            cleaned, stats = clean_cell(cell, key, hue_threshold=args.hue_threshold)
            cleaned_cells[(row, column)] = cleaned
            rows.append({"state": state, "column": column, **stats})

    output = compose_atlas(cleaned_cells)
    final_dir.mkdir(parents=True, exist_ok=True)
    png_path = final_dir / "spritesheet.png"
    webp_path = final_dir / "spritesheet.webp"
    output.save(png_path)
    output.save(webp_path, format="WEBP", lossless=True, quality=100, method=6)
    qa_dir.mkdir(parents=True, exist_ok=True)
    (qa_dir / "fringe-cleanup.json").write_text(
        json.dumps(
            {
                "ok": True,
                "source_atlas": str(source),
                "key_color": f"#{key[0]:02X}{key[1]:02X}{key[2]:02X}",
                "hue_threshold": args.hue_threshold,
                "rows": rows,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    installed = None
    if args.install:
        pet_slug = load_pet_slug(run_dir, args.pet_slug)
        pet_dir = Path.home() / ".codex" / "pets" / pet_slug
        pet_dir.mkdir(parents=True, exist_ok=True)
        installed = pet_dir / "spritesheet.webp"
        shutil.copy2(webp_path, installed)

    print(
        json.dumps(
            {
                "ok": True,
                "source": str(source),
                "spritesheet_png": str(png_path),
                "spritesheet_webp": str(webp_path),
                "report": str(qa_dir / "fringe-cleanup.json"),
                "installed": str(installed) if installed else None,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
