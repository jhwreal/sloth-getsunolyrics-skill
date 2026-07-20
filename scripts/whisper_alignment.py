#!/usr/bin/env python3
"""Content-aware lyric timing with whisper.cpp DTW token timestamps."""

from __future__ import annotations

import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from typing import Iterable
import unicodedata


ALIGNMENT_CACHE_VERSION = 1
DTW_BACKTRACK_TOLERANCE_MS = 500
DEFAULT_MODEL_CANDIDATES = [
    Path("~/.cache/hyperframes/whisper/models/ggml-large-v3.bin"),
    Path("~/.cache/whisper/ggml-large-v3.bin"),
    Path("~/.cache/whisper.cpp/ggml-large-v3.bin"),
]


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def resolve_whisper_cli(explicit: Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit.expanduser())
    if os.environ.get("SLOTH_WHISPER_CLI"):
        candidates.append(Path(os.environ["SLOTH_WHISPER_CLI"]).expanduser())
    discovered = shutil.which("whisper-cli")
    if discovered:
        candidates.append(Path(discovered))
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise SystemExit(
        "high-precision timing requires whisper-cli; set --whisper-cli or "
        "SLOTH_WHISPER_CLI to an executable whisper.cpp CLI"
    )


def resolve_whisper_model(explicit: Path | None = None) -> Path:
    candidates: list[Path] = []
    if explicit is not None:
        candidates.append(explicit.expanduser())
    if os.environ.get("SLOTH_WHISPER_MODEL"):
        candidates.append(Path(os.environ["SLOTH_WHISPER_MODEL"]).expanduser())
    candidates.extend(path.expanduser() for path in DEFAULT_MODEL_CANDIDATES)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise SystemExit(
        "high-precision timing requires a local whisper.cpp model; set "
        "--whisper-model or SLOTH_WHISPER_MODEL (large-v3 is recommended)"
    )


def dtw_preset_for_model(model: Path) -> str:
    name = model.name.casefold().replace("_", "-")
    mappings = [
        ("large-v3-turbo", "large.v3.turbo"),
        ("large-v3", "large.v3"),
        ("large-v2", "large.v2"),
        ("large-v1", "large.v1"),
        ("medium.en", "medium.en"),
        ("medium", "medium"),
        ("small.en", "small.en"),
        ("small", "small"),
        ("base.en", "base.en"),
        ("base", "base"),
        ("tiny.en", "tiny.en"),
        ("tiny", "tiny"),
    ]
    for marker, preset in mappings:
        if marker in name:
            return preset
    raise SystemExit(
        f"cannot infer a whisper.cpp DTW preset from model filename: {model.name}; "
        "rename it to include its official model family"
    )


def transcript_has_dtw(payload: dict) -> bool:
    return any(
        int(token.get("t_dtw", -1)) >= 0
        for segment in payload.get("transcription", [])
        for token in segment.get("tokens", [])
        if normalize_text(str(token.get("text", "")))
    )


def transcribe_with_dtw(
    vocals: Path,
    lyrics: list[dict],
    work: Path,
    *,
    language: str = "auto",
    whisper_cli: Path | None = None,
    whisper_model: Path | None = None,
    threads: int | None = None,
) -> tuple[dict, dict]:
    """Run deterministic offline whisper.cpp DTW, with a content-addressed cache."""
    cli = resolve_whisper_cli(whisper_cli)
    model = resolve_whisper_model(whisper_model)
    preset = dtw_preset_for_model(model)
    thread_count = threads or min(8, max(1, os.cpu_count() or 1))
    if thread_count < 1:
        raise SystemExit("--whisper-threads must be positive")
    prompt = " ".join(str(item["text"]).strip() for item in lyrics)
    if not prompt:
        raise SystemExit("cannot run Whisper alignment with empty lyrics")

    root = work / "whisper-dtw"
    transcript_path = root / "transcript.json"
    cache_path = root / "cache.json"
    lyrics_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    identity = {
        "cache_version": ALIGNMENT_CACHE_VERSION,
        "vocals_sha256": sha256(vocals),
        "lyrics_prompt_sha256": lyrics_digest,
        "whisper_cli_sha256": sha256(cli),
        "whisper_model_sha256": sha256(model),
        "whisper_model_filename": model.name,
        "dtw_preset": preset,
        "language": language,
        "threads": thread_count,
        "max_context": 0,
        "flash_attention": False,
        "gpu": False,
    }
    cache = read_json(cache_path) or {}
    cached_payload = read_json(transcript_path)
    if (
        cached_payload
        and transcript_has_dtw(cached_payload)
        and all(cache.get(key) == value for key, value in identity.items())
        and cache.get("transcript_sha256") == sha256(transcript_path)
    ):
        metadata = {
            **identity,
            "backend": "whisper.cpp-dtw",
            "cache_reused": True,
        }
        return cached_payload, metadata

    root.mkdir(parents=True, exist_ok=True)
    temporary_prefix = root / ".transcript"
    temporary_json = temporary_prefix.with_suffix(".json")
    temporary_json.unlink(missing_ok=True)
    command = [
        str(cli),
        "-ng",
        "-nfa",
        "-t",
        str(thread_count),
        "-m",
        str(model),
        "-l",
        language,
        "-mc",
        "0",
        "-nf",
        "-sns",
        "-dtw",
        preset,
        "--prompt",
        prompt,
        "-ojf",
        "-np",
        "-of",
        str(temporary_prefix),
        "-f",
        str(vocals),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        details = "\n".join((result.stderr or result.stdout).strip().splitlines()[-8:])
        raise SystemExit(f"whisper.cpp DTW alignment failed:\n{details}")
    payload = read_json(temporary_json)
    if not payload or not transcript_has_dtw(payload):
        raise SystemExit(
            "whisper.cpp produced no DTW token timestamps; DTW requires -nfa because "
            "Flash Attention disables alignment timestamps"
        )
    temporary_json.replace(transcript_path)
    write_json_atomic(
        cache_path,
        {
            **identity,
            "transcript_sha256": sha256(transcript_path),
        },
    )
    metadata = {
        **identity,
        "backend": "whisper.cpp-dtw",
        "cache_reused": False,
    }
    return payload, metadata


def parse_segments(transcript: dict) -> list[dict]:
    segments = []
    for item in transcript.get("transcription", []):
        text = str(item.get("text", ""))
        if not normalize_text(text):
            continue
        raw_start = int((item.get("offsets") or {}).get("from", 0))
        tokens = []
        for token in item.get("tokens", []):
            token_text = str(token.get("text", ""))
            if token_text.startswith("[_") or not normalize_text(token_text):
                continue
            dtw_value = int(token.get("t_dtw", -1))
            token_start = (
                dtw_value * 10
                if dtw_value >= 0
                else int((token.get("offsets") or {}).get("from", raw_start))
            )
            tokens.append(
                {
                    "text": token_text,
                    "start_ms": token_start,
                    "dtw_available": dtw_value >= 0,
                }
            )
        first_dtw = next(
            (token["start_ms"] for token in tokens if token["dtw_available"]),
            raw_start,
        )
        segments.append(
            {
                "text": text,
                "dtw_start_ms": int(first_dtw),
                "raw_start_ms": raw_start,
                "tokens": tokens,
                "dtw_available": any(
                    bool(token["dtw_available"]) for token in tokens
                ),
            }
        )
    if not segments:
        raise SystemExit("Whisper returned no lyric-like text segments")
    return segments


def group_starts(lyrics: list[dict], segments: list[dict]) -> list[dict]:
    """Map line boundaries inside a Whisper segment group to DTW token starts."""
    canonical = "".join(normalize_text(str(item["text"])) for item in lyrics)
    line_positions = []
    position = 0
    for lyric in lyrics:
        line_positions.append(position)
        position += len(normalize_text(str(lyric["text"])))

    recognized_chars: list[str] = []
    char_sources: list[dict] = []
    for segment in segments:
        for token in segment.get("tokens", []):
            for character in normalize_text(str(token.get("text", ""))):
                recognized_chars.append(character)
                char_sources.append(
                    {
                        "dtw_start_ms": int(token["start_ms"]),
                        "raw_start_ms": int(segment["raw_start_ms"]),
                        "dtw_available": bool(token.get("dtw_available")),
                    }
                )
    recognized = "".join(recognized_chars)
    if not canonical or not recognized:
        return [
            {
                "dtw_start_ms": int(segments[0]["dtw_start_ms"]),
                "raw_start_ms": int(segments[0]["raw_start_ms"]),
                "dtw_available": bool(segments[0].get("dtw_available")),
            }
            for _ in lyrics
        ]

    blocks = [
        block
        for block in difflib.SequenceMatcher(None, canonical, recognized).get_matching_blocks()
        if block.size
    ]
    mapped = []
    last_position = 0
    for lyric_index, canonical_position in enumerate(line_positions):
        line_end = (
            line_positions[lyric_index + 1]
            if lyric_index + 1 < len(line_positions)
            else len(canonical)
        )
        recognized_position = None
        for block in blocks:
            if block.a <= canonical_position < block.a + block.size:
                recognized_position = block.b + canonical_position - block.a
                break
        if recognized_position is None:
            following = next(
                (block for block in blocks if canonical_position <= block.a < line_end),
                None,
            )
            if following is not None:
                recognized_position = following.b - (following.a - canonical_position)
        if recognized_position is None:
            recognized_position = last_position
        recognized_position = min(
            len(char_sources) - 1,
            max(last_position, int(recognized_position)),
        )
        mapped.append(char_sources[recognized_position].copy())
        last_position = recognized_position
    return mapped


def _align_groups(
    lyrics: list[dict], segments: list[dict], video_cues: list[dict]
) -> dict[int, dict]:
    lyric_count, segment_count = len(lyrics), len(segments)
    negative = -1e9
    scores = [[negative] * (segment_count + 1) for _ in range(lyric_count + 1)]
    moves: list[list[tuple | None]] = [
        [None] * (segment_count + 1) for _ in range(lyric_count + 1)
    ]
    scores[0][0] = 0.0
    for lyric_index in range(lyric_count + 1):
        for segment_index in range(segment_count + 1):
            if scores[lyric_index][segment_index] < -1e8:
                continue
            options = []
            if segment_index < segment_count:
                options.append(
                    (lyric_index, segment_index + 1, -0.45, ("segment", 0, 1, 0.0))
                )
            if lyric_index < lyric_count:
                options.append(
                    (lyric_index + 1, segment_index, -0.85, ("lyric", 1, 0, 0.0))
                )
            for lyrics_in_group in range(1, min(3, lyric_count - lyric_index) + 1):
                lyric_text = "".join(
                    str(item["text"])
                    for item in lyrics[lyric_index : lyric_index + lyrics_in_group]
                )
                for segments_in_group in range(
                    1, min(3, segment_count - segment_index) + 1
                ):
                    segment_text = "".join(
                        str(item["text"])
                        for item in segments[
                            segment_index : segment_index + segments_in_group
                        ]
                    )
                    similarity = difflib.SequenceMatcher(
                        None, normalize_text(lyric_text), normalize_text(segment_text)
                    ).ratio()
                    distance = abs(
                        int(segments[segment_index]["dtw_start_ms"])
                        - int(video_cues[lyric_index]["video_start_ms"])
                    )
                    time_penalty = min(distance / 20_000, 1.0) * 0.12
                    group_penalty = 0.08 * (
                        lyrics_in_group + segments_in_group - 2
                    )
                    increment = 2.8 * similarity - 0.8 - group_penalty - time_penalty
                    options.append(
                        (
                            lyric_index + lyrics_in_group,
                            segment_index + segments_in_group,
                            increment,
                            ("match", lyrics_in_group, segments_in_group, similarity),
                        )
                    )
            for next_lyric, next_segment, increment, move in options:
                candidate = scores[lyric_index][segment_index] + increment
                if candidate > scores[next_lyric][next_segment]:
                    scores[next_lyric][next_segment] = candidate
                    moves[next_lyric][next_segment] = (
                        lyric_index,
                        segment_index,
                        move,
                    )

    segment_index = max(
        range(segment_count + 1),
        key=lambda index: scores[lyric_count][index],
    )
    lyric_index = lyric_count
    matched: dict[int, dict] = {}
    while lyric_index or segment_index:
        previous = moves[lyric_index][segment_index]
        if previous is None:
            break
        prior_lyric, prior_segment, move = previous
        kind, lyrics_in_group, segments_in_group, similarity = move
        if kind == "match":
            segment_group = segments[
                prior_segment : prior_segment + segments_in_group
            ]
            starts = (
                group_starts(
                    lyrics[prior_lyric : prior_lyric + lyrics_in_group],
                    segment_group,
                )
                if lyrics_in_group > 1
                else [
                    {
                        "dtw_start_ms": int(segment_group[0]["dtw_start_ms"]),
                        "raw_start_ms": int(segment_group[0]["raw_start_ms"]),
                        "dtw_available": bool(
                            segment_group[0].get("dtw_available")
                        ),
                    }
                ]
            )
            for offset in range(lyrics_in_group):
                matched[prior_lyric + offset] = {
                    **starts[offset],
                    "similarity": float(similarity),
                    "lyric_count": lyrics_in_group,
                    "segment_count": segments_in_group,
                    "segment_text": "".join(
                        str(item["text"]) for item in segment_group
                    ),
                }
        lyric_index, segment_index = prior_lyric, prior_segment
    return matched


def align_lyrics_to_whisper(
    lyrics: list[dict],
    transcript: dict,
    video_cues: list[dict],
    *,
    duration_ms: int,
) -> list[dict | None]:
    segments = parse_segments(transcript)
    matched = _align_groups(lyrics, segments, video_cues)

    # Repair only weak single-line matches. Multi-line matches retain their
    # token-level internal boundary so the second lyric does not inherit the
    # enclosing Whisper segment start.
    for index, lyric in enumerate(lyrics):
        existing = matched.get(index)
        if (
            existing
            and existing.get("dtw_start_ms") is not None
            and (
                float(existing.get("similarity", 0.0)) >= 0.62
                or int(existing.get("lyric_count", 1)) > 1
            )
        ):
            continue
        lower = max(0, int(video_cues[index]["video_start_ms"]) - 4_000)
        upper = (
            int(video_cues[index + 1]["video_start_ms"]) + 2_000
            if index + 1 < len(video_cues)
            else duration_ms
        )
        best_single = None
        best_group = None
        for segment_index, segment in enumerate(segments):
            if not lower <= int(segment["raw_start_ms"]) <= upper:
                continue
            for count in range(1, min(3, len(segments) - segment_index) + 1):
                segment_group = segments[segment_index : segment_index + count]
                text = "".join(str(item["text"]) for item in segment_group)
                similarity = difflib.SequenceMatcher(
                    None,
                    normalize_text(str(lyric["text"])),
                    normalize_text(text),
                ).ratio()
                candidate = {
                    "dtw_start_ms": int(segment["dtw_start_ms"]),
                    "raw_start_ms": int(segment["raw_start_ms"]),
                    "dtw_available": bool(segment.get("dtw_available")),
                    "similarity": float(similarity),
                    "lyric_count": 1,
                    "segment_count": count,
                    "segment_text": text,
                }
                if count == 1 and (
                    best_single is None or similarity > best_single["similarity"]
                ):
                    best_single = candidate
                score = similarity - 0.18 * (count - 1)
                if best_group is None or score > best_group[0]:
                    best_group = (score, candidate)
        best = (
            best_single
            if best_single and float(best_single["similarity"]) >= 0.35
            else (best_group[1] if best_group else None)
        )
        if best and float(best["similarity"]) >= 0.35:
            matched[index] = best
    return [matched.get(index) for index in range(len(lyrics))]


def nearest(items: Iterable[int], target: int, radius: int) -> int | None:
    candidates = [item for item in items if abs(int(item) - target) <= radius]
    return min(candidates, key=lambda item: abs(int(item) - target)) if candidates else None


def select_start_times(
    cues: list[dict],
    whisper_matches: list[dict | None],
    vocal_onsets: list[int],
    *,
    duration_ms: int,
) -> list[dict]:
    if len(cues) != len(whisper_matches):
        raise SystemExit("Whisper alignment count does not match the lyric cue count")
    results = []
    for index, (cue, match) in enumerate(zip(cues, whisper_matches)):
        flags = list(cue.get("flags") or [])
        video_start = int(cue["video_start_ms"])
        chosen = video_start
        timing_source = "video_anchor_fallback"
        dtw_start = None
        raw_start = None
        similarity = None
        if match:
            dtw_start = int(match["dtw_start_ms"])
            raw_start = int(match["raw_start_ms"])
            similarity = float(match.get("similarity", 0.0))
            if similarity < 0.5:
                flags.append("low-whisper-lyrics-similarity")
            if bool(match.get("dtw_available")):
                if dtw_start < raw_start - DTW_BACKTRACK_TOLERANCE_MS:
                    later_onset = next(
                        (
                            onset
                            for onset in vocal_onsets
                            if raw_start + 100 <= onset <= raw_start + 1_000
                        ),
                        None,
                    )
                    refined = (
                        later_onset
                        if later_onset is not None
                        else nearest(vocal_onsets, raw_start, 1_000)
                    )
                    chosen = int(refined if refined is not None else raw_start)
                    timing_source = "vocal_onset_after_rejected_dtw_backtrack"
                    flags.append("dtw-before-whisper-segment-rejected")
                else:
                    chosen = dtw_start
                    timing_source = "whisper_dtw_token_start"
            else:
                refined = nearest(vocal_onsets, raw_start, 1_000)
                chosen = int(refined if refined is not None else raw_start)
                timing_source = "whisper_segment_vocal_fallback"
                flags.append("missing-dtw-token-start")
        else:
            refined = nearest(vocal_onsets, video_start, 1_000)
            chosen = int(refined if refined is not None else video_start)
            flags.append("missing-whisper-lyric-match")

        minimum = results[-1]["start_ms"] + 1 if results else 0
        maximum = duration_ms - (len(cues) - index)
        bounded = min(maximum, max(minimum, chosen))
        if bounded != chosen:
            flags.append("monotonic-start-clamp")
        results.append(
            {
                "index": index + 1,
                "text": cue["text"],
                "section": cue.get("section"),
                "start_ms": int(bounded),
                "video_start_ms": video_start,
                "whisper_dtw_start_ms": dtw_start,
                "whisper_segment_start_ms": raw_start,
                "whisper_lyrics_similarity": (
                    round(similarity, 4) if similarity is not None else None
                ),
                "text_source": cue.get("text_source", "video_ocr"),
                "timing_source": timing_source,
                "confidence": round(float(cue["confidence"]), 4),
                "video_ocr_text": cue.get("video_ocr_text"),
                "lyrics_video_similarity": cue.get("lyrics_video_similarity"),
                "flags": flags,
            }
        )
    return results
