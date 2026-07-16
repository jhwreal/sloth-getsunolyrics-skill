#!/usr/bin/env python3
"""Align supplied Suno lyrics using a lyric video and an isolated vocal stem."""

from __future__ import annotations

import argparse
import array
import difflib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import wave

from media_utils import require_command, validate_media_pair


SKILL_ROOT = Path(__file__).resolve().parents[1]
VISION_SOURCE = SKILL_ROOT / "scripts" / "vision_ocr.swift"
PIPELINE_SCHEMA_VERSION = 2
OCR_CACHE_VERSION = 2
SECTION_RE = re.compile(r"^[\[［（(]([^\]］）)]+)[\]］）)]\s*")
SECTION_ONLY_RE = re.compile(r"^[\[［（(]([^\]］）)]+)[\]］）)]\s*$")


def run(command: list[str], *, stdout=None) -> None:
    subprocess.run(command, check=True, stdout=stdout)


def write_text_atomic(path: Path, content: str, *, encoding: str = "utf-8") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding=encoding)
    temporary.replace(path)


def write_json_atomic(path: Path, payload: dict) -> None:
    write_text_atomic(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def pipeline_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in [Path(__file__).resolve(), VISION_SOURCE.resolve()]:
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def macos_sdk_candidates() -> list[Path | None]:
    """Return the default SDK followed by installed SDKs, newest first."""
    candidates: list[Path | None] = [None]
    result = subprocess.run(
        ["xcode-select", "-p"], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return candidates
    developer = Path(result.stdout.strip())
    roots = [
        developer / "SDKs",
        developer / "Platforms" / "MacOSX.platform" / "Developer" / "SDKs",
    ]
    discovered = []
    for root in roots:
        if root.is_dir():
            discovered.extend(path for path in root.glob("MacOSX*.sdk") if path.is_dir())

    def version_key(path: Path) -> tuple[int, ...]:
        numbers = re.findall(r"\d+", path.stem)
        return tuple(map(int, numbers)) if numbers else (0,)

    seen = set()
    for path in sorted(discovered, key=version_key, reverse=True):
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            candidates.append(path)
    return candidates


def compile_vision_scanner(work: Path) -> Path:
    swiftc = require_command("swiftc")
    binary = work / "vision-ocr"
    metadata_path = work / "vision-ocr.build.json"
    module_cache = work / "clang-module-cache"
    module_cache.mkdir(parents=True, exist_ok=True)
    version = subprocess.run(
        [swiftc, "--version"], check=True, capture_output=True, text=True
    ).stdout.strip()
    build_identity = {
        "vision_source_sha256": sha256(VISION_SOURCE),
        "swiftc_version": version,
    }
    existing_build = read_json(metadata_path) or {}
    if binary.is_file() and all(
        existing_build.get(key) == value for key, value in build_identity.items()
    ):
        return binary
    command_tail = [
        str(VISION_SOURCE),
        "-o",
        str(binary),
        "-framework",
        "Vision",
        "-framework",
        "CoreGraphics",
        "-framework",
        "ImageIO",
    ]
    env = os.environ.copy()
    env["CLANG_MODULE_CACHE_PATH"] = str(module_cache)
    failures = []
    for sdk in macos_sdk_candidates():
        binary.unlink(missing_ok=True)
        command = [swiftc]
        if sdk is not None:
            command += ["-sdk", str(sdk)]
        command += command_tail
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        if result.returncode == 0 and binary.is_file():
            write_json_atomic(
                metadata_path,
                {
                    **build_identity,
                    "sdk": sdk.name if sdk is not None else "default",
                },
            )
            return binary
        failures.append(
            f"{sdk.name if sdk is not None else 'default'}: "
            + (result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "failed")
        )
    raise SystemExit("could not compile Vision OCR scanner; " + " | ".join(failures))


def extract_ocr(video: Path, work: Path, interval: float, language: str) -> list[dict]:
    frames = work / "frames"
    output = work / "ocr.jsonl"
    cache_path = work / "ocr.cache.json"
    cache_key = {
        "cache_version": OCR_CACHE_VERSION,
        "video_sha256": sha256(video),
        "interval_ms": round(interval * 1000),
        "language": language,
        "vision_source_sha256": sha256(VISION_SOURCE),
    }
    cache_record = read_json(cache_path) or {}
    cache_identity_matches = all(
        cache_record.get(key) == value for key, value in cache_key.items()
    )
    if output.is_file() and cache_identity_matches:
        try:
            cached = [
                json.loads(line)
                for line in output.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
        except json.JSONDecodeError:
            cached = []
        cache_content_matches = all(
            [
                cache_record.get("frame_count") == len(cached),
                cache_record.get("ocr_sha256") == sha256(output),
            ]
        )
        if cached and cache_content_matches and any(item.get("observations") for item in cached):
            print(f"      reusing {len(cached)} cached OCR frames", file=sys.stderr)
            return cached

    # A reused work directory may contain frames from a different or longer video.
    # Remove the complete directory before fresh extraction to prevent contamination.
    if frames.exists():
        shutil.rmtree(frames)
    frames.mkdir(parents=True, exist_ok=True)
    fps = 1.0 / interval
    run(
        [
            require_command("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(video),
            "-vf",
            f"fps={fps:.8f}",
            "-q:v",
            "3",
            "-start_number",
            "0",
            str(frames / "%08d.jpg"),
            "-y",
        ]
    )
    scanner = compile_vision_scanner(work)
    temporary_output = work / ".ocr.jsonl.tmp"
    languages = "zh-Hans,en-US" if language in {"auto", "zh"} else "en-US"
    scale = round(interval * 1000)
    with temporary_output.open("wb") as stream:
        run(
            [
                str(scanner),
                "--frames-dir",
                str(frames),
                "--languages",
                languages,
                "--filename-scale",
                str(scale),
            ],
            stdout=stream,
        )
    results = [
        json.loads(line)
        for line in temporary_output.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not results or not any(item.get("observations") for item in results):
        temporary_output.unlink(missing_ok=True)
        raise SystemExit(
            "OCR returned no text. On macOS, run this command outside an application sandbox "
            "or allow access to the Vision framework."
        )
    temporary_output.replace(output)
    write_json_atomic(
        cache_path,
        {
            **cache_key,
            "frame_count": len(results),
            "ocr_sha256": sha256(output),
        },
    )
    return results


def normalize_text(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"^[\[（(][^\]）)]+[\]）)]\s*", "", text)
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_lyrics(path: Path) -> list[dict]:
    """Parse Suno lyrics while preserving line text, order, and section labels."""
    current_section = None
    lyrics = []
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        section_only = SECTION_ONLY_RE.match(line)
        if section_only:
            current_section = section_only.group(1).strip()
            continue
        prefixed = SECTION_RE.match(line)
        if prefixed:
            current_section = prefixed.group(1).strip()
            line = line[prefixed.end() :].strip()
            if not line:
                continue
        lyrics.append(
            {
                "lyrics_index": len(lyrics) + 1,
                "text": line,
                "section": current_section,
            }
        )
    if not lyrics:
        raise SystemExit(f"lyrics file contains no lyric lines: {path}")
    return lyrics


def text_similarity(left: str, right: str) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def filter_video_cues_for_lyrics(lyrics: list[dict], video_cues: list[dict]) -> list[dict]:
    """Drop titles and decorations using canonical lyrics instead of song-specific rules."""
    lyric_texts = [cue["text"] for cue in lyrics]
    relevant = [
        cue
        for cue in video_cues
        if max((text_similarity(cue["text"], text) for text in lyric_texts), default=0.0) >= 0.5
    ]
    if not relevant:
        raise SystemExit("none of the video OCR anchors resemble the supplied lyrics")
    return relevant


def estimate_line_spacing_ms(video_cues: list[dict], interval_ms: int) -> int:
    deltas = [
        int(right["video_start_ms"]) - int(left["video_start_ms"])
        for left, right in zip(video_cues, video_cues[1:])
        if interval_ms <= int(right["video_start_ms"]) - int(left["video_start_ms"]) <= 15_000
    ]
    if not deltas:
        return max(2_000, interval_ms * 4)
    return max(interval_ms, round(statistics.median(deltas)))


def align_lyrics_to_video(
    lyrics: list[dict],
    video_cues: list[dict],
    interval_ms: int,
    *,
    duration_ms: int | None = None,
) -> list[dict]:
    """Order-align canonical lyric lines to noisy OCR cues without changing lyric text."""
    video_cues = filter_video_cues_for_lyrics(lyrics, video_cues)
    lyric_count = len(lyrics)
    video_count = len(video_cues)
    gap_score = -0.55
    scores = [[0.0] * (video_count + 1) for _ in range(lyric_count + 1)]
    moves = [[""] * (video_count + 1) for _ in range(lyric_count + 1)]
    for i in range(1, lyric_count + 1):
        scores[i][0] = i * gap_score
        moves[i][0] = "lyric"
    for j in range(1, video_count + 1):
        scores[0][j] = j * gap_score
        moves[0][j] = "video"
    for i in range(1, lyric_count + 1):
        for j in range(1, video_count + 1):
            similarity = text_similarity(lyrics[i - 1]["text"], video_cues[j - 1]["text"])
            options = {
                # A tiny deterministic position penalty resolves equal duplicate matches
                # toward the earliest viable video anchor without overpowering text quality.
                "match": scores[i - 1][j - 1] + (2.0 * similarity - 1.0) - j * 1e-6,
                "lyric": scores[i - 1][j] + gap_score,
                "video": scores[i][j - 1] + gap_score,
            }
            move = max(options, key=options.get)
            scores[i][j] = options[move]
            moves[i][j] = move

    mapping: dict[int, int] = {}
    i, j = lyric_count, video_count
    while i or j:
        move = moves[i][j]
        if move == "match":
            mapping[i - 1] = j - 1
            i -= 1
            j -= 1
        elif move == "lyric":
            i -= 1
        else:
            j -= 1

    aligned = []
    for lyric_index, lyric in enumerate(lyrics):
        video_index = mapping.get(lyric_index)
        if video_index is not None:
            candidate_similarity = text_similarity(lyric["text"], video_cues[video_index]["text"])
            if candidate_similarity < 0.5:
                video_index = None
        if video_index is not None:
            video = video_cues[video_index]
            similarity = text_similarity(lyric["text"], video["text"])
            flags = [] if similarity >= 0.72 else ["low-video-lyrics-similarity"]
            aligned.append(
                {
                    "text": lyric["text"],
                    "section": lyric.get("section") or video.get("section"),
                    "video_start_ms": int(video["video_start_ms"]),
                    "confidence": min(float(video["confidence"]), similarity),
                    "text_source": "suno_lyrics_confirmed_by_video",
                    "video_ocr_text": video["text"],
                    "lyrics_video_similarity": round(similarity, 4),
                    "flags": flags,
                }
            )
        else:
            aligned.append(
                {
                    "text": lyric["text"],
                    "section": lyric.get("section"),
                    "video_start_ms": None,
                    "confidence": 0.0,
                    "text_source": "suno_lyrics_interpolated_from_video",
                    "video_ocr_text": None,
                    "lyrics_video_similarity": 0.0,
                    "flags": ["lyrics-line-interpolated-from-video"],
                }
            )

    matched = [index for index, cue in enumerate(aligned) if cue["video_start_ms"] is not None]
    if not matched:
        raise SystemExit("none of the supplied lyric lines could be aligned to the video OCR")
    typical_spacing = estimate_line_spacing_ms(video_cues, interval_ms)
    for index, cue in enumerate(aligned):
        if cue["video_start_ms"] is not None:
            continue
        left = max((item for item in matched if item < index), default=None)
        right = min((item for item in matched if item > index), default=None)
        if left is not None and right is not None:
            left_time = aligned[left]["video_start_ms"]
            right_time = aligned[right]["video_start_ms"]
            fraction = (index - left) / (right - left)
            cue["video_start_ms"] = round(left_time + (right_time - left_time) * fraction)
        elif left is not None:
            spacing = typical_spacing
            if duration_ms is not None:
                missing_after = len(aligned) - left - 1
                available = max(1, duration_ms - aligned[left]["video_start_ms"])
                spacing = min(spacing, max(1, available // (missing_after + 1)))
            cue["video_start_ms"] = aligned[left]["video_start_ms"] + spacing * (index - left)
        else:
            available = max(1, aligned[right]["video_start_ms"])
            spacing = min(typical_spacing, max(1, available // (right + 1)))
            cue["video_start_ms"] = aligned[right]["video_start_ms"] - spacing * (right - index)
    previous = -1
    for index, cue in enumerate(aligned):
        candidate = max(previous + 1, int(cue["video_start_ms"]))
        if duration_ms is not None:
            maximum = duration_ms - (len(aligned) - index)
            if candidate > maximum:
                candidate = maximum
                cue["flags"].append("video-anchor-clamped-to-media")
            if candidate <= previous:
                raise SystemExit("too many lyric lines to fit within the media duration")
        cue["video_start_ms"] = candidate
        previous = cue["video_start_ms"]
    return aligned


def contains_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", text))


def same_text(left: str, right: str) -> bool:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return False
    coverage = min(len(a), len(b)) / max(len(a), len(b))
    if min(len(a), len(b)) >= 4 and coverage >= 0.55 and (a in b or b in a):
        return True
    return difflib.SequenceMatcher(None, a, b).ratio() >= 0.86


def clean_candidate(text: str) -> tuple[str, str | None]:
    text = text.strip().strip("\"“”‘’ ")
    section = None
    match = SECTION_RE.match(text)
    if match:
        section = match.group(1).strip()
        text = text[match.end() :].strip()
    else:
        damaged_section = re.match(
            r"^\[?(intro|verse\s*\d*|pre[- ]?chorus|chorus|bridge|final\s+chorus|outro)[\]l1)]*\s+",
            text,
            flags=re.IGNORECASE,
        )
        if damaged_section:
            section = damaged_section.group(1).strip()
            text = text[damaged_section.end() :].strip()
    text = text.strip().strip("\"“”‘’ ")
    if len(text.split()) >= 3:
        text = re.sub(r"\s+[27]$", "?", text)
    return text, section


def frame_candidate(frame: dict) -> dict | None:
    observations = []
    for observation in frame.get("observations", []):
        text = observation["text"].strip()
        upper = text.upper()
        if not 0.27 <= observation["y"] <= 0.48:
            continue
        if "MADE WITH SUNO" in upper or text.casefold().startswith("by @"):
            continue
        observations.append(observation)
    if not observations:
        return None
    observations.sort(key=lambda item: item["y"], reverse=True)
    chosen_index = min(range(len(observations)), key=lambda i: abs(observations[i]["y"] - 0.38))
    chosen = observations[chosen_index]
    pieces = [chosen["text"]]
    confidence = chosen["confidence"]
    if chosen_index > 0:
        preceding = observations[chosen_index - 1]
        gap = preceding["y"] - (chosen["y"] + chosen["height"])
        looks_like_continuation = chosen["width"] < 0.32 and preceding["width"] > chosen["width"] * 1.35
        if -0.004 <= gap <= 0.018 and looks_like_continuation and not contains_cjk(chosen["text"]):
            pieces.insert(0, preceding["text"])
            confidence = min(confidence, preceding["confidence"])
    if chosen_index + 1 < len(observations):
        following = observations[chosen_index + 1]
        gap = chosen["y"] - (following["y"] + following["height"])
        short_continuation = following["width"] < 0.25 or following["width"] < chosen["width"] * 0.5
        if (
            -0.004 <= gap <= 0.018
            and short_continuation
            and not SECTION_RE.match(following["text"])
            and not contains_cjk(chosen["text"] + following["text"])
        ):
            pieces.append(following["text"])
            confidence = min(confidence, following["confidence"])
    text, section = clean_candidate(" ".join(pieces))
    if not normalize_text(text):
        return None
    return {
        "time_ms": int(frame["timeMs"]),
        "text": text,
        "section": section,
        "confidence": float(confidence),
    }


def build_video_cues(frames: list[dict], interval_ms: int) -> list[dict]:
    candidates = [candidate for frame in frames if (candidate := frame_candidate(frame))]
    if not candidates:
        raise SystemExit("no lyric candidates were found in the video")

    accepted: list[dict] = []
    current: dict | None = None
    for candidate in candidates:
        if current is None:
            current = candidate.copy()
            current["video_start_ms"] = candidate["time_ms"]
            accepted.append(current)
            continue
        if same_text(candidate["text"], current["text"]):
            if len(normalize_text(candidate["text"])) > len(normalize_text(current["text"])):
                current["text"] = candidate["text"]
            if not current.get("section") and candidate.get("section"):
                current["section"] = candidate["section"]
            current["confidence"] = max(current["confidence"], candidate["confidence"])
            continue
        current = candidate.copy()
        current["video_start_ms"] = candidate["time_ms"]
        accepted.append(current)

    # Remove duplicate OCR states that were separated only by a short recognition glitch.
    compact: list[dict] = []
    for cue in accepted:
        if compact and same_text(cue["text"], compact[-1]["text"]):
            if len(normalize_text(cue["text"])) > len(normalize_text(compact[-1]["text"])):
                compact[-1]["text"] = cue["text"]
            continue
        compact.append(cue)
    # A wrapped continuation can briefly occupy the active anchor during a scroll.
    merged: list[dict] = []
    glitch_window_ms = max(1500, interval_ms * 3)
    for cue in compact:
        if any(
            same_text(cue["text"], prior["text"])
            and cue["video_start_ms"] - prior["video_start_ms"] <= glitch_window_ms
            for prior in merged[-3:]
        ):
            continue
        short = len(normalize_text(cue["text"])) <= 12
        if (
            merged
            and short
            and not contains_cjk(cue["text"])
            and cue["video_start_ms"] - merged[-1]["video_start_ms"] <= 1500
        ):
            previous = merged[-1]
            if normalize_text(cue["text"]) in normalize_text(previous["text"]):
                continue
            previous["text"] = f'{previous["text"]} {cue["text"]}'.strip()
            continue
        merged.append(cue)
    return merged


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return -120.0
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def vocal_envelope(vocals: Path, work: Path) -> tuple[list[float], int, float]:
    pcm = work / "vocals-16k.wav"
    run(
        [
            require_command("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(vocals),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(pcm),
            "-y",
        ]
    )
    with wave.open(str(pcm), "rb") as source:
        sample_rate = source.getframerate()
        samples = array.array("h")
        samples.frombytes(source.readframes(source.getnframes()))
    if sys.byteorder != "little":
        samples.byteswap()
    frame_ms = 20
    frame_size = sample_rate * frame_ms // 1000
    if len(samples) < frame_size:
        raise SystemExit("vocal stem is too short to analyze")
    db: list[float] = []
    for offset in range(0, len(samples) - frame_size + 1, frame_size):
        total = sum(value * value for value in samples[offset : offset + frame_size])
        rms = math.sqrt(total / frame_size) / 32768.0
        db.append(20 * math.log10(max(rms, 1e-6)))
    finite_floor = percentile([value for value in db if value > -100], 0.1)
    threshold = min(-34.0, max(-48.0, finite_floor + 10.0))
    if not any(value >= threshold for value in db):
        raise SystemExit(
            "no vocal activity was detected in the supplied stem; download the full-song Lead Vocal"
        )
    return db, frame_ms, threshold


def vocal_onsets(db: list[float], frame_ms: int, threshold: float) -> list[int]:
    active = []
    radius = max(1, 60 // frame_ms)
    for index in range(len(db)):
        active.append(max(db[max(0, index - radius) : min(len(db), index + radius + 1)]) >= threshold)
    # Fill brief gaps so consonants and breaths do not split a sung phrase.
    max_gap = max(1, 120 // frame_ms)
    index = 0
    while index < len(active):
        if active[index]:
            index += 1
            continue
        end = index
        while end < len(active) and not active[end]:
            end += 1
        if index > 0 and end < len(active) and end - index <= max_gap:
            active[index:end] = [True] * (end - index)
        index = end
    onsets = []
    for index, value in enumerate(active):
        if value and (index == 0 or not active[index - 1]):
            onsets.append(index * frame_ms)
    # Add strong energy rises inside continuous phrases.
    lookback = max(1, 240 // frame_ms)
    for index in range(lookback, len(db)):
        before = statistics.fmean(db[index - lookback : index])
        if db[index] >= threshold + 4 and db[index] - before >= 7:
            time_ms = index * frame_ms
            if not onsets or min(abs(time_ms - item) for item in onsets) > 140:
                onsets.append(time_ms)
    return sorted(onsets)


def calibrate(cues: list[dict], db: list[float], frame_ms: int, threshold: float, duration_ms: int) -> list[dict]:
    if not cues:
        raise SystemExit("no aligned lyric cues to calibrate")
    if not db:
        raise SystemExit("vocal activity envelope is empty")
    onsets = vocal_onsets(db, frame_ms, threshold)
    calibrated = []
    for index, cue in enumerate(cues):
        video_start = cue["video_start_ms"]
        video_frame = min(len(db) - 1, max(0, video_start // frame_ms))
        nearby_video_energy = max(db[max(0, video_frame - 5) : min(len(db), video_frame + 6)])
        video_is_active = nearby_video_energy >= threshold
        search_before = 300
        search_after = 1800 if index == 0 else (12000 if not video_is_active else 1200)
        candidates = [item for item in onsets if video_start - search_before <= item <= video_start + search_after]
        if calibrated:
            candidates = [item for item in candidates if item > calibrated[-1]["start_ms"] + 200]
        if index == 0 and video_start < 3000:
            candidates = [item for item in onsets if item <= min(duration_ms, 30000)]
        if candidates:
            forward = [item for item in candidates if item >= video_start - 100]
            chosen = min(forward, default=min(candidates, key=lambda item: abs(item - video_start)))
        else:
            chosen = video_start
        minimum = calibrated[-1]["start_ms"] + 1 if calibrated else 0
        maximum = duration_ms - (len(cues) - index)
        chosen = min(maximum, max(minimum, chosen))
        if chosen < minimum:
            raise SystemExit("calibrated lyric starts cannot fit within the media duration")
        frame_index = min(len(db) - 1, max(0, chosen // frame_ms))
        flags = list(cue.get("flags") or [])
        if not video_is_active:
            flags.append("low-vocal-energy-at-video-boundary")
        if db[frame_index] < threshold:
            flags.append("low-vocal-energy-at-start")
        if abs(chosen - video_start) > 1500:
            flags.append("large-alignment-shift")
        calibrated.append(
            {
                "index": index + 1,
                "text": cue["text"],
                "section": cue.get("section"),
                "start_ms": int(chosen),
                "end_ms": 0,
                "video_start_ms": int(video_start),
                "text_source": cue.get("text_source", "video_ocr"),
                "timing_source": "vocal_alignment" if chosen != video_start else "video_highlight",
                "confidence": round(float(cue["confidence"]), 4),
                "video_ocr_text": cue.get("video_ocr_text"),
                "lyrics_video_similarity": cue.get("lyrics_video_similarity"),
                "flags": flags,
            }
        )
    for index, cue in enumerate(calibrated):
        if index + 1 < len(calibrated):
            next_start = calibrated[index + 1]["start_ms"]
            first_frame = max(0, cue["start_ms"] // frame_ms)
            last_frame = min(len(db) - 1, max(first_frame, (next_start - 1) // frame_ms))
            last_active = max(
                (frame for frame in range(first_frame, last_frame + 1) if db[frame] >= threshold),
                default=None,
            )
            vocal_end = (last_active + 1) * frame_ms if last_active is not None else next_start
            if cue["start_ms"] < vocal_end <= next_start - 120:
                cue["end_ms"] = vocal_end
                cue["end_timing_source"] = "vocal_offset"
            else:
                cue["end_ms"] = next_start
                cue["end_timing_source"] = "next_line_start_fallback"
        else:
            last_active = max(
                (i for i, value in enumerate(db) if value >= threshold), default=None
            )
            if last_active is None:
                cue["end_ms"] = duration_ms
                cue["end_timing_source"] = "media_duration_fallback"
            else:
                cue["end_ms"] = min(
                    duration_ms, max(cue["start_ms"] + 1, (last_active + 1) * frame_ms)
                )
                cue["end_timing_source"] = "vocal_offset"
    return calibrated


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True, help="MP4 lyric video")
    parser.add_argument("--vocals", type=Path, required=True, help="isolated vocal WAV/audio")
    parser.add_argument("--lyrics", type=Path, required=True, help="untimed lyrics copied from Suno")
    parser.add_argument("--output", type=Path, required=True, help="output timeline JSON")
    parser.add_argument("--language", choices=["auto", "zh", "en"], default="auto")
    parser.add_argument("--interval", type=float, default=0.5, help="OCR sampling interval in seconds")
    parser.add_argument("--work-dir", type=Path, help="persistent intermediate directory")
    parser.add_argument("--keep-work", action="store_true")
    args = parser.parse_args()
    for path in [args.video, args.vocals, args.lyrics]:
        if not path.is_file():
            raise SystemExit(f"input does not exist: {path}")
    if args.interval < 0.25 or args.interval > 1.0:
        raise SystemExit("--interval must be between 0.25 and 1.0 seconds")

    video_path = args.video.resolve()
    vocals_path = args.vocals.resolve()
    lyrics_path = args.lyrics.resolve()
    print("[1/5] Validate media streams and durations", file=sys.stderr)
    video_info, vocal_info = validate_media_pair(video_path, vocals_path)
    duration_ms = min(video_info["duration_ms"], vocal_info["duration_ms"])
    lyrics = parse_lyrics(lyrics_path)

    temporary = None
    if args.work_dir:
        work = args.work_dir.resolve()
        work.mkdir(parents=True, exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="sloth-getsunolyrics-")
        work = Path(temporary.name)
    print(f"[2/5] OCR video frames every {args.interval:.2f}s", file=sys.stderr)
    frames = extract_ocr(video_path, work, args.interval, args.language)
    print("[3/5] Build video anchors and align supplied Suno lyrics", file=sys.stderr)
    raw_video_cues = build_video_cues(frames, round(args.interval * 1000))
    video_cues = filter_video_cues_for_lyrics(lyrics, raw_video_cues)
    aligned_cues = align_lyrics_to_video(
        lyrics,
        video_cues,
        round(args.interval * 1000),
        duration_ms=duration_ms,
    )
    print(
        f"      aligned {len(aligned_cues)} lyric lines to {len(video_cues)} video anchors",
        file=sys.stderr,
    )
    print("[4/5] Analyze isolated-vocal activity and calibrate boundaries", file=sys.stderr)
    db, frame_ms, threshold = vocal_envelope(vocals_path, work)
    timeline = calibrate(aligned_cues, db, frame_ms, threshold, duration_ms)
    confirmed_count = sum(
        cue["text_source"] == "suno_lyrics_confirmed_by_video" for cue in timeline
    )
    payload = {
        "schema_version": PIPELINE_SCHEMA_VERSION,
        "pipeline_fingerprint": pipeline_fingerprint(),
        "video": video_path.name,
        "video_sha256": sha256(video_path),
        "video_duration_ms": video_info["duration_ms"],
        "vocals": vocals_path.name,
        "vocals_sha256": sha256(vocals_path),
        "vocals_duration_ms": vocal_info["duration_ms"],
        "lyrics": lyrics_path.name,
        "lyrics_sha256": sha256(lyrics_path),
        "media_duration_ms": duration_ms,
        "ocr_interval_ms": round(args.interval * 1000),
        "ocr_language": args.language,
        "vocal_threshold_db": round(threshold, 2),
        "alignment_summary": {
            "lyrics_count": len(lyrics),
            "video_anchor_count": len(video_cues),
            "raw_video_anchor_count": len(raw_video_cues),
            "confirmed_count": confirmed_count,
            "interpolated_count": len(timeline) - confirmed_count,
            "confirmed_ratio": round(confirmed_count / len(timeline), 4),
        },
        "cues": timeline,
    }
    write_json_atomic(args.output, payload)
    print(f"[5/5] Wrote {args.output} ({len(timeline)} cues)", file=sys.stderr)
    if args.keep_work and temporary:
        retained = args.output.with_suffix(".work")
        shutil.copytree(work, retained, dirs_exist_ok=True)
        print(f"      retained intermediates at {retained}", file=sys.stderr)


if __name__ == "__main__":
    main()
