from __future__ import annotations

import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from export_timeline import csv_time, lrc_time, subtitle_time, validate  # noqa: E402


class ExportTimelineTests(unittest.TestCase):
    def test_time_formats(self) -> None:
        self.assertEqual(lrc_time(59_999), "00:59.99")
        self.assertEqual(lrc_time(60_000), "01:00.00")
        self.assertEqual(subtitle_time(3_661_007, ","), "01:01:01,007")
        self.assertEqual(subtitle_time(3_661_007, "."), "01:01:01.007")
        self.assertEqual(csv_time(61_007), "01:01.007")

    def test_validate_accepts_monotonic_intervals(self) -> None:
        cues = validate(
            {
                "media_duration_ms": 3_000,
                "cues": [
                    {"index": 1, "text": "a", "start_ms": 0, "end_ms": 1_000},
                    {"index": 2, "text": "b", "start_ms": 1_000, "end_ms": 3_000},
                ],
            }
        )
        self.assertEqual(len(cues), 2)

    def test_validate_rejects_non_monotonic_intervals(self) -> None:
        with self.assertRaises(SystemExit):
            validate(
                {
                    "media_duration_ms": 3_000,
                    "cues": [
                        {"index": 1, "text": "a", "start_ms": 1_000, "end_ms": 2_000},
                        {"index": 2, "text": "b", "start_ms": 1_000, "end_ms": 3_000},
                    ],
                }
            )


if __name__ == "__main__":
    unittest.main()
