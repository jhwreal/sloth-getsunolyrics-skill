#!/usr/bin/env python3
"""Export a Sloth lyric timeline JSON to CSV, LRC, SRT, and WebVTT."""

from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path


def write_text_atomic(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding=encoding)
    temporary.replace(path)


def lrc_time(milliseconds: int) -> str:
    minutes, remainder = divmod(max(0, milliseconds), 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis // 10:02d}"


def subtitle_time(milliseconds: int, separator: str) -> str:
    hours, remainder = divmod(max(0, milliseconds), 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{millis:03d}"


def csv_time(milliseconds: int) -> str:
    minutes, remainder = divmod(max(0, milliseconds), 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def validate(payload: dict) -> list[dict]:
    duration = int(payload["media_duration_ms"])
    cues = payload.get("cues") or []
    if not cues:
        raise SystemExit("timeline contains no cues")
    previous = -1
    for cue in cues:
        start = int(cue["start_ms"])
        end = int(cue["end_ms"])
        if not 0 <= start < end <= duration:
            raise SystemExit(f"invalid cue interval at index {cue.get('index')}: {start}..{end}")
        if start <= previous:
            raise SystemExit(f"cue starts are not strictly increasing at index {cue.get('index')}")
        previous = start
    return cues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="timeline JSON")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--basename", default="timeline")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    cues = validate(payload)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    lrc = "\n".join(f"[{lrc_time(int(cue['start_ms']))}]{cue['text']}" for cue in cues) + "\n"
    srt_blocks = []
    vtt_blocks = ["WEBVTT", ""]
    for number, cue in enumerate(cues, 1):
        start = int(cue["start_ms"])
        end = int(cue["end_ms"])
        text = str(cue["text"])
        srt_blocks.append(
            f"{number}\n{subtitle_time(start, ',')} --> {subtitle_time(end, ',')}\n{text}\n"
        )
        vtt_blocks.append(
            f"{number}\n{subtitle_time(start, '.')} --> {subtitle_time(end, '.')}\n{text}\n"
        )

    outputs = {
        ".lrc": lrc,
        ".srt": "\n".join(srt_blocks),
        ".vtt": "\n".join(vtt_blocks),
    }
    for suffix, content in outputs.items():
        path = args.output_dir / f"{args.basename}{suffix}"
        write_text_atomic(path, content)
        print(path)

    csv_stream = io.StringIO(newline="")
    writer = csv.writer(csv_stream)
    writer.writerow(["id", "section", "start_time", "end_time", "lyric"])
    for number, cue in enumerate(cues, 1):
        writer.writerow(
            [
                f"lyric-{number:02d}",
                cue.get("section") or "",
                csv_time(int(cue["start_ms"])),
                csv_time(int(cue["end_ms"])),
                cue["text"],
            ]
        )
    csv_path = args.output_dir / f"{args.basename}.csv"
    write_text_atomic(csv_path, csv_stream.getvalue(), encoding="utf-8-sig")
    print(csv_path)


if __name__ == "__main__":
    main()
