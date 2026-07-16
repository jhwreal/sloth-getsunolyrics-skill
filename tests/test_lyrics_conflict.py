from __future__ import annotations

import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from lyrics_conflict import compare_lyrics_to_video, render_markdown  # noqa: E402


def lyric(text: str, index: int) -> dict:
    return {"lyrics_index": index, "text": text, "section": None}


def video(
    text: str,
    time_ms: int,
    *,
    confidence: float = 0.95,
    sample_count: int = 3,
) -> dict:
    return {
        "text": text,
        "video_start_ms": time_ms,
        "confidence": confidence,
        "sample_count": sample_count,
    }


class LyricsConflictTests(unittest.TestCase):
    def test_matching_lyrics_continue_original_workflow(self) -> None:
        report = compare_lyrics_to_video(
            [lyric("Hello, World!", 1), lyric("再次出发", 2)],
            [video("hello world", 1000), video("再次出发", 3000)],
        )
        self.assertEqual(report["status"], "matched")
        self.assertFalse(report["requires_user_decision"])
        self.assertEqual(report["difference_count"], 0)

    def test_weak_ocr_typo_does_not_interrupt_normal_flow(self) -> None:
        report = compare_lyrics_to_video(
            [lyric("Again", 1)],
            [video("Agaln", 1000, confidence=0.70, sample_count=1)],
        )
        self.assertFalse(report["requires_user_decision"])
        self.assertEqual(report["uncertain_items"][0]["kind"], "possible_ocr_error")

    def test_high_evidence_changed_word_requires_decision(self) -> None:
        report = compare_lyrics_to_video(
            [
                lyric("We carry questions into starlight", 1),
                lyric("And find our way home", 2),
            ],
            [
                video("We carry answers into starlight", 1000),
                video("And find our way home", 4000),
            ],
        )
        self.assertTrue(report["requires_user_decision"])
        difference = report["differences"][0]
        self.assertEqual(difference["kind"], "text_changed")
        self.assertEqual(difference["copied_line"], 1)
        self.assertEqual(difference["video_time"], "00:01.000")
        self.assertIn(
            {"operation": "replace", "copied": "questions", "video": "answers"},
            difference["changes"],
        )

    def test_high_evidence_chinese_change_requires_decision(self) -> None:
        report = compare_lyrics_to_video(
            [lyric("点亮心中的火种", 1)],
            [video("点亮夜空的火种", 2500)],
        )
        self.assertTrue(report["requires_user_decision"])
        self.assertEqual(report["differences"][0]["copied_text"], "点亮心中的火种")

    def test_video_only_line_between_matches_requires_decision(self) -> None:
        report = compare_lyrics_to_video(
            [lyric("first line", 1), lyric("last line", 2)],
            [
                video("first line", 1000),
                video("a newly generated line", 2500),
                video("last line", 4000),
            ],
        )
        self.assertTrue(report["requires_user_decision"])
        self.assertEqual(report["differences"][0]["kind"], "video_only_line")

    def test_local_scroll_duplicate_does_not_become_a_new_lyric(self) -> None:
        report = compare_lyrics_to_video(
            [
                lyric("first line", 1),
                lyric("repeat me", 2),
                lyric("a transition line", 3),
                lyric("last line", 4),
            ],
            [
                video("first line", 1000),
                video("repeat me", 3000, sample_count=6),
                video("a transition line", 5000),
                video("repeat me", 6500, sample_count=3),
                video("last line", 8000),
            ],
        )
        self.assertFalse(report["requires_user_decision"])
        self.assertTrue(
            any(
                item["kind"] == "possible_video_decoration"
                for item in report["uncertain_items"]
            )
        )

    def test_single_leading_video_title_is_not_treated_as_lyrics(self) -> None:
        report = compare_lyrics_to_video(
            [lyric("the real lyric", 1)],
            [video("Song Title", 0), video("the real lyric", 2000)],
        )
        self.assertFalse(report["requires_user_decision"])
        self.assertEqual(report["uncertain_items"][0]["kind"], "possible_video_decoration")

    def test_markdown_explains_difference_and_user_choices(self) -> None:
        report = compare_lyrics_to_video(
            [lyric("we choose morning", 1)],
            [video("we choose midnight", 12_340)],
        )
        rendered = render_markdown(report)
        self.assertIn("00:12.340", rendered)
        self.assertIn("we choose morning", rendered)
        self.assertIn("we choose midnight", rendered)
        self.assertIn("不得静默选择", rendered)


if __name__ == "__main__":
    unittest.main()
