#!/usr/bin/env python3
"""Build a lyric package from Suno lyrics, lyric MP4, and aligned vocals."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from extract_timeline import pipeline_fingerprint
from media_utils import validate_media_pair


ROOT = Path(__file__).resolve().parents[1]
GENERATED_DELIVERABLES = [
    "timeline.json",
    "timeline.csv",
    "timeline.lrc",
    "timeline.srt",
    "timeline.vtt",
    "manifest.json",
    "validation.json",
]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def timeline_is_reusable(
    payload: dict,
    *,
    pipeline_hash: str,
    lyrics_hash: str,
    video_hash: str,
    vocals_hash: str,
    language: str,
    interval_ms: int,
    lyrics_conflict_resolution: str,
) -> bool:
    return all(
        [
            payload.get("pipeline_fingerprint") == pipeline_hash,
            payload.get("lyrics_sha256") == lyrics_hash,
            payload.get("video_sha256") == video_hash,
            payload.get("vocals_sha256") == vocals_hash,
            payload.get("ocr_language") == language,
            payload.get("ocr_interval_ms") == interval_ms,
            (payload.get("lyrics_comparison") or {}).get("requested_resolution")
            == lyrics_conflict_resolution,
        ]
    )


def copy_verified(source: Path, destination: Path, expected_hash: str) -> None:
    if source == destination.resolve():
        return
    if destination.is_file() and sha256(destination) == expected_hash:
        print(f"Reusing unchanged packaged input at {destination}", flush=True)
        return
    shutil.copy2(source, destination)
    if sha256(destination) != expected_hash:
        raise SystemExit(f"copied file failed hash verification: {destination}")


def remove_stale_deliverables(output: Path) -> None:
    """Prevent an older successful package from looking valid after a conflict pause."""
    for filename in GENERATED_DELIVERABLES:
        (output / filename).unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True, help="downloaded lyric MP4")
    parser.add_argument("--lyrics", type=Path, required=True, help="untimed lyrics copied from Suno")
    parser.add_argument("--vocals", type=Path, required=True, help="Suno lead-vocal stem")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto")
    parser.add_argument("--interval", type=float, default=0.5)
    parser.add_argument("--title")
    parser.add_argument("--source-url")
    parser.add_argument(
        "--vocals-source",
        choices=["suno_stem_download", "provided_aligned_vocals"],
        default="suno_stem_download",
    )
    parser.add_argument("--resume", action="store_true", help="reuse valid existing stage outputs")
    parser.add_argument(
        "--lyrics-conflict-resolution",
        choices=["ask", "verified-ocr-error", "use-copied"],
        default="ask",
        help="default ask pauses on a visually verifiable MP4/copied-lyrics difference",
    )
    args = parser.parse_args()
    if not args.video.is_file():
        raise SystemExit(f"video does not exist: {args.video}")
    if not args.vocals.is_file():
        raise SystemExit(f"vocals do not exist: {args.vocals}")
    if not args.lyrics.is_file():
        raise SystemExit(f"lyrics do not exist: {args.lyrics}")
    if args.interval < 0.25 or args.interval > 1.0:
        raise SystemExit("--interval must be between 0.25 and 1.0 seconds")

    video_source = args.video.resolve()
    vocal_source = args.vocals.resolve()
    lyrics_source = args.lyrics.resolve()
    print("Preflight: validating media streams and durations", flush=True)
    validate_media_pair(video_source, vocal_source)
    video_hash = sha256(video_source)
    vocals_hash = sha256(vocal_source)
    lyrics_hash = sha256(lyrics_source)

    output = args.output_dir.resolve()
    work = output / "work"
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    packaged_video = output / "song.mp4"
    copy_verified(video_source, packaged_video, video_hash)
    packaged_vocals = output / "vocals.wav"
    packaged_lyrics = output / "lyrics.txt"
    copy_verified(lyrics_source, packaged_lyrics, lyrics_hash)

    separation_metadata = packaged_vocals.with_suffix(".separation.json")
    resumed_vocals = args.resume and packaged_vocals.is_file() and separation_metadata.is_file()
    if resumed_vocals:
        separation = read_json(separation_metadata) or {}
        recorded_hash = separation.get("vocals_sha256")
        if recorded_hash and recorded_hash != sha256(packaged_vocals):
            raise SystemExit("existing vocals.wav does not match vocals.separation.json")
        if vocals_hash != sha256(packaged_vocals):
            resumed_vocals = False
        else:
            print(f"Reusing separated vocals at {packaged_vocals}", flush=True)
    if not resumed_vocals:
        copy_verified(vocal_source, packaged_vocals, vocals_hash)
    separation = {
        "schema_version": 2,
        "backend": args.vocals_source,
        "source_filename": vocal_source.name,
        "vocals": packaged_vocals.name,
        "vocals_sha256": vocals_hash,
    }
    write_json_atomic(separation_metadata, separation)

    timeline = output / "timeline.json"
    reusable_timeline = False
    if args.resume and timeline.is_file():
        existing_timeline = read_json(timeline)
        if existing_timeline:
            reusable_timeline = timeline_is_reusable(
                existing_timeline,
                pipeline_hash=pipeline_fingerprint(),
                lyrics_hash=lyrics_hash,
                video_hash=video_hash,
                vocals_hash=vocals_hash,
                language=args.language,
                interval_ms=round(args.interval * 1000),
                lyrics_conflict_resolution=args.lyrics_conflict_resolution,
            )
    if reusable_timeline:
        print(f"Reusing timeline at {timeline}", flush=True)
    else:
        extraction = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "extract_timeline.py"),
                "--video",
                str(packaged_video),
                "--vocals",
                str(packaged_vocals),
                "--lyrics",
                str(packaged_lyrics),
                "--language",
                args.language,
                "--interval",
                str(args.interval),
                "--work-dir",
                str(work / "timeline"),
                "--output",
                str(timeline),
                "--lyrics-conflict-resolution",
                args.lyrics_conflict_resolution,
                "--lyrics-comparison-json",
                str(output / "lyrics-comparison.json"),
                "--lyrics-comparison-markdown",
                str(output / "lyrics-comparison.md"),
            ],
            check=False,
        )
        if extraction.returncode == 3:
            remove_stale_deliverables(output)
            print(
                "Lyrics differ between the copied Suno panel and the MP4. "
                "No final timeline was produced. Verify lyrics-comparison.md against "
                "the video, then ask the user how to proceed.",
                file=sys.stderr,
            )
            raise SystemExit(3)
        extraction.check_returncode()
    run(
        [
            sys.executable,
            str(ROOT / "scripts" / "export_timeline.py"),
            "--input",
            str(timeline),
            "--output-dir",
            str(output),
        ]
    )
    timeline_payload = json.loads(timeline.read_text(encoding="utf-8"))
    manifest = {
        "schema_version": 4,
        "pipeline_fingerprint": timeline_payload["pipeline_fingerprint"],
        "title": args.title,
        "source_url": args.source_url,
        "video": "song.mp4",
        "video_sha256": video_hash,
        "vocals": "vocals.wav",
        "vocals_sha256": vocals_hash,
        "lyrics": "lyrics.txt",
        "lyrics_sha256": lyrics_hash,
        "timeline": "timeline.json",
        "exports": ["timeline.csv", "timeline.lrc", "timeline.srt", "timeline.vtt"],
        "media_duration_ms": timeline_payload["media_duration_ms"],
        "cue_count": len(timeline_payload["cues"]),
        "language": args.language,
        "ocr_interval_ms": timeline_payload["ocr_interval_ms"],
        "lyrics_comparison": timeline_payload["lyrics_comparison"],
        "alignment_summary": timeline_payload["alignment_summary"],
    }
    write_json_atomic(output / "manifest.json", manifest)
    run(
        [
            sys.executable,
            str(ROOT / "scripts" / "validate_package.py"),
            "--package-dir",
            str(output),
            "--output",
            str(output / "validation.json"),
        ]
    )
    print(f"Built lyric package at {output}", flush=True)


if __name__ == "__main__":
    main()
