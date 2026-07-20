from __future__ import annotations

import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from evaluate_timeline import align_cues, timing_metrics  # noqa: E402


class EvaluateTimelineTests(unittest.TestCase):
    def test_alignment_does_not_shift_after_missing_generated_cue(self) -> None:
        generated = [{"text": "one"}, {"text": "three"}]
        gold = [{"text": "one"}, {"text": "two"}, {"text": "three"}]
        self.assertEqual(align_cues(generated, gold), [(0, 0), (1, 2)])

    def test_start_metrics_report_strict_maximum(self) -> None:
        metrics = timing_metrics(
            [
                ({"start_ms": 1_100}, {"start_ms": 1_000}),
                ({"start_ms": 2_490}, {"start_ms": 2_000}),
            ],
            "start_ms",
        )
        self.assertEqual(metrics["max_abs_error_ms"], 490)
        self.assertTrue(metrics["all_within_500ms"])


if __name__ == "__main__":
    unittest.main()
