from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tools.live_preflight import _runtime_handoff_cache_hygiene_check


def _write_snapshot(root: Path, anchors: dict, features: dict) -> None:
    state = root / "state"
    state.mkdir(parents=True, exist_ok=True)
    (state / "live_runtime_handoff_snapshot.json").write_text(
        json.dumps(
            {
                "mode": "live",
                "written_at": "2026-07-16T12:00:00+09:00",
                "session_dates": {"KR": "2026-07-16", "US": "2026-07-15"},
                "fields": {
                    "_post_open_anchor": anchors,
                    "_last_post_open_features_by_ticker": features,
                },
            }
        ),
        encoding="utf-8",
    )


def test_runtime_handoff_hygiene_flags_cross_session_rows(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        {
            "KR:005930": {"anchor_at": "2026-07-15T09:00:00"},
            "US:AAPL": {"anchor_at": "2026-07-16T01:30:00+09:00"},
        },
        {
            "KR": {
                "005930": {
                    "market": "KR",
                    "session_date": "2026-07-16",
                    "anchor_at": "2026-07-15T09:00:00",
                }
            },
            "US": {},
        },
    )

    with patch("runtime_paths._RUNTIME_ROOT", tmp_path):
        result = _runtime_handoff_cache_hygiene_check("live")

    assert result.status == "WARN"
    assert result.data["stale_row_count"] == 2
    assert result.data["restore_is_fail_closed"] is True


def test_runtime_handoff_hygiene_accepts_us_kst_cross_midnight(tmp_path: Path) -> None:
    _write_snapshot(
        tmp_path,
        {
            "KR:005930": {"anchor_at": "2026-07-16T09:00:00+09:00"},
            "US:AAPL": {"anchor_at": "2026-07-16T01:30:00+09:00"},
        },
        {
            "KR": {
                "005930": {
                    "market": "KR",
                    "session_date": "2026-07-16",
                    "anchor_at": "2026-07-16T09:00:00+09:00",
                    "known_at": "2026-07-16T09:05:00+09:00",
                }
            },
            "US": {
                "AAPL": {
                    "market": "US",
                    "session_date": "2026-07-15",
                    "anchor_at": "2026-07-16T01:30:00+09:00",
                    "known_at": "2026-07-16T03:00:00+09:00",
                }
            },
        },
    )

    with patch("runtime_paths._RUNTIME_ROOT", tmp_path):
        result = _runtime_handoff_cache_hygiene_check("live")

    assert result.status == "PASS"
    assert result.data["stale_row_count"] == 0
