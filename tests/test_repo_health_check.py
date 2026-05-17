from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tools.repo_health_check import check_json_strict_parse


class RepoHealthCheckTests(unittest.TestCase):
    def test_json_strict_parse_treats_archive_corrupt_as_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state" / "archive").mkdir(parents=True)
            (root / "state" / "brain.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
            (root / "state" / "archive" / "old_corrupt.json").write_text("{bad", encoding="utf-8")

            result = check_json_strict_parse(root)

        self.assertEqual(result.status, "WARN")
        self.assertIn("archive_errors=1", result.detail)

    def test_json_strict_parse_fails_active_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state").mkdir(parents=True)
            (root / "state" / "brain.json").write_text("{bad", encoding="utf-8")

            result = check_json_strict_parse(root)

        self.assertEqual(result.status, "FAIL")
        self.assertIn("active_errors=1", result.detail)


if __name__ == "__main__":
    unittest.main()

