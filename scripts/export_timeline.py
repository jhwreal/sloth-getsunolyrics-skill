#!/usr/bin/env python3
"""Export a start-only lyric timeline to Netease-style CSV and LRC."""

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
        if not 0 <= start < duration:
            raise SystemExit(f"invalid cue start at index {cue.get('index')}: {start}")
        if start <= previous:
            raise SystemExit(f"cue starts are not strictly increasing at index {cue.get('index')}")
        previous = start
    return cues


def export_timeline(payload: dict, output_dir: Path, *, basename: str = "timeline") -> list[Path]:
    """Write the canonical start-only exports and return their paths."""
    cues = validate(payload)
    output_dir.mkdir(parents=True, exist_ok=True)

    lrc = "\n".join(f"[{lrc_time(int(cue['start_ms']))}]{cue['text']}" for cue in cues) + "\n"
    lrc_path = output_dir / f"{basename}.lrc"
    write_text_atomic(lrc_path, lrc)
    for stale_suffix in [".srt", ".vtt"]:
        (output_dir / f"{basename}{stale_suffix}").unlink(missing_ok=True)

    csv_stream = io.StringIO(newline="")
    writer = csv.writer(csv_stream)
    writer.writerow(["id", "section", "start_time", "lyric"])
    for number, cue in enumerate(cues, 1):
        writer.writerow(
            [
                f"lyric-{number:02d}",
                cue.get("section") or "",
                csv_time(int(cue["start_ms"])),
                cue["text"],
            ]
        )
    csv_path = output_dir / f"{basename}.csv"
    write_text_atomic(csv_path, csv_stream.getvalue(), encoding="utf-8-sig")
    return [csv_path, lrc_path]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="timeline JSON")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--basename", default="timeline")
    args = parser.parse_args()
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    for path in export_timeline(payload, args.output_dir, basename=args.basename):
        print(path)


if __name__ == "__main__":
    main()
