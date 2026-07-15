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
    rows = list(csv.DictReader(path.open()))
    cues = []
    for row in rows:
        text = row.get("english_lyric") or row.get("text") or row.get("lyric")
        start = row.get("start_time") or row.get("start_ms")
        end = row.get("end_time") or row.get("end_ms")
        if text is None or start is None:
            raise SystemExit("gold CSV needs a text/lyric column and start_time/start_ms")
        cues.append(
            {
                "text": text,
                "start_ms": parse_time(start) if ":" in start else int(start),
                "end_ms": parse_time(end) if end and ":" in end else int(end or 0),
            }
        )
    return cues


def load_typescript(path: Path) -> list[dict]:
    source = path.read_text()
    pattern = re.compile(
        r"startMs:\s*([0-9_]+),\s*\n\s*(?:endMs:\s*([0-9_]+),\s*\n\s*)?(?:line:\s*\"([^\"]*)\"|lines:\s*\[\"([^\"]*)\"\])"
    )
    cues = []
    for start, end, line, lines in pattern.findall(source):
        cues.append(
            {
                "text": line or lines,
                "start_ms": int(start.replace("_", "")),
                "end_ms": int(end.replace("_", "")) if end else 0,
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


def timing_metrics(generated: list[dict], gold: list[dict], field: str) -> dict:
    errors = [abs(int(actual[field]) - int(expected["start_ms"])) for actual, expected in zip(generated, gold)]
    return {
        "median_abs_error_ms": round(statistics.median(errors), 2),
        "mean_abs_error_ms": round(statistics.fmean(errors), 2),
        "p90_abs_error_ms": round(percentile(errors, 0.9), 2),
        "p95_abs_error_ms": round(percentile(errors, 0.95), 2),
        "within_500ms_ratio": round(sum(error <= 500 for error in errors) / len(errors), 4),
        "within_1000ms_ratio": round(sum(error <= 1000 for error in errors) / len(errors), 4),
        "within_1500ms_ratio": round(sum(error <= 1500 for error in errors) / len(errors), 4),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated", type=Path, required=True)
    gold = parser.add_mutually_exclusive_group(required=True)
    gold.add_argument("--gold-csv", type=Path)
    gold.add_argument("--gold-typescript", type=Path)
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    args = parser.parse_args()

    generated = json.loads(args.generated.read_text())["cues"]
    expected = load_csv(args.gold_csv) if args.gold_csv else load_typescript(args.gold_typescript)
    count = min(len(generated), len(expected))
    if not count:
        raise SystemExit("empty generated or gold timeline")
    pairs = list(zip(generated[:count], expected[:count]))
    similarities = [
        difflib.SequenceMatcher(None, normalize(actual["text"]), normalize(gold["text"])).ratio()
        for actual, gold in pairs
    ]
    report = {
        "generated_cues": len(generated),
        "gold_cues": len(expected),
        "paired_cues": count,
        "exact_text_matches": sum(
            normalize(actual["text"]) == normalize(gold["text"]) for actual, gold in pairs
        ),
        "text_similarity_at_least_0_9": sum(value >= 0.9 for value in similarities),
        "video_timing": timing_metrics(generated[:count], expected[:count], "video_start_ms"),
        "vocal_calibrated_timing": timing_metrics(generated[:count], expected[:count], "start_ms"),
        "flag_counts": dict(
            sorted(Counter(flag for cue in generated for flag in cue.get("flags", [])).items())
        ),
        "largest_vocal_errors": sorted(
            [
                {
                    "index": index + 1,
                    "text": actual["text"],
                    "gold_start_ms": gold["start_ms"],
                    "video_start_ms": actual["video_start_ms"],
                    "start_ms": actual["start_ms"],
                    "vocal_abs_error_ms": abs(int(actual["start_ms"]) - int(gold["start_ms"])),
                    "flags": actual.get("flags", []),
                }
                for index, (actual, gold) in enumerate(pairs)
            ],
            key=lambda item: item["vocal_abs_error_ms"],
            reverse=True,
        )[:10],
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
