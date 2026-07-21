from __future__ import annotations

"""티커→섹터 매핑 캐시를 만든다(섹터 다양성 캡의 입력 복구).

analysts.py의 후보 다양성 캡은 `if sector and sector_counts.get(sector,0) >= cap` 형태라
sector가 비면 조용히 통과한다. 그런데 ticker_selection_log 35,124행의 sector가 KR/US
100% 비어 있었다(2026-07-22 실측) — universe_manager는 값을 전달만 하고 생산자가 없어
애초에 채워진 적이 없는 필드였다. 즉 섹터 집중 방지가 코드만 있고 영구 미작동이었다.

섹터는 거의 바뀌지 않으므로 JSON에 캐시하고 후보 생성 시 조회만 한다. 실패한 티커는
빈 값으로 두며 위조하지 않는다(캡은 기존과 동일하게 통과 — 현행 동작이 최악의 경우다).

사용:
  python tools/build_sector_map.py --market US            # 신규만 채움
  python tools/build_sector_map.py --market US --refresh  # 전체 갱신
"""

import argparse
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SELECTION_DB = ROOT / "data" / "ticker_selection_log.db"
MAP_PATH = ROOT / "data" / "sector_map.json"


def load_map() -> dict:
    if not MAP_PATH.exists():
        return {"US": {}, "KR": {}, "_meta": {}}
    try:
        data = json.loads(MAP_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"US": {}, "KR": {}, "_meta": {}}
    for key in ("US", "KR"):
        if not isinstance(data.get(key), dict):
            data[key] = {}
    if not isinstance(data.get("_meta"), dict):
        data["_meta"] = {}
    return data


def recent_tickers(market: str, since: str) -> list[str]:
    if not SELECTION_DB.exists():
        return []
    con = sqlite3.connect(f"file:{SELECTION_DB}?mode=ro", uri=True, timeout=15)
    try:
        con.execute("PRAGMA busy_timeout=15000")
        rows = con.execute(
            "SELECT DISTINCT ticker FROM ticker_selection_log WHERE market=? AND date>=?",
            (market, since),
        ).fetchall()
    finally:
        con.close()
    return sorted({str(r[0]).strip() for r in rows if str(r[0] or "").strip()})


def main() -> int:
    ap = argparse.ArgumentParser(description="티커→섹터 매핑 캐시 생성")
    ap.add_argument("--market", default="US", choices=["US", "KR"])
    ap.add_argument("--since", default="2026-05-23", help="이 날짜 이후 등장한 티커만")
    ap.add_argument("--refresh", action="store_true", help="이미 있는 값도 다시 조회")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.15)
    args = ap.parse_args()

    if args.market != "US":
        print("KR은 yfinance 섹터 품질이 낮아 별도 매핑이 필요하다. 지금은 US만 지원한다.")
        return 2

    import yfinance as yf

    data = load_map()
    known = data.get(args.market) or {}
    targets = recent_tickers(args.market, args.since)
    todo = [t for t in targets if args.refresh or not known.get(t)]
    if args.limit:
        todo = todo[: args.limit]
    print(f"[{args.market}] 대상 {len(targets)} / 조회 필요 {len(todo)}")

    ok = 0
    for i, ticker in enumerate(todo, 1):
        sector = ""
        industry = ""
        try:
            info = yf.Ticker(ticker).info or {}
            sector = str(info.get("sector") or "").strip()
            industry = str(info.get("industry") or "").strip()
        except Exception:
            sector = ""
        if sector:
            known[ticker] = {"sector": sector, "industry": industry}
            ok += 1
        time.sleep(args.sleep)
        if i % 25 == 0:
            print(f"  {i}/{len(todo)} … 확보 {ok}")

    data[args.market] = known
    data["_meta"][args.market] = {
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(known),
        "source": "yfinance_info",
    }
    MAP_PATH.parent.mkdir(parents=True, exist_ok=True)
    MAP_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(f"저장 {MAP_PATH} — {args.market} {len(known)}개 (이번에 {ok}개 신규)")

    from collections import Counter
    dist = Counter(v.get("sector") for v in known.values() if isinstance(v, dict))
    for name, cnt in dist.most_common(12):
        print(f"  {name}: {cnt}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
