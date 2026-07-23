from __future__ import annotations

"""mfe_time/mae_time 백필 — 분봉으로 '우리 보유창 안'의 고점/저점 시각을 복원한다.

왜 필요한가 (2026-07-23 데이터 흐름 점검):
  mfe_time 0/318. 라이브 excursion 시각(observed_peak_at)이 원장에 안 실렸다.
  그 결과 capture(= net / MFE)를 계산하는 전 분석이 무효였다 — mfe_pct 는 일봉
  backfill(그날 고/저)이라 우리 보유 중 고점이 아니었기 때문이다.

  일봉 backfill 과의 결정적 차이: 이 도구는 **진입~청산 구간의 분봉**만 읽어
  그 창 안의 최고가/최저가와 **그 시각**을 쓴다. 즉 우리 보유기간 고점이 맞다.
  그래서 mfe_time 이 채워지고 → audit/mfe_trust 게이트를 통과 → capture 유효.

  이게 살아나야 "MILD_BEAR 안에서 좋은 후보"처럼 국면 안 변별을 데이터로 볼 수 있다.

무엇을 채우나:
  mfe_pct  = (창내 최고가/진입가 - 1)*100   (일봉값보다 정확 — 보유창 한정)
  mae_pct  = (창내 최저가/진입가 - 1)*100
  mfe_time = 최고가 분봉 ts
  mae_time = 최저가 분봉 ts
  mfe_source = 'minute_window_backfill'

주의:
  - entry_at/closed_at 은 UTC(+00:00), 분봉 ts 는 KST(+09:00). 반드시 tz-aware 로 파싱한다
    (문자열 비교 금지 — 2026-07-23 반복 함정).
  - 분봉이 그 창을 커버 못 하면 skip(억지로 채우지 않는다).
  - 기존 mfe_time 있는 행은 무접촉(멱등). learning·canonical 양쪽 채운다.
  - 기본 dry-run. --apply 로 기록(백업 자동).

  python tools/backfill_mfe_time_from_minute.py
  python tools/backfill_mfe_time_from_minute.py --apply
"""

import argparse
import csv
import io
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ML_DB = ROOT / "data" / "ml" / "decisions.db"
MINUTE = {"KR": ROOT / "data" / "price" / "minute" / "kr",
          "US": ROOT / "data" / "price" / "minute" / "us"}


def _parse_dt(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _minute_path(market: str, ticker: str) -> Path | None:
    d = MINUTE.get(market)
    if not d or not d.exists():
        return None
    prefix = "kr" if market == "KR" else "us"
    tk = str(ticker)
    for name in (f"{prefix}_{tk}.csv", f"{prefix}_{tk.upper()}.csv"):
        p = d / name
        if p.exists():
            return p
    return None


def _window_extremes(path: Path, start: datetime, end: datetime):
    """[start,end] 창 안 최고가(+시각)·최저가(+시각). 분봉 부족하면 None."""
    hi_px = lo_px = None
    hi_at = lo_at = None
    n = 0
    try:
        with io.open(path, encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                ts = _parse_dt(row.get("ts") or row.get("﻿ts"))
                if ts is None or ts < start or ts > end:
                    continue
                try:
                    high = float(row["high"]); low = float(row["low"])
                except (KeyError, TypeError, ValueError):
                    continue
                n += 1
                if hi_px is None or high > hi_px:
                    hi_px, hi_at = high, ts
                if lo_px is None or low < lo_px:
                    lo_px, lo_at = low, ts
    except OSError:
        return None
    if n == 0:
        return None
    return hi_px, hi_at, lo_px, lo_at, n


def main() -> int:
    ap = argparse.ArgumentParser(description="mfe_time 분봉 백필")
    ap.add_argument("--since", default="2026-05-01")
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    if not ML_DB.exists():
        print("ML DB 없음")
        return 1
    con = sqlite3.connect(f"file:{ML_DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT v2_decision_id, market, ticker, earliest_fill_at, last_closed_at, entry_price, pnl_pct "
        "FROM v2_canonical_performance WHERE closed=1 AND (mfe_time IS NULL OR mfe_time='') "
        "AND earliest_fill_at IS NOT NULL AND last_closed_at IS NOT NULL AND entry_price IS NOT NULL "
        "AND session_date>=?", (args.since,)).fetchall()
    con.close()
    print(f"=== mfe_time 분봉 백필 (since {args.since}) ===")
    print(f"대상 {len(rows)}건 · 모드 {'APPLY' if args.apply else 'DRY-RUN'}\n")

    updates = []
    skip = {"no_file": 0, "no_bars": 0, "bad_price": 0}
    for r in rows:
        market = "US" if str(r["market"]).upper() == "US" else "KR"
        path = _minute_path(market, r["ticker"])
        if path is None:
            skip["no_file"] += 1
            continue
        start = _parse_dt(r["earliest_fill_at"]); end = _parse_dt(r["last_closed_at"])
        entry = float(r["entry_price"] or 0)
        if start is None or end is None or entry <= 0:
            skip["bad_price"] += 1
            continue
        ext = _window_extremes(path, start, end)
        if ext is None:
            skip["no_bars"] += 1
            continue
        hi_px, hi_at, lo_px, lo_at, nbars = ext
        mfe = (hi_px / entry - 1.0) * 100.0
        mae = (lo_px / entry - 1.0) * 100.0
        # ★ 정합성 가드: 실현 gross 는 반드시 [mae, mfe] 밴드 안이어야 한다.
        #   밖이면 분봉이 실제 청산 순간을 놓쳤거나 entry_price 기준이 달라 창이 불완전하다
        #   — 그런 mfe 를 쓰면 capture>100% 같은 물리적 불가능이 나온다. skip.
        gross = r["pnl_pct"]
        if gross is not None:
            g = float(gross)
            tol = 0.15  # 분봉 고/저와 체결가 미세차 허용
            if g > mfe + tol or g < mae - tol:
                skip["realized_outside_band"] = skip.get("realized_outside_band", 0) + 1
                continue
        updates.append({
            "did": r["v2_decision_id"],
            "mfe_pct": round(mfe, 4), "mae_pct": round(mae, 4),
            "mfe_time": hi_at.isoformat(timespec="seconds"),
            "mae_time": lo_at.isoformat(timespec="seconds"),
            "ticker": r["ticker"], "market": market, "nbars": nbars,
        })

    print(f"복원 가능 {len(updates)}건 · skip {skip}")
    for u in updates[:8]:
        print(f"  {u['market']} {u['ticker']:8s} mfe {u['mfe_pct']:+.2f}%@{u['mfe_time'][11:16]} "
              f"mae {u['mae_pct']:+.2f}%@{u['mae_time'][11:16]} (분봉 {u['nbars']})")
    if len(updates) > 8:
        print(f"  ... 외 {len(updates)-8}건")

    if not args.apply:
        print("\n[dry-run] 실제 기록은 --apply")
        return 0

    backup = ML_DB.with_suffix(f".db.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(ML_DB, backup)
    print(f"\n백업: {backup.name}")
    w = sqlite3.connect(ML_DB, timeout=60)
    w.execute("PRAGMA busy_timeout=50000")
    # mfe_source 컬럼이 있으면 표식(없으면 생략)
    has_src = {"v2_learning_performance": False, "v2_canonical_performance": False}
    for t in has_src:
        cols = {c[1] for c in w.execute(f"PRAGMA table_info({t})")}
        has_src[t] = "mfe_source" in cols
    n = 0
    for u in updates:
        for t in ("v2_learning_performance", "v2_canonical_performance"):
            sets = ("mfe_pct=?, mae_pct=?, mfe_time=?, mae_time=?"
                    + (", mfe_source=?" if has_src[t] else ""))
            params = [u["mfe_pct"], u["mae_pct"], u["mfe_time"], u["mae_time"]]
            if has_src[t]:
                params.append("minute_window_backfill")
            params.append(u["did"])
            w.execute(f"UPDATE {t} SET {sets} WHERE v2_decision_id=? AND (mfe_time IS NULL OR mfe_time='')",
                      params)
        n += 1
    w.commit()
    chk = w.execute("SELECT SUM(mfe_time IS NOT NULL AND mfe_time!='') , COUNT(*) "
                    "FROM v2_canonical_performance WHERE closed=1").fetchone()
    w.close()
    print(f"기록 {n}건 · canonical closed mfe_time 보유 {chk[0]}/{chk[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
