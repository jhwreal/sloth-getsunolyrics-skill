#!/usr/bin/env python3
"""Validate media hashes, start-only cues, and exports in a lyric package."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import re

from extract_timeline import parse_lyrics
from media_utils import probe_media


CSV_TIME_RE = re.compile(r"^(\d{2,}):(\d{2})\.(\d{3})$")
LRC_RE = re.compile(r"^\[(\d+):(\d{2})\.(\d{2})\](.*)$")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_csv_time(value: str) -> int:
    match = CSV_TIME_RE.fullmatch(value)
    if not match:
        raise ValueError(f"invalid CSV time: {value}")
    minutes, seconds, milliseconds = map(int, match.groups())
    if seconds >= 60:
        raise ValueError(f"invalid CSV seconds: {value}")
    return minutes * 60_000 + seconds * 1000 + milliseconds


def parse_lrc(path: Path) -> list[dict]:
    cues = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line:
            continue
        match = LRC_RE.fullmatch(line)
        if not match:
            raise ValueError(f"invalid LRC line: {line}")
        minutes, seconds, centiseconds, text = match.groups()
        if int(seconds) >= 60:
            raise ValueError(f"invalid LRC seconds: {line}")
        cues.append(
            {
                "start_ms": int(minutes) * 60_000 + int(seconds) * 1000 + int(centiseconds) * 10,
                "text": text,
            }
        )
    return cues


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.package_dir.resolve()
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    timeline = json.loads((root / manifest["timeline"]).read_text(encoding="utf-8"))
    video = root / manifest["video"]
    vocals = root / manifest["vocals"]
    lyrics = root / manifest["lyrics"]

    checks: dict[str, bool] = {}
    checks["video_sha256"] = sha256(video) == manifest["video_sha256"]
    checks["vocals_sha256"] = sha256(vocals) == manifest["vocals_sha256"]
    checks["lyrics_sha256"] = sha256(lyrics) == manifest["lyrics_sha256"]
    video_info = probe_media(video)
    vocal_info = probe_media(vocals)
    video_duration = int(video_info["duration_ms"])
    vocal_duration = int(vocal_info["duration_ms"])
    checks["media_streams"] = all(
        [
            video_info["has_video"],
            video_info["has_audio"],
            not vocal_info["has_video"],
            vocal_info["has_audio"],
        ]
    )
    checks["media_durations_aligned"] = abs(video_duration - vocal_duration) <= max(
        500, round(video_duration * 0.005)
    )
    cues = timeline.get("cues") or []
    checks["cue_count"] = bool(cues) and len(cues) == int(manifest["cue_count"])
    checks["cue_starts"] = all(
        0 <= int(cue["start_ms"]) < int(timeline["media_duration_ms"])
        for cue in cues
    )
    checks["start_only_schema"] = all("end_ms" not in cue for cue in cues)
    checks["cue_order"] = all(
        int(cues[index]["start_ms"]) < int(cues[index + 1]["start_ms"])
        for index in range(len(cues) - 1)
    )
    checks["cue_indexes"] = all(
        int(cue.get("index", -1)) == index for index, cue in enumerate(cues, 1)
    )
    checks["cue_text"] = all(isinstance(cue.get("text"), str) and cue["text"] for cue in cues)
    checks["cue_confidence"] = all(
        isinstance(cue.get("confidence"), (int, float))
        and 0.0 <= float(cue["confidence"]) <= 1.0
        for cue in cues
    )
    checks["text_sources"] = all(
        cue.get("text_source")
        in {
            "suno_lyrics_confirmed_by_video",
            "suno_lyrics_interpolated_from_video",
            "suno_lyrics_conflict_overridden",
        }
        for cue in cues
    )
    checks["timing_sources"] = all(isinstance(cue.get("timing_source"), str) for cue in cues)
    canonical_lyrics = parse_lyrics(lyrics)
    checks["timeline_text_matches_lyrics"] = [cue.get("text") for cue in cues] == [
        cue["text"] for cue in canonical_lyrics
    ]
    checks["timeline_inputs_match_manifest"] = all(
        [
            timeline.get("video_sha256") == manifest.get("video_sha256"),
            timeline.get("vocals_sha256") == manifest.get("vocals_sha256"),
            timeline.get("lyrics_sha256") == manifest.get("lyrics_sha256"),
            timeline.get("pipeline_fingerprint") == manifest.get("pipeline_fingerprint"),
            timeline.get("media_duration_ms") == manifest.get("media_duration_ms"),
            timeline.get("video") == manifest.get("video"),
            timeline.get("vocals") == manifest.get("vocals"),
            timeline.get("lyrics") == manifest.get("lyrics"),
            timeline.get("ocr_language") == manifest.get("language"),
            timeline.get("ocr_interval_ms") == manifest.get("ocr_interval_ms"),
            timeline.get("lyrics_comparison") == manifest.get("lyrics_comparison"),
            timeline.get("alignment_summary") == manifest.get("alignment_summary"),
            timeline.get("whisper_alignment") == manifest.get("whisper_alignment"),
        ]
    )
    comparison_metadata = timeline.get("lyrics_comparison") or {}
    comparison_json = root / str(comparison_metadata.get("report_json", ""))
    comparison_markdown = root / str(comparison_metadata.get("report_markdown", ""))
    try:
        comparison = json.loads(comparison_json.read_text(encoding="utf-8"))
        comparison_markdown_text = comparison_markdown.read_text(encoding="utf-8")
    except (OSError, json.JSONDecodeError):
        comparison = {}
        comparison_markdown_text = ""
    checks["lyrics_comparison_reports"] = all(
        [
            comparison_json.is_file(),
            comparison_markdown.is_file(),
            bool(comparison_markdown_text.strip()),
            comparison.get("lyrics_sha256") == manifest.get("lyrics_sha256"),
            comparison.get("video_sha256") == manifest.get("video_sha256"),
            comparison.get("status") == comparison_metadata.get("status"),
            comparison.get("detected_status")
            == comparison_metadata.get("detected_status"),
            comparison.get("requested_resolution")
            == comparison_metadata.get("requested_resolution"),
            comparison.get("decision_pending")
            == comparison_metadata.get("decision_pending"),
            comparison.get("difference_count")
            == comparison_metadata.get("difference_count"),
            comparison.get("uncertain_item_count")
            == comparison_metadata.get("uncertain_item_count"),
            not comparison.get("decision_pending", False),
            not comparison_metadata.get("decision_pending", False),
        ]
    )
    summary = timeline.get("alignment_summary") or {}
    confirmed_count = sum(
        cue.get("text_source") == "suno_lyrics_confirmed_by_video" for cue in cues
    )
    interpolated_count = sum(
        cue.get("text_source") == "suno_lyrics_interpolated_from_video" for cue in cues
    )
    conflict_overridden_count = sum(
        cue.get("text_source") == "suno_lyrics_conflict_overridden" for cue in cues
    )
    checks["alignment_summary"] = all(
        [
            summary.get("lyrics_count") == len(cues),
            summary.get("confirmed_count") == confirmed_count,
            summary.get("interpolated_count") == interpolated_count,
            summary.get("conflict_overridden_count") == conflict_overridden_count,
            summary.get("whisper_match_count") == len(cues),
            int(summary.get("dtw_token_start_count", -1))
            + int(summary.get("rejected_dtw_backtrack_count", -1))
            == len(cues),
            confirmed_count + interpolated_count + conflict_overridden_count == len(cues),
            summary.get("confirmed_ratio") == round(confirmed_count / len(cues), 4)
            if cues
            else False,
        ]
    )
    checks["manifest_exports"] = manifest.get("exports") == [
        "timeline.csv",
        "timeline.lrc",
    ]
    checks["no_interval_exports"] = not (root / "timeline.srt").exists() and not (
        root / "timeline.vtt"
    ).exists()
    checks["timeline_duration_matches_media"] = int(timeline["media_duration_ms"]) == min(
        video_duration, vocal_duration
    )
    try:
        lrc_rows = parse_lrc(root / "timeline.lrc")
    except (OSError, ValueError):
        lrc_rows = []
    lrc_count = len(lrc_rows)
    try:
        with (root / "timeline.csv").open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            csv_rows = list(reader)
            csv_fields = reader.fieldnames
    except (OSError, csv.Error):
        csv_rows = []
        csv_fields = None
    csv_count = len(csv_rows)
    checks["csv_columns"] = csv_fields == [
        "id",
        "section",
        "start_time",
        "lyric",
    ]
    checks["export_cue_counts"] = lrc_count == csv_count == len(cues)
    try:
        checks["csv_matches_json"] = all(
            [
                row.get("id") == f"lyric-{index:02d}",
                row.get("section") == (cue.get("section") or ""),
                row.get("lyric") == str(cue["text"]),
                parse_csv_time(row.get("start_time", "")) == int(cue["start_ms"]),
            ]
            for index, (row, cue) in enumerate(zip(csv_rows, cues), 1)
        )
    except ValueError:
        checks["csv_matches_json"] = False
    checks["lrc_matches_json"] = all(
        row["text"] == str(cue["text"])
        and row["start_ms"] == (int(cue["start_ms"]) // 10) * 10
        for row, cue in zip(lrc_rows, cues)
    ) and len(lrc_rows) == len(cues)
    report = {
        "valid": all(checks.values()),
        "checks": checks,
        "video_duration_ms": video_duration,
        "vocal_duration_ms": vocal_duration,
        "cue_count": len(cues),
        "export_cue_counts": {
            "csv": csv_count,
            "lrc": lrc_count,
        },
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_name(f".{args.output.name}.tmp")
        temporary.write_text(rendered, encoding="utf-8")
        temporary.replace(args.output)
    print(rendered, end="")
    if not report["valid"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
