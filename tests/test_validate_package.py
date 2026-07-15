from __future__ import annotations

import sys
from pathlib import Path
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from validate_package import parse_csv_time  # noqa: E402


class ValidatePackageTests(unittest.TestCase):
    def test_parse_csv_time(self) -> None:
        self.assertEqual(parse_csv_time("00:00.000"), 0)
        self.assertEqual(parse_csv_time("61:02.345"), 3_662_345)

    def test_parse_csv_time_rejects_invalid_values(self) -> None:
        for value in ["1:02.345", "00:60.000", "00:01,000"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_csv_time(value)


if __name__ == "__main__":
    unittest.main()
