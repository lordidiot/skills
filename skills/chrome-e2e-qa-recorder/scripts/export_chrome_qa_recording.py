#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "pillow>=10.0.0",
# ]
# ///

import argparse
import base64
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


TOOLS = {
    "click",
    "drag",
    "get_app_state",
    "perform_secondary_action",
    "press_key",
    "scroll",
    "set_value",
    "type_text",
}
START_PREFIX = "CODEX_QA_RECORDING_START:"
END_PREFIX = "CODEX_QA_RECORDING_END:"


@dataclass
class ToolCall:
    line: int
    timestamp: str
    name: str
    call_id: str
    arguments: dict[str, Any]


@dataclass
class EventFrame:
    index: int
    line: int
    timestamp: str
    call: ToolCall
    image: Image.Image


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a marked Chrome Computer Use QA run from a Codex session JSONL."
    )
    parser.add_argument("--run-id", help="Recording run id used in CODEX_QA_RECORDING markers.")
    parser.add_argument("--session-jsonl", type=Path, help="Use this session JSONL instead of copying the current session.")
    parser.add_argument("--codex-home", default=os.environ.get("CODEX_HOME", "~/.codex"))
    parser.add_argument("--output-dir", type=Path, help="Output directory. Defaults to ./qa-recordings/<run-id>.")
    parser.add_argument("--duration", type=float, default=2.0, help="Seconds per frame. Defaults to 2.")
    parser.add_argument("--fps", type=int, default=30, help="Output video FPS. Defaults to 30.")
    parser.add_argument("--include-all", action="store_true", help="Ignore markers and export all Computer Use frames.")
    parser.add_argument("--keep-frames", action="store_true", help="Keep rendered frame PNGs.")
    return parser.parse_args()


def safe_run_id(value: str | None) -> str:
    if value:
        run_id = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
        if run_id:
            return run_id
    return "qa-" + datetime.now().strftime("%Y%m%d-%H%M%S")


def find_current_session(codex_home: Path) -> Path:
    thread_id = os.environ.get("CODEX_THREAD_ID")
    if not thread_id:
        raise RuntimeError("set CODEX_THREAD_ID or pass --session-jsonl")
    sessions_dir = codex_home.expanduser() / "sessions"
    matches = sorted(sessions_dir.glob(f"**/*{thread_id}*.jsonl"))
    if not matches:
        raise RuntimeError(f"no Codex session JSONL found for CODEX_THREAD_ID={thread_id}")
    if len(matches) > 1:
        raise RuntimeError("multiple session JSONLs matched CODEX_THREAD_ID:\n" + "\n".join(map(str, matches)))
    return matches[0]


def copy_session(source: Path, output_dir: Path) -> Path:
    destination = output_dir / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        shutil.copy2(source, destination)
    return destination


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if line.strip():
                yield line_number, line, json.loads(line)


def marker_lines(session_jsonl: Path, run_id: str) -> tuple[int | None, int | None]:
    start = f"{START_PREFIX} {run_id}"
    end = f"{END_PREFIX} {run_id}"
    starts: list[int] = []
    ends: list[int] = []
    for line_number, raw, _record in iter_jsonl(session_jsonl):
        if start in raw:
            starts.append(line_number)
        if end in raw:
            ends.append(line_number)
    if len(starts) > 1:
        raise RuntimeError(f"multiple start markers found for run id {run_id}: {starts}")
    if len(ends) > 1:
        raise RuntimeError(f"multiple end markers found for run id {run_id}: {ends}")
    start_line = starts[0] if starts else None
    end_line = ends[0] if ends else None
    if start_line is not None and end_line is not None and end_line < start_line:
        raise RuntimeError(f"end marker appears before start marker for run id {run_id}")
    return start_line, end_line


def parse_call_arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def extract_image(output: Any) -> Image.Image | None:
    if not isinstance(output, list):
        return None
    for item in output:
        if not isinstance(item, dict) or item.get("type") != "input_image":
            continue
        image_url = item.get("image_url", "")
        match = re.match(r"data:image/[^;]+;base64,(.+)", image_url)
        if not match:
            continue
        return Image.open(BytesIO(base64.b64decode(match.group(1)))).convert("RGB")
    return None


def in_range(line: int, start_line: int | None, end_line: int | None) -> bool:
    if start_line is not None and line <= start_line:
        return False
    if end_line is not None and line >= end_line:
        return False
    return True


def load_events(session_jsonl: Path, start_line: int | None, end_line: int | None) -> list[EventFrame]:
    calls: dict[str, ToolCall] = {}
    events: list[EventFrame] = []
    for line_number, _raw, record in iter_jsonl(session_jsonl):
        if not in_range(line_number, start_line, end_line):
            continue
        if record.get("type") != "response_item":
            continue
        payload = record.get("payload", {})
        payload_type = payload.get("type")
        if payload_type == "function_call":
            name = payload.get("name", "")
            call_id = payload.get("call_id", "")
            if name in TOOLS and call_id:
                calls[call_id] = ToolCall(
                    line=line_number,
                    timestamp=record.get("timestamp", ""),
                    name=name,
                    call_id=call_id,
                    arguments=parse_call_arguments(payload.get("arguments")),
                )
            continue
        if payload_type != "function_call_output":
            continue
        call = calls.get(payload.get("call_id", ""))
        if call is None:
            continue
        image = extract_image(payload.get("output"))
        if image is None:
            continue
        events.append(EventFrame(len(events) + 1, line_number, record.get("timestamp", ""), call, image))
    return events


def font(size: int) -> ImageFont.ImageFont:
    for path in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/Library/Fonts/Arial.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def action_label(event: EventFrame) -> str:
    args = event.call.arguments
    parts = []
    for key in ("app", "element_index", "click_count", "mouse_button", "x", "y", "direction", "pages", "key", "text"):
        if key in args:
            value = args[key]
            if key == "text" and isinstance(value, str) and len(value) > 48:
                value = value[:45] + "..."
            parts.append(f"{key}={value!r}")
    return f"{event.index}. {event.call.name}({', '.join(parts)})" if parts else f"{event.index}. {event.call.name}()"


def even(value: int) -> int:
    return value if value % 2 == 0 else value + 1


def draw_marker(draw: ImageDraw.ImageDraw, x: float, y: float, offset_x: int, offset_y: int) -> None:
    cx = int(offset_x + x)
    cy = int(offset_y + y)
    draw.ellipse((cx - 18, cy - 18, cx + 18, cy + 18), outline=(255, 59, 48), width=5)
    draw.line((cx - 26, cy, cx + 26, cy), fill=(255, 59, 48), width=3)
    draw.line((cx, cy - 26, cx, cy + 26), fill=(255, 59, 48), width=3)


def render_frame(event: EventFrame, canvas_size: tuple[int, int], banner_height: int) -> Image.Image:
    canvas_width, canvas_height = canvas_size
    screenshot_height = canvas_height - banner_height
    canvas = Image.new("RGB", canvas_size, (18, 18, 20))
    offset_x = (canvas_width - event.image.width) // 2
    offset_y = (screenshot_height - event.image.height) // 2
    canvas.paste(event.image, (offset_x, offset_y))
    draw = ImageDraw.Draw(canvas)

    args = event.call.arguments
    if "x" in args and "y" in args:
        draw_marker(draw, float(args["x"]), float(args["y"]), offset_x, offset_y)

    banner_top = canvas_height - banner_height
    draw.rectangle((0, banner_top, canvas_width, canvas_height), fill=(248, 248, 246))
    wrapped = textwrap.wrap(action_label(event), width=max(36, canvas_width // 15))
    draw.text((24, banner_top + 14), wrapped[0], fill=(24, 24, 26), font=font(24))
    if len(wrapped) > 1:
        draw.text((24, banner_top + 44), wrapped[1], fill=(24, 24, 26), font=font(16))
    meta = f"{event.timestamp} | call line {event.call.line} -> output line {event.line}"
    draw.text((24, canvas_height - 26), meta, fill=(92, 92, 96), font=font(16))
    return canvas


def write_frames(events: list[EventFrame], frames_dir: Path, duration: float) -> tuple[list[Path], Path]:
    max_width = max(event.image.width for event in events)
    max_height = max(event.image.height for event in events)
    banner_height = 104
    canvas_size = (even(max_width), even(max_height + banner_height))
    frames_dir.mkdir(parents=True, exist_ok=True)
    frame_paths: list[Path] = []
    for event in events:
        frame_path = frames_dir / f"frame-{event.index:03d}-{event.call.name}.png"
        render_frame(event, canvas_size, banner_height).save(frame_path)
        frame_paths.append(frame_path)
    concat_path = frames_dir / "frames.txt"
    lines = []
    for frame_path in frame_paths:
        lines.append(f"file '{frame_path.resolve()}'")
        lines.append(f"duration {duration}")
    concat_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return frame_paths, concat_path


def run_ffmpeg(concat_path: Path, output_mp4: Path, fps: int) -> None:
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-vf",
        f"fps={fps},format=yuv420p",
        "-movflags",
        "+faststart",
        str(output_mp4),
    ]
    subprocess.run(command, check=True)


def main() -> int:
    args = parse_args()
    if args.duration <= 0 or args.fps <= 0:
        print("error: --duration and --fps must be positive", file=sys.stderr)
        return 2

    run_id = safe_run_id(args.run_id)
    output_dir = args.output_dir or Path.cwd() / "qa-recordings" / run_id
    output_dir.mkdir(parents=True, exist_ok=True)

    source_jsonl = args.session_jsonl or find_current_session(Path(args.codex_home))
    session_jsonl = copy_session(source_jsonl.expanduser(), output_dir)

    start_line = end_line = None
    if not args.include_all:
        if not args.run_id:
            print("error: pass --run-id or use --include-all", file=sys.stderr)
            return 2
        start_line, end_line = marker_lines(session_jsonl, run_id)
        if start_line is None:
            print(f"error: missing {START_PREFIX} {run_id}", file=sys.stderr)
            return 1

    events = load_events(session_jsonl, start_line, end_line)
    if not events:
        print("error: no Computer Use screenshots found in selected range", file=sys.stderr)
        return 1

    frames_dir = output_dir / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frame_paths, concat_path = write_frames(events, frames_dir, args.duration)
    output_mp4 = output_dir / f"{run_id}.mp4"
    run_ffmpeg(concat_path, output_mp4, args.fps)

    manifest_path = output_dir / f"{run_id}.manifest.json"
    manifest = {
        "run_id": run_id,
        "session_jsonl": str(session_jsonl),
        "video": str(output_mp4),
        "duration": args.duration,
        "fps": args.fps,
        "start_line": start_line,
        "end_line": end_line,
        "events": [
            {
                "index": event.index,
                "timestamp": event.timestamp,
                "tool": event.call.name,
                "call_id": event.call.call_id,
                "arguments": event.call.arguments,
                "frame": str(frame_paths[event.index - 1]),
            }
            for event in events
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    if not args.keep_frames:
        for frame_path in frame_paths:
            frame_path.unlink()
        concat_path.unlink()

    print(f"video={output_mp4}")
    print(f"manifest={manifest_path}")
    print(f"session_jsonl={session_jsonl}")
    print(f"frames={len(events)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
