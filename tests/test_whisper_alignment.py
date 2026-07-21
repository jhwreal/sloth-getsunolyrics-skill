from __future__ import annotations

import json
import sys
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from whisper_alignment import (  # noqa: E402
    align_lyrics_to_whisper,
    group_starts,
    select_start_times,
    transcribe_with_dtw,
    transcript_has_dtw,
)


def token(text: str, start_ms: int) -> dict:
    return {"text": text, "start_ms": start_ms, "dtw_available": True}


class WhisperAlignmentTests(unittest.TestCase):
    def test_maps_second_lyric_to_token_inside_merged_segment(self) -> None:
        lyrics = [
            {"text": "first line"},
            {"text": "second line"},
        ]
        text = "firstlinesecondline"
        segments = [
            {
                "raw_start_ms": 1_000,
                "dtw_start_ms": 1_050,
                "tokens": [
                    token(character, 1_050 + index * 100)
                    for index, character in enumerate(text)
                ],
            }
        ]
        starts = group_starts(lyrics, segments)
        self.assertEqual(starts[0]["dtw_start_ms"], 1_050)
        self.assertEqual(starts[1]["dtw_start_ms"], 1_950)

    def test_rejects_dtw_start_before_its_whisper_segment(self) -> None:
        cues = [
            {
                "text": "line",
                "section": None,
                "video_start_ms": 0,
                "confidence": 1.0,
                "text_source": "suno_lyrics_confirmed_by_video",
            }
        ]
        matches = [
            {
                "dtw_start_ms": 3_900,
                "raw_start_ms": 12_680,
                "dtw_available": True,
                "similarity": 0.9,
            }
        ]
        result = select_start_times(
            cues,
            matches,
            [12_700, 12_860],
            duration_ms=20_000,
        )[0]
        self.assertEqual(result["start_ms"], 12_860)
        self.assertIn("dtw-before-whisper-segment-rejected", result["flags"])

    def test_valid_dtw_start_is_not_replaced_by_nearby_energy_rise(self) -> None:
        cues = [
            {
                "text": "line",
                "section": None,
                "video_start_ms": 1_400,
                "confidence": 1.0,
                "text_source": "suno_lyrics_confirmed_by_video",
            }
        ]
        matches = [
            {
                "dtw_start_ms": 1_000,
                "raw_start_ms": 900,
                "dtw_available": True,
                "similarity": 1.0,
            }
        ]
        result = select_start_times(
            cues,
            matches,
            [1_500],
            duration_ms=5_000,
        )[0]
        self.assertEqual(result["start_ms"], 1_000)
        self.assertEqual(result["timing_source"], "whisper_dtw_token_start")

    def test_recovers_weak_first_match_from_leading_dtw_and_vocal_onset(self) -> None:
        cues = [
            {
                "text": "intro line",
                "section": "Intro",
                "video_start_ms": 0,
                "confidence": 1.0,
                "text_source": "suno_lyrics_confirmed_by_video",
            }
        ]
        matches = [
            {
                "dtw_start_ms": 20_000,
                "raw_start_ms": 20_000,
                "leading_weak_dtw_start_ms": 2_040,
                "dtw_available": True,
                "similarity": 0.4,
            }
        ]
        result = select_start_times(
            cues,
            matches,
            [1_960, 20_200],
            duration_ms=30_000,
        )[0]
        self.assertEqual(result["start_ms"], 1_960)
        self.assertEqual(
            result["timing_source"],
            "vocal_onset_near_leading_weak_dtw",
        )
        self.assertIn(
            "weak-first-whisper-match-recovered-from-leading-dtw",
            result["flags"],
        )

    def test_recovers_earlier_line_when_two_lines_share_a_whisper_start(self) -> None:
        cues = [
            {
                "text": "long repeated line",
                "section": None,
                "video_start_ms": 154_500,
                "confidence": 1.0,
                "text_source": "suno_lyrics_confirmed_by_video",
            },
            {
                "text": "short repeated line",
                "section": None,
                "video_start_ms": 160_000,
                "confidence": 1.0,
                "text_source": "suno_lyrics_confirmed_by_video",
            },
        ]
        matches = [
            {
                "dtw_start_ms": 157_520,
                "raw_start_ms": 157_120,
                "dtw_available": True,
                "similarity": 0.7,
            },
            {
                "dtw_start_ms": 157_520,
                "raw_start_ms": 157_120,
                "dtw_available": True,
                "similarity": 1.0,
            },
        ]
        results = select_start_times(
            cues,
            matches,
            [],
            duration_ms=170_000,
        )
        self.assertEqual(results[0]["start_ms"], 154_500)
        self.assertEqual(results[1]["start_ms"], 157_520)
        self.assertIn(
            "duplicate-whisper-start-recovered-from-video-anchor",
            results[0]["flags"],
        )

    def test_duplicate_transcript_segment_uses_dtw_time_prior(self) -> None:
        lyrics = [{"text": "same words"}]
        transcript = {
            "transcription": [
                {
                    "text": "same words",
                    "offsets": {"from": 0, "to": 2_000},
                    "tokens": [
                        {"text": "same", "t_dtw": 100, "offsets": {"from": 0}},
                        {"text": " words", "t_dtw": 140, "offsets": {"from": 500}},
                    ],
                },
                {
                    "text": "same words",
                    "offsets": {"from": 900, "to": 6_000},
                    "tokens": [
                        {"text": "same", "t_dtw": 500, "offsets": {"from": 900}},
                        {"text": " words", "t_dtw": 540, "offsets": {"from": 1_400}},
                    ],
                },
            ]
        }
        matches = align_lyrics_to_whisper(
            lyrics,
            transcript,
            [{"video_start_ms": 1_000}],
            duration_ms=10_000,
        )
        self.assertEqual(matches[0]["dtw_start_ms"], 1_000)

    def test_detects_real_dtw_tokens(self) -> None:
        self.assertTrue(
            transcript_has_dtw(
                {
                    "transcription": [
                        {"tokens": [{"text": "word", "t_dtw": 12}]}
                    ]
                }
            )
        )
        self.assertFalse(
            transcript_has_dtw(
                {
                    "transcription": [
                        {"tokens": [{"text": "word", "t_dtw": -1}]}
                    ]
                }
            )
        )

    def test_transcriber_disables_flash_attention_for_real_dtw(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cli = root / "whisper-cli"
            cli.write_bytes(b"test executable")
            cli.chmod(0o755)
            model = root / "ggml-large-v3.bin"
            model.write_bytes(b"test model")
            vocals = root / "vocals.wav"
            vocals.write_bytes(b"test vocals")
            seen_command = []

            def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess:
                seen_command.extend(command)
                prefix = Path(command[command.index("-of") + 1])
                prefix.with_suffix(".json").write_text(
                    json.dumps(
                        {
                            "transcription": [
                                {
                                    "text": "hello",
                                    "tokens": [{"text": "hello", "t_dtw": 12}],
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(command, 0, "", "")

            with patch("whisper_alignment.subprocess.run", side_effect=fake_run):
                transcribe_with_dtw(
                    vocals,
                    [{"text": "hello"}],
                    root / "work",
                    whisper_cli=cli,
                    whisper_model=model,
                    threads=1,
                )
            self.assertIn("-nfa", seen_command)
            self.assertEqual(seen_command[seen_command.index("-dtw") + 1], "large.v3")
            self.assertEqual(seen_command[seen_command.index("-mc") + 1], "0")


if __name__ == "__main__":
    unittest.main()
