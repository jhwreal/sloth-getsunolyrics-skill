from __future__ import annotations

import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from evaluate_timeline import align_cues  # noqa: E402


class EvaluateTimelineTests(unittest.TestCase):
    def test_alignment_does_not_shift_after_missing_generated_cue(self) -> None:
        generated = [{"text": "one"}, {"text": "three"}]
        gold = [{"text": "one"}, {"text": "two"}, {"text": "three"}]
        self.assertEqual(align_cues(generated, gold), [(0, 0), (1, 2)])


if __name__ == "__main__":
    unittest.main()
