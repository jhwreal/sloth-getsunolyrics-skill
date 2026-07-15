from __future__ import annotations

import sys
from pathlib import Path
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from extract_timeline import align_lyrics_to_video, parse_lyrics  # noqa: E402


class ExtractTimelineTests(unittest.TestCase):
    def test_parse_lyrics_preserves_text_and_sections(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lyrics.txt"
            path.write_text("[Verse 1]\nHello, World!\n\n[Chorus] 再次出发\n", encoding="utf-8")
            self.assertEqual(
                parse_lyrics(path),
                [
                    {"lyrics_index": 1, "text": "Hello, World!", "section": "Verse 1"},
                    {"lyrics_index": 2, "text": "再次出发", "section": "Chorus"},
                ],
            )

    def test_alignment_keeps_supplied_lyrics_as_canonical_text(self) -> None:
        lyrics = [
            {"lyrics_index": 1, "text": "Hello, World!", "section": "Verse"},
            {"lyrics_index": 2, "text": "Again", "section": "Chorus"},
        ]
        video = [
            {"text": "Hello World", "video_start_ms": 1000, "confidence": 0.9},
            {"text": "Agaln", "video_start_ms": 2500, "confidence": 0.7},
        ]
        aligned = align_lyrics_to_video(lyrics, video, 500)
        self.assertEqual([cue["text"] for cue in aligned], ["Hello, World!", "Again"])
        self.assertEqual([cue["video_start_ms"] for cue in aligned], [1000, 2500])

    def test_alignment_interpolates_missing_video_line(self) -> None:
        lyrics = [
            {"lyrics_index": 1, "text": "one", "section": None},
            {"lyrics_index": 2, "text": "two", "section": None},
            {"lyrics_index": 3, "text": "three", "section": None},
        ]
        video = [
            {"text": "one", "video_start_ms": 1000, "confidence": 1.0},
            {"text": "three", "video_start_ms": 3000, "confidence": 1.0},
        ]
        aligned = align_lyrics_to_video(lyrics, video, 500)
        self.assertEqual(aligned[1]["video_start_ms"], 2000)
        self.assertIn("lyrics-line-interpolated-from-video", aligned[1]["flags"])


if __name__ == "__main__":
    unittest.main()
