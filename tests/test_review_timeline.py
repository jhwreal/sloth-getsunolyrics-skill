from __future__ import annotations

import csv
import json
from pathlib import Path
import sys
from threading import Thread
import tempfile
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from review_timeline import (  # noqa: E402
    ReviewError,
    ReviewServer,
    RevisionConflict,
    TimelineStore,
    discover_package_dirs,
)


class ReviewTimelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "song-package"
        self.root.mkdir()
        (self.root / "song.mp4").write_bytes(b"0123456789abcdefghijklmnopqrstuvwxyz")
        timeline = {
            "schema_version": 4,
            "video": "song.mp4",
            "media_duration_ms": 5_000,
            "alignment_summary": {
                "lyrics_count": 2,
                "whisper_match_count": 2,
                "dtw_token_start_count": 2,
                "rejected_dtw_backtrack_count": 0,
                "leading_weak_dtw_recovery_count": 0,
                "duplicate_whisper_start_recovery_count": 0,
            },
            "cues": [
                {
                    "index": 1,
                    "section": "Verse",
                    "text": "first line",
                    "start_ms": 1_000,
                    "timing_source": "whisper_dtw_token_start",
                    "flags": [],
                },
                {
                    "index": 2,
                    "section": "Verse",
                    "text": "second line",
                    "start_ms": 2_000,
                    "timing_source": "whisper_dtw_token_start",
                    "flags": [],
                },
            ],
        }
        (self.root / "timeline.json").write_text(
            json.dumps(timeline), encoding="utf-8"
        )
        (self.root / "manifest.json").write_text(
            json.dumps(
                {
                    "schema_version": 5,
                    "title": "Test Song",
                    "video": "song.mp4",
                    "timeline": "timeline.json",
                    "alignment_summary": timeline["alignment_summary"],
                }
            ),
            encoding="utf-8",
        )
        self.store = TimelineStore([self.root], validate_on_save=False)
        self.song_id = next(iter(self.store.songs))

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_save_preserves_original_and_regenerates_exports(self) -> None:
        detail = self.store.detail(self.song_id)
        result = self.store.save(
            self.song_id,
            starts=[1_000, 2_250],
            revision=detail["revision"],
            finalize=False,
        )
        saved = json.loads((self.root / "timeline.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["cues"][1]["start_ms"], 2_250)
        self.assertEqual(
            saved["cues"][1]["timing_source"],
            "human_reviewed_player_position",
        )
        self.assertEqual(saved["cues"][1]["automatic_start_ms"], 2_000)
        self.assertIn("human-reviewed-start", saved["cues"][1]["flags"])
        self.assertEqual(saved["alignment_summary"]["human_reviewed_start_count"], 1)
        self.assertEqual(saved["alignment_summary"]["dtw_token_start_count"], 1)
        original = json.loads(
            (self.root / "review" / "original" / "timeline.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(original["cues"][1]["start_ms"], 2_000)
        with (self.root / "timeline.csv").open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        self.assertEqual(rows[1]["start_time"], "00:02.250")
        self.assertEqual(result["review"]["status"], "in_progress")
        self.assertEqual(result["review"]["edited_count"], 1)

        finalized = self.store.save(
            self.song_id,
            starts=[1_000, 2_250],
            revision=result["revision"],
            finalize=True,
        )
        self.assertEqual(finalized["review"]["status"], "finalized")
        self.assertTrue(finalized["review"]["finalized_at"])

    def test_rejects_non_monotonic_and_stale_updates(self) -> None:
        detail = self.store.detail(self.song_id)
        with self.assertRaises(ReviewError):
            self.store.save(
                self.song_id,
                starts=[1_000, 1_000],
                revision=detail["revision"],
                finalize=False,
            )
        with self.assertRaises(RevisionConflict):
            self.store.save(
                self.song_id,
                starts=[1_000, 2_100],
                revision="stale",
                finalize=False,
            )

    def test_discovery_ignores_review_and_work_copies(self) -> None:
        review_copy = self.root / "review" / "original"
        review_copy.mkdir(parents=True)
        (review_copy / "timeline.json").write_text("{}", encoding="utf-8")
        work_copy = self.root / "work" / "timeline"
        work_copy.mkdir(parents=True)
        (work_copy / "timeline.json").write_text("{}", encoding="utf-8")
        self.assertEqual(
            discover_package_dirs([], [Path(self.temporary.name)]),
            [self.root.resolve()],
        )

    def test_http_lists_songs_serves_ranges_and_saves(self) -> None:
        try:
            server = ReviewServer(("127.0.0.1", 0), self.store)
        except PermissionError:
            self.skipTest("local socket binding is disabled in this sandbox")
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            with urlopen(f"{base}/api/songs") as response:
                listing = json.load(response)
            self.assertEqual(listing["songs"][0]["title"], "Test Song")

            request = Request(
                f"{base}/api/songs/{self.song_id}/media",
                headers={"Range": "bytes=3-7"},
            )
            with urlopen(request) as response:
                self.assertEqual(response.status, 206)
                self.assertEqual(response.read(), b"34567")

            detail = self.store.detail(self.song_id)
            body = json.dumps(
                {
                    "revision": detail["revision"],
                    "starts": [1_100, 2_100],
                    "finalize": False,
                }
            ).encode("utf-8")
            request = Request(
                f"{base}/api/songs/{self.song_id}/timeline",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urlopen(request) as response:
                result = json.load(response)
            self.assertTrue(result["ok"])

            stale_request = Request(
                f"{base}/api/songs/{self.song_id}/timeline",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with self.assertRaises(HTTPError) as context:
                urlopen(stale_request)
            self.assertEqual(context.exception.code, 409)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
