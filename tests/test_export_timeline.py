from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from export_timeline import csv_time, lrc_time, validate  # noqa: E402


class ExportTimelineTests(unittest.TestCase):
    def test_time_formats(self) -> None:
        self.assertEqual(lrc_time(59_999), "00:59.99")
        self.assertEqual(lrc_time(60_000), "01:00.00")
        self.assertEqual(csv_time(61_007), "01:01.007")

    def test_validate_accepts_monotonic_starts(self) -> None:
        cues = validate(
            {
                "media_duration_ms": 3_000,
                "cues": [
                    {"index": 1, "text": "a", "start_ms": 0},
                    {"index": 2, "text": "b", "start_ms": 1_000},
                ],
            }
        )
        self.assertEqual(len(cues), 2)

    def test_validate_rejects_non_monotonic_starts(self) -> None:
        with self.assertRaises(SystemExit):
            validate(
                {
                    "media_duration_ms": 3_000,
                    "cues": [
                        {"index": 1, "text": "a", "start_ms": 1_000},
                        {"index": 2, "text": "b", "start_ms": 1_000},
                    ],
                }
            )

    def test_cli_writes_netease_start_only_csv_and_no_subtitles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            timeline = root / "timeline.json"
            timeline.write_text(
                json.dumps(
                    {
                        "media_duration_ms": 5_000,
                        "cues": [
                            {
                                "index": 1,
                                "section": "Verse",
                                "text": "hello",
                                "start_ms": 1_234,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parents[1] / "scripts" / "export_timeline.py"),
                    "--input",
                    str(timeline),
                    "--output-dir",
                    str(root),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            with (root / "timeline.csv").open(encoding="utf-8-sig", newline="") as source:
                reader = csv.DictReader(source)
                rows = list(reader)
            self.assertEqual(reader.fieldnames, ["id", "section", "start_time", "lyric"])
            self.assertEqual(rows[0]["start_time"], "00:01.234")
            self.assertFalse((root / "timeline.srt").exists())
            self.assertFalse((root / "timeline.vtt").exists())


if __name__ == "__main__":
    unittest.main()
