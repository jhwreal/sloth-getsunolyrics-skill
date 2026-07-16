#!/usr/bin/env python3
"""Shared FFprobe helpers for lyric-video and vocal-stem validation."""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess


def require_command(name: str) -> str:
    command = shutil.which(name)
    if not command:
        raise SystemExit(f"missing required command: {name}")
    return command


def probe_media(path: Path) -> dict:
    result = subprocess.run(
        [
            require_command("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration,format_name:stream=index,codec_type,codec_name,sample_rate,channels,r_frame_rate,avg_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    try:
        payload = json.loads(result.stdout)
        duration_ms = round(float(payload["format"]["duration"]) * 1000)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"could not read media duration from {path.name}: {error}") from error
    if duration_ms <= 0:
        raise SystemExit(f"media has no positive duration: {path.name}")
    streams = payload.get("streams") or []
    return {
        "duration_ms": duration_ms,
        "format_name": payload.get("format", {}).get("format_name"),
        "streams": streams,
        "has_video": any(stream.get("codec_type") == "video" for stream in streams),
        "has_audio": any(stream.get("codec_type") == "audio" for stream in streams),
    }


def validate_media_pair(video: Path, vocals: Path) -> tuple[dict, dict]:
    video_info = probe_media(video)
    vocal_info = probe_media(vocals)
    if not video_info["has_video"]:
        raise SystemExit(f"lyric video contains no video stream: {video.name}")
    if not video_info["has_audio"]:
        raise SystemExit(f"lyric video contains no audio stream: {video.name}")
    if vocal_info["has_video"]:
        raise SystemExit(f"vocal stem unexpectedly contains a video stream: {vocals.name}")
    if not vocal_info["has_audio"]:
        raise SystemExit(f"vocal stem contains no audio stream: {vocals.name}")
    allowed_delta = max(500, round(video_info["duration_ms"] * 0.005))
    difference = abs(video_info["duration_ms"] - vocal_info["duration_ms"])
    if difference > allowed_delta:
        raise SystemExit(
            "video and vocal durations are not aligned: "
            f"{video_info['duration_ms']} ms vs {vocal_info['duration_ms']} ms "
            f"(allowed {allowed_delta} ms)"
        )
    return video_info, vocal_info
