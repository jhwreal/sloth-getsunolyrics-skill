from __future__ import annotations

import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from process_song import timeline_is_reusable  # noqa: E402


class ProcessSongTests(unittest.TestCase):
    def setUp(self) -> None:
        self.payload = {
            "pipeline_fingerprint": "pipeline",
            "lyrics_sha256": "lyrics",
            "video_sha256": "video",
            "vocals_sha256": "vocals",
            "ocr_language": "en",
            "ocr_interval_ms": 500,
            "lyrics_comparison": {"requested_resolution": "ask"},
        }

    def reusable(self, **overrides: object) -> bool:
        arguments = {
            "pipeline_hash": "pipeline",
            "lyrics_hash": "lyrics",
            "video_hash": "video",
            "vocals_hash": "vocals",
            "language": "en",
            "interval_ms": 500,
            "lyrics_conflict_resolution": "ask",
        }
        arguments.update(overrides)
        return timeline_is_reusable(self.payload, **arguments)

    def test_reuses_only_exact_processing_identity(self) -> None:
        self.assertTrue(self.reusable())
        for key, value in [
            ("pipeline_hash", "changed"),
            ("lyrics_hash", "changed"),
            ("video_hash", "changed"),
            ("vocals_hash", "changed"),
            ("language", "zh"),
            ("interval_ms", 250),
            ("lyrics_conflict_resolution", "use-copied"),
        ]:
            with self.subTest(key=key):
                self.assertFalse(self.reusable(**{key: value}))


if __name__ == "__main__":
    unittest.main()
