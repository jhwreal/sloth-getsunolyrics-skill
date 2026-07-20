#!/usr/bin/env python3
"""Compare a generated JSON timeline with a manually reviewed CSV or TypeScript timeline."""

from __future__ import annotations

import argparse
from collections import Counter
import csv
import difflib
import json
from pathlib import Path
import re
import statistics


def normalize(text: str) -> str:
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text.casefold())


def parse_time(value: str) -> int:
    minutes, seconds = value.split(":", 1)
    return round((int(minutes) * 60 + float(seconds)) * 1000)


def load_csv(path: Path) -> list[dict]:
    with path.open(encoding="utf-8-sig", newline="") as source:
        rows = list(csv.DictReader(source))
    cues = []
    for row in rows:
        text = row.get("english_lyric") or row.get("text") or row.get("lyric")
        start = row.get("start_time") or row.get("start_ms")
        if text is None or start is None:
            raise SystemExit("gold CSV needs a text/lyric column and start_time/start_ms")
        cues.append(
            {
                "text": text,
                "start_ms": parse_time(start) if ":" in start else int(start),
            }
        )
    return cues


def load_typescript(path: Path) -> list[dict]:
    source = path.read_text(encoding="utf-8")
    pattern = re.compile(
        r"startMs:\s*([0-9_]+),\s*\n\s*(?:endMs:\s*([0-9_]+),\s*\n\s*)?(?:line:\s*\"([^\"]*)\"|lines:\s*\[\"([^\"]*)\"\])"
    )
    cues = []
    for start, end, line, lines in pattern.findall(source):
        cues.append(
            {
                "text": line or lines,
                "start_ms": int(start.replace("_", "")),
            }
        )
    if not cues:
        raise SystemExit("no lyric cues found in TypeScript gold file")
    return cues


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = int(position)
    upper = min(len(ordered) - 1, lower + 1)
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def align_cues(generated: list[dict], gold: list[dict]) -> list[tuple[int, int]]:
    """Order-align cues so one missing row does not corrupt every later metric."""
    rows, columns = len(generated), len(gold)
    gap_score = -0.55
    scores = [[0.0] * (columns + 1) for _ in range(rows + 1)]
    moves = [[""] * (columns + 1) for _ in range(rows + 1)]
    for row in range(1, rows + 1):
        scores[row][0] = row * gap_score
        moves[row][0] = "generated"
    for column in range(1, columns + 1):
        scores[0][column] = column * gap_score
        moves[0][column] = "gold"
    for row in range(1, rows + 1):
        for column in range(1, columns + 1):
            similarity = difflib.SequenceMatcher(
                None,
                normalize(generated[row - 1]["text"]),
                normalize(gold[column - 1]["text"]),
            ).ratio()
            options = {
                "match": scores[row - 1][column - 1] + (2.0 * similarity - 1.0),
                "generated": scores[row - 1][column] + gap_score,
                "gold": scores[row][column - 1] + gap_score,
            }
            move = max(options, key=options.get)
            scores[row][column] = options[move]
            moves[row][column] = move
    aligned = []
    row, column = rows, columns
    while row or column:
        move = moves[row][column]
        if move == "match":
            similarity = difflib.SequenceMatcher(
                None,
                normalize(generated[row - 1]["text"]),
                normalize(gold[column - 1]["text"]),
            ).ratio()
            if similarity >= 0.5:
                aligned.append((row - 1, column - 1))
            row -= 1
            column -= 1
        elif move == "generated":
            row -= 1
        else:
            column -= 1
    return list(reversed(aligned))


def timing_metrics(
    pairs: list[tuple[dict, dict]], actual_field: str, expected_field: str = "start_ms"
) -> dict:
    errors = [
        abs(int(actual[actual_field]) - int(expected[expected_field]))
        for actual, expected in pairs
    ]
    signed_errors = [
        int(actual[actual_field]) - int(expected[expected_field])
        for actual, expected in pairs
    ]
    return {
        "cue_count": len(errors),
        "max_abs_error_ms": max(errors),
        "median_abs_error_ms": round(statistics.median(errors), 2),
        "mean_abs_error_ms": round(statistics.fmean(errors), 2),
        "mean_signed_error_ms": round(statistics.fmean(signed_errors), 2),
        "p90_abs_error_ms": round(percentile(errors, 0.9), 2),
        "p95_abs_error_ms": round(percentile(errors, 0.95), 2),
        "within_500ms_ratio": round(sum(error <= 500 for error in errors) / len(errors), 4),
        "within_1000ms_ratio": round(sum(error <= 1000 for error in errors) / len(errors), 4),
        "within_1500ms_ratio": round(sum(error <= 1500 for error in errors) / len(errors), 4),
        "all_within_500ms": all(error <= 500 for error in errors),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    gold = parser.add_mutually_exclusive_group(required=True)
    gold.add_argument("--gold-csv", type=Path)
    gold.add_argument("--gold-typescript", type=Path)
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    parser.add_argument(
        "--max-start-error-ms",
        type=int,
        help="exit nonzero unless every paired start is within this absolute error",
    )
    args = parser.parse_args()

    generated = json.loads(args.generated.read_text(encoding="utf-8"))["cues"]
    expected = load_csv(args.gold_csv) if args.gold_csv else load_typescript(args.gold_typescript)
    aligned_indexes = align_cues(generated, expected)
    if not aligned_indexes:
        raise SystemExit("empty generated/gold timeline or no text-aligned cues")
    pairs = [(generated[left], expected[right]) for left, right in aligned_indexes]
    similarities = [
        difflib.SequenceMatcher(None, normalize(actual["text"]), normalize(gold["text"])).ratio()
        for actual, gold in pairs
    ]
    start_timing = timing_metrics(pairs, "start_ms")
    report = {
        "generated_cues": len(generated),
        "gold_cues": len(expected),
        "paired_cues": len(pairs),
        "unmatched_generated_cues": len(generated) - len(pairs),
        "unmatched_gold_cues": len(expected) - len(pairs),
        "exact_text_matches": sum(
            normalize(actual["text"]) == normalize(gold["text"]) for actual, gold in pairs
        ),
        "text_similarity_at_least_0_9": sum(value >= 0.9 for value in similarities),
        "video_timing": timing_metrics(pairs, "video_start_ms"),
        "start_timing": start_timing,
        "flag_counts": dict(
            sorted(Counter(flag for cue in generated for flag in cue.get("flags", [])).items())
        ),
        "largest_start_errors": sorted(
            [
                {
                    "index": index + 1,
                    "text": actual["text"],
                    "gold_start_ms": gold["start_ms"],
                    "video_start_ms": actual["video_start_ms"],
                    "start_ms": actual["start_ms"],
                    "start_abs_error_ms": abs(int(actual["start_ms"]) - int(gold["start_ms"])),
                    "flags": actual.get("flags", []),
                }
                for index, (actual, gold) in enumerate(pairs)
            ],
            key=lambda item: item["start_abs_error_ms"],
            reverse=True,
        )[:10],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    if args.max_start_error_ms is not None:
        complete_pairing = len(pairs) == len(generated) == len(expected)
        if (
            not complete_pairing
            or int(start_timing["max_abs_error_ms"]) > args.max_start_error_ms
        ):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
