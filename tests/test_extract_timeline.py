from __future__ import annotations

import sys
from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from extract_timeline import (  # noqa: E402
    align_lyrics_to_video,
    build_video_cues,
    extract_ocr,
    filter_video_cues_for_lyrics,
    frame_candidate,
    parse_lyrics,
    same_text,
    sha256,
    VISION_SOURCE,
)


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

    def test_alignment_rejects_completely_unrelated_ocr(self) -> None:
        lyrics = [{"lyrics_index": 1, "text": "canonical lyric", "section": None}]
        video = [{"text": "totally unrelated", "video_start_ms": 1000, "confidence": 1.0}]
        with self.assertRaises(SystemExit):
            align_lyrics_to_video(lyrics, video, 500)

    def test_similar_but_distinct_lyrics_are_not_deduplicated(self) -> None:
        self.assertFalse(
            same_text("Turn questions into starlight", "Turn answers into starlight")
        )
        self.assertFalse(same_text("Fireseed", "Hold the fireseed tonight"))
        self.assertTrue(same_text("Soft piano warm", "Soft piano warm and bright"))

    def test_trailing_interpolation_uses_line_spacing_not_frame_interval(self) -> None:
        lyrics = [
            {"lyrics_index": index, "text": text, "section": None}
            for index, text in enumerate(["one", "two", "three", "four"], 1)
        ]
        video = [
            {"text": "one", "video_start_ms": 1000, "confidence": 1.0},
            {"text": "two", "video_start_ms": 4000, "confidence": 1.0},
        ]
        aligned = align_lyrics_to_video(lyrics, video, 500, duration_ms=13_000)
        self.assertEqual([cue["video_start_ms"] for cue in aligned], [1000, 4000, 7000, 10_000])

    def test_sample_song_name_is_not_hard_coded_as_noise(self) -> None:
        candidate = frame_candidate(
            {
                "timeMs": 1000,
                "observations": [
                    {
                        "text": "Fireseed lights the way",
                        "confidence": 0.95,
                        "x": 0.2,
                        "y": 0.38,
                        "width": 0.6,
                        "height": 0.04,
                        "brightRatio": 0.8,
                    }
                ],
            }
        )
        self.assertEqual(candidate["text"], "Fireseed lights the way")

    def test_distant_repeated_video_line_is_preserved(self) -> None:
        def frame(time_ms: int, text: str) -> dict:
            return {
                "timeMs": time_ms,
                "observations": [
                    {
                        "text": text,
                        "confidence": 0.9,
                        "x": 0.2,
                        "y": 0.38,
                        "width": 0.6,
                        "height": 0.04,
                        "brightRatio": 0.8,
                    }
                ],
            }

        cues = build_video_cues(
            [
                frame(0, "repeat this lyric line"),
                frame(2000, "a different lyric line"),
                frame(4000, "another distinct lyric line"),
                frame(8000, "repeat this lyric line"),
            ],
            500,
        )
        self.assertEqual([cue["text"] for cue in cues].count("repeat this lyric line"), 2)

    def test_generic_lyrics_filter_removes_title_without_hard_coding(self) -> None:
        lyrics = [{"text": "the actual lyric", "section": None}]
        video = [
            {"text": "Unrelated Song Title", "video_start_ms": 0},
            {"text": "the actual lyric", "video_start_ms": 1000},
        ]
        self.assertEqual(filter_video_cues_for_lyrics(lyrics, video), [video[1]])

    def test_valid_ocr_cache_is_reused_without_media_tools(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            video = root / "song.mp4"
            video.write_bytes(b"cache identity only")
            observation = {
                "timeMs": 0,
                "observations": [{"text": "hello", "confidence": 1.0}],
            }
            (root / "ocr.jsonl").write_text(
                json.dumps(observation) + "\n", encoding="utf-8"
            )
            ocr_path = root / "ocr.jsonl"
            (root / "ocr.cache.json").write_text(
                json.dumps(
                    {
                        "cache_version": 2,
                        "video_sha256": sha256(video),
                        "interval_ms": 500,
                        "language": "en",
                        "vision_source_sha256": sha256(VISION_SOURCE),
                        "frame_count": 1,
                        "ocr_sha256": sha256(ocr_path),
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(extract_ocr(video, root, 0.5, "en"), [observation])
            ocr_path.write_text(json.dumps(observation) + "\n{}\n", encoding="utf-8")
            with patch("extract_timeline.run", side_effect=RuntimeError("fresh OCR required")):
                with self.assertRaisesRegex(RuntimeError, "fresh OCR required"):
                    extract_ocr(video, root, 0.5, "en")


if __name__ == "__main__":
    unittest.main()
