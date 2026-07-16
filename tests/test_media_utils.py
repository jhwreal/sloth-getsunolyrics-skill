from __future__ import annotations

import sys
from pathlib import Path
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from media_utils import validate_media_pair  # noqa: E402


class MediaUtilsTests(unittest.TestCase):
    def test_accepts_aligned_video_and_audio_stem(self) -> None:
        video = {"duration_ms": 10_000, "has_video": True, "has_audio": True}
        vocals = {"duration_ms": 10_020, "has_video": False, "has_audio": True}
        with patch("media_utils.probe_media", side_effect=[video, vocals]):
            self.assertEqual(
                validate_media_pair(Path("song.mp4"), Path("vocals.wav")),
                (video, vocals),
            )

    def test_rejects_duration_mismatch_before_processing(self) -> None:
        video = {"duration_ms": 10_000, "has_video": True, "has_audio": True}
        vocals = {"duration_ms": 12_000, "has_video": False, "has_audio": True}
        with patch("media_utils.probe_media", side_effect=[video, vocals]), self.assertRaises(
            SystemExit
        ):
            validate_media_pair(Path("song.mp4"), Path("vocals.wav"))

    def test_rejects_video_without_audio(self) -> None:
        video = {"duration_ms": 10_000, "has_video": True, "has_audio": False}
        vocals = {"duration_ms": 10_000, "has_video": False, "has_audio": True}
        with patch("media_utils.probe_media", side_effect=[video, vocals]), self.assertRaises(
            SystemExit
        ):
            validate_media_pair(Path("song.mp4"), Path("vocals.wav"))


if __name__ == "__main__":
    unittest.main()
