from __future__ import annotations

import sys
from pathlib import Path
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from validate_package import (  # noqa: E402
    parse_csv_time,
    parse_lrc,
    parse_numbered_subtitles,
    parse_subtitle_time,
)


class ValidatePackageTests(unittest.TestCase):
    def test_parse_csv_time(self) -> None:
        self.assertEqual(parse_csv_time("00:00.000"), 0)
        self.assertEqual(parse_csv_time("61:02.345"), 3_662_345)

    def test_parse_csv_time_rejects_invalid_values(self) -> None:
        for value in ["1:02.345", "00:60.000", "00:01,000"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_csv_time(value)

    def test_parse_export_formats(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            lrc = root / "timeline.lrc"
            srt = root / "timeline.srt"
            vtt = root / "timeline.vtt"
            lrc.write_text("[00:01.23]hello\n", encoding="utf-8")
            srt.write_text("1\n00:00:01,234 --> 00:00:02,345\nhello\n", encoding="utf-8")
            vtt.write_text(
                "WEBVTT\n\n1\n00:00:01.234 --> 00:00:02.345\nhello\n",
                encoding="utf-8",
            )
            self.assertEqual(parse_lrc(lrc), [{"start_ms": 1230, "text": "hello"}])
            expected = [{"start_ms": 1234, "end_ms": 2345, "text": "hello"}]
            self.assertEqual(parse_numbered_subtitles(srt), expected)
            self.assertEqual(parse_numbered_subtitles(vtt, webvtt=True), expected)

    def test_parse_subtitle_time_rejects_invalid_clock(self) -> None:
        self.assertEqual(parse_subtitle_time("01:02:03.004"), 3_723_004)
        with self.assertRaises(ValueError):
            parse_subtitle_time("00:60:00.000")


if __name__ == "__main__":
    unittest.main()
