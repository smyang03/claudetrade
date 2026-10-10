"""dart_insider_reason — 본문 조회 실패는 원장에 박제하지 않고 다음 실행에서 재시도 (네트워크 없음)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import dart_insider_reason as R  # noqa: E402
import runtime.kr_event_lane as L  # noqa: E402


def test_failed_fetch_is_retried_not_recorded(tmp_path, monkeypatch):
    src, out = tmp_path / "src.jsonl", tmp_path / "out.jsonl"
    src.write_text("\n".join(json.dumps({"rcept_no": n, "stock": "000640", "rcept_dt": "2026-10-08", "irds_cnt": 1})
                             for n in ("A", "B")), encoding="utf-8")
    monkeypatch.setattr(R, "SRC", src)
    monkeypatch.setattr(R, "OUT", out)
    monkeypatch.setattr(R, "SLEEP", 0)
    monkeypatch.setattr(R, "_env_key", lambda: None)
    monkeypatch.setattr(L, "dart_document_text", lambda rn, **k: "" if rn == "B" else "장내매수 취득")
    R.main()
    assert [json.loads(x)["rcept_no"] for x in out.read_text(encoding="utf-8").splitlines()] == ["A"]
    monkeypatch.setattr(L, "dart_document_text", lambda rn, **k: "장내매수 취득")
    R.main()
    assert sorted(json.loads(x)["rcept_no"] for x in out.read_text(encoding="utf-8").splitlines()) == ["A", "B"]
