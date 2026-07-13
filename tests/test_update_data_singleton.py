from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from update_data import UpdateAlreadyRunning, update_market_lock


class UpdateDataSingletonTests(unittest.TestCase):
    def test_same_market_cannot_acquire_twice(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "update_data.get_runtime_path",
            side_effect=lambda *parts, **_kwargs: Path(tmp).joinpath(*parts),
        ):
            with update_market_lock("US") as lock_path:
                self.assertTrue(lock_path.exists())
                with self.assertRaises(UpdateAlreadyRunning):
                    with update_market_lock("US"):
                        pass
            self.assertFalse(lock_path.exists())

    def test_markets_have_independent_locks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, patch(
            "update_data.get_runtime_path",
            side_effect=lambda *parts, **_kwargs: Path(tmp).joinpath(*parts),
        ):
            with update_market_lock("KR"), update_market_lock("US"):
                pass


if __name__ == "__main__":
    unittest.main()
