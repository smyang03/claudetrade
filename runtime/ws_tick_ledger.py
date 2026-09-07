# -*- coding: utf-8 -*-
"""KR WS 관측 전용 틱 원장 (2026-09-08, Codex N2 매도 체결 흡수).

봇의 KISWebSocket이 관측 전용 종목(state/ws_observe_kr.json, 오늘 날짜)의 H0STCNT0 원시 문자열을 `tick_sink`로 넘긴다.
- 매매 경로와 완전 분리: 여기서는 파싱하지 않고(필드 번호 실수 → 라이브 영향 방지) 수신 시각만 붙여 버퍼에 쌓는다.
- 창: 틱 시각(필드 1, HHMMSS) < 09:40만 저장(N2 창 09:05~09:20 + 여유). 200행 또는 2초마다 flush. 예외는 삼키고 카운터만.
- 파일: data/shadow/kr_ws_ticks/<YYYY-MM-DD>.jsonl — 한 행 {"rx": 수신 ISO, "raw": 원시 문자열}. 파싱은 tools/kr_absorption_shadow.py(스펙 기준).
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TICK_DIR = ROOT / "data" / "shadow" / "kr_ws_ticks"
OBSERVE_FILE = ROOT / "state" / "ws_observe_kr.json"
WINDOW_UNTIL_HHMMSS = "094000"
FLUSH_ROWS = 200
FLUSH_SEC = 2.0
_BUF: list[str] = []
_LOCK = threading.Lock()
_LAST_FLUSH = [time.time()]
STATS = {"received": 0, "kept": 0, "dropped_window": 0, "errors": 0, "flushed": 0}


def load_observe_list() -> list[str]:
    """오늘 날짜의 관측 목록. 파일 없음·날짜 불일치·오류 → []."""
    try:
        d = json.loads(OBSERVE_FILE.read_text(encoding="utf-8"))
        if str(d.get("date")) != date.today().isoformat():
            return []
        return [str(t).strip() for t in (d.get("tickers") or []) if str(t).strip()]
    except Exception:  # noqa: BLE001
        return []


def tick_sink(raw: str) -> None:
    """WS 콜백 스레드에서 호출 — 절대 예외를 올리지 않고, 파일 I/O는 flush 주기에만."""
    try:
        STATS["received"] += 1
        parts = raw.split("^", 2)
        if len(parts) < 2 or parts[1][:6] >= WINDOW_UNTIL_HHMMSS:
            STATS["dropped_window"] += 1
            return
        line = json.dumps({"rx": datetime.now().isoformat(timespec="milliseconds"), "raw": raw}, ensure_ascii=False)
        with _LOCK:
            _BUF.append(line); STATS["kept"] += 1
            due = len(_BUF) >= FLUSH_ROWS or (time.time() - _LAST_FLUSH[0]) >= FLUSH_SEC
        if due:
            flush()
    except Exception:  # noqa: BLE001
        STATS["errors"] += 1


def flush(force: bool = False) -> int:
    try:
        with _LOCK:
            if not _BUF:
                return 0
            rows = list(_BUF); _BUF.clear(); _LAST_FLUSH[0] = time.time()
        TICK_DIR.mkdir(parents=True, exist_ok=True)
        with (TICK_DIR / f"{date.today().isoformat()}.jsonl").open("a", encoding="utf-8") as fh:
            fh.write("\n".join(rows) + "\n")
        STATS["flushed"] += len(rows)
        return len(rows)
    except Exception:  # noqa: BLE001
        STATS["errors"] += 1
        return 0
