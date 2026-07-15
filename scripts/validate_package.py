#!/usr/bin/env python3
"""Validate media hashes, durations, cues, and exports in a lyric package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess


CSV_TIME_RE = re.compile(r"^(\d{2,}):(\d{2})\.(\d{3})$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def duration_ms(path: Path) -> int:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise SystemExit("missing required command: ffprobe")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return round(float(result.stdout.strip()) * 1000)


def parse_csv_time(value: str) -> int:
    match = CSV_TIME_RE.fullmatch(value)
    if not match:
        raise ValueError(f"invalid CSV time: {value}")
    minutes, seconds, milliseconds = map(int, match.groups())
    if seconds >= 60:
        raise ValueError(f"invalid CSV seconds: {value}")
    return minutes * 60_000 + seconds * 1000 + milliseconds


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.package_dir.resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    timeline = json.loads((root / manifest["timeline"]).read_text())
    video = root / manifest["video"]
    vocals = root / manifest["vocals"]
    lyrics = root / manifest["lyrics"]

    checks: dict[str, bool] = {}
    checks["video_sha256"] = sha256(video) == manifest["video_sha256"]
    checks["vocals_sha256"] = sha256(vocals) == manifest["vocals_sha256"]
    checks["lyrics_sha256"] = sha256(lyrics) == manifest["lyrics_sha256"]
    video_duration = duration_ms(video)
    vocal_duration = duration_ms(vocals)
    checks["media_durations_aligned"] = abs(video_duration - vocal_duration) <= max(
        500, round(video_duration * 0.005)
    )
    cues = timeline.get("cues") or []
    checks["cue_count"] = bool(cues) and len(cues) == int(manifest["cue_count"])
    checks["cue_intervals"] = all(
        0 <= int(cue["start_ms"]) < int(cue["end_ms"]) <= int(timeline["media_duration_ms"])
        for cue in cues
    )
    checks["cue_order"] = all(
        int(cues[index]["start_ms"]) < int(cues[index + 1]["start_ms"])
        for index in range(len(cues) - 1)
    )
    lrc_count = sum(1 for line in (root / "timeline.lrc").read_text().splitlines() if line.startswith("["))
    srt_count = len(re.findall(r"(?m)^\d+\s*$", (root / "timeline.srt").read_text()))
    vtt_count = len(re.findall(r"(?m)^\d+\s*$", (root / "timeline.vtt").read_text()))
    with (root / "timeline.csv").open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        csv_rows = list(reader)
    csv_count = len(csv_rows)
    checks["csv_columns"] = reader.fieldnames == [
        "id",
        "section",
        "start_time",
        "end_time",
        "lyric",
    ]
    checks["export_cue_counts"] = lrc_count == srt_count == vtt_count == csv_count == len(cues)
    try:
        checks["csv_matches_json"] = all(
            [
                row.get("id") == f"lyric-{index:02d}",
                row.get("section") == (cue.get("section") or ""),
                row.get("lyric") == str(cue["text"]),
                parse_csv_time(row.get("start_time", "")) == int(cue["start_ms"]),
                parse_csv_time(row.get("end_time", "")) == int(cue["end_ms"]),
            ]
            for index, (row, cue) in enumerate(zip(csv_rows, cues), 1)
        )
    except ValueError:
        checks["csv_matches_json"] = False
    report = {
        "valid": all(checks.values()),
        "checks": checks,
        "video_duration_ms": video_duration,
        "vocal_duration_ms": vocal_duration,
        "cue_count": len(cues),
        "export_cue_counts": {
            "csv": csv_count,
            "lrc": lrc_count,
            "srt": srt_count,
            "vtt": vtt_count,
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    if not report["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
