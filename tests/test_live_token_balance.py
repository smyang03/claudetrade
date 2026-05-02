from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from tools.live_preflight import _token_checks


ROOT = Path(__file__).resolve().parents[1]


class LiveTokenBalanceTests(unittest.TestCase):
    def test_token_refresh_helper_is_wired_in_startup_balance(self) -> None:
        source = (ROOT / "trading_bot.py").read_text(encoding="utf-8", errors="replace")

        self.assertIn("_get_balance_with_token_refresh", source)
        self.assertIn("get_access_token(force_refresh=True, market=", source)
        self.assertIn("force_refresh=True", source)

    def test_live_token_file_is_parseable_when_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state_dir = root / "state"
            state_dir.mkdir(parents=True)
            token_path = state_dir / "live_kis_token.json"
            token_path.write_text(
                json.dumps(
                    {
                        "access_token": "test-token",
                        "issued_at": datetime.now().isoformat(),
                        "expires_at": (datetime.now() + timedelta(hours=4)).isoformat(),
                        "context": {},
                    }
                ),
                encoding="utf-8",
            )

            with patch("tools.live_preflight.ROOT", root), patch(
                "tools.live_preflight._repo_text",
                return_value="_get_balance_with_token_refresh get_access_token(force_refresh=True, market='KR')",
            ):
                checks = _token_checks("live")
        failing = [item for item in checks if item.status == "FAIL"]

        self.assertEqual(failing, [])


if __name__ == "__main__":
    unittest.main()
