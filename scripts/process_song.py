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


ROOT = Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


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
    args = parser.parse_args()
    if not args.video.is_file():
        raise SystemExit(f"video does not exist: {args.video}")
    if not args.vocals.is_file():
        raise SystemExit(f"vocals do not exist: {args.vocals}")
    if not args.lyrics.is_file():
        raise SystemExit(f"lyrics do not exist: {args.lyrics}")

    output = args.output_dir.resolve()
    work = output / "work"
    output.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    packaged_video = output / "song.mp4"
    if args.video.resolve() != packaged_video.resolve():
        shutil.copy2(args.video, packaged_video)
    packaged_vocals = output / "vocals.wav"
    packaged_lyrics = output / "lyrics.txt"
    if args.lyrics.resolve() != packaged_lyrics.resolve():
        shutil.copy2(args.lyrics, packaged_lyrics)

    separation_metadata = packaged_vocals.with_suffix(".separation.json")
    resumed_vocals = args.resume and packaged_vocals.is_file() and separation_metadata.is_file()
    if resumed_vocals:
        separation = json.loads(separation_metadata.read_text())
        recorded_hash = separation.get("vocals_sha256")
        if recorded_hash and recorded_hash != sha256(packaged_vocals):
            raise SystemExit("existing vocals.wav does not match vocals.separation.json")
        if sha256(args.vocals) != sha256(packaged_vocals):
            resumed_vocals = False
        else:
            print(f"Reusing separated vocals at {packaged_vocals}")
    if not resumed_vocals:
        if args.vocals.resolve() != packaged_vocals.resolve():
            shutil.copy2(args.vocals, packaged_vocals)
        separation = {
            "schema_version": 1,
            "backend": args.vocals_source,
            "source": str(args.vocals.resolve()),
            "vocals": str(packaged_vocals),
            "vocals_sha256": sha256(packaged_vocals),
        }
        separation_metadata.write_text(
            json.dumps(separation, ensure_ascii=False, indent=2) + "\n"
        )

    timeline = output / "timeline.json"
    lyrics_hash = sha256(packaged_lyrics)
    reusable_timeline = False
    if args.resume and timeline.is_file():
        existing_timeline = json.loads(timeline.read_text())
        reusable_timeline = all(
            [
                existing_timeline.get("lyrics_sha256") == lyrics_hash,
                existing_timeline.get("video_sha256") == sha256(packaged_video),
                existing_timeline.get("vocals_sha256") == sha256(packaged_vocals),
            ]
        )
    if reusable_timeline:
        print(f"Reusing timeline at {timeline}")
    else:
        run(
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
            ]
        )
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
    timeline_payload = json.loads(timeline.read_text())
    manifest = {
        "schema_version": 2,
        "title": args.title,
        "source_url": args.source_url,
        "video": "song.mp4",
        "video_sha256": sha256(packaged_video),
        "vocals": "vocals.wav",
        "vocals_sha256": sha256(packaged_vocals),
        "lyrics": "lyrics.txt",
        "lyrics_sha256": lyrics_hash,
        "timeline": "timeline.json",
        "exports": ["timeline.csv", "timeline.lrc", "timeline.srt", "timeline.vtt"],
        "media_duration_ms": timeline_payload["media_duration_ms"],
        "cue_count": len(timeline_payload["cues"]),
        "language": args.language,
        "ocr_interval_ms": timeline_payload["ocr_interval_ms"],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
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
    print(f"Built lyric package at {output}")


if __name__ == "__main__":
    main()
