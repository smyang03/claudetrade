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


# KSIC(한국표준산업분류) 2자리 대분류 → 캡용 섹터 버킷.
# 다양성 캡(KR 2종목/섹터)의 입력이므로 정밀 분류보다 동질 위험 묶음이 목적이다.
_KSIC_SECTOR_BUCKETS: tuple[tuple[range, str], ...] = (
    (range(1, 4), "농림어업"),
    (range(5, 9), "광업"),
    (range(10, 13), "식품·담배"),
    (range(13, 16), "섬유·의복"),
    (range(16, 19), "목재·종이·인쇄"),
    (range(19, 21), "석유·화학"),
    (range(21, 22), "제약·바이오"),
    (range(22, 24), "고무·비금속"),
    (range(24, 26), "금속"),
    (range(26, 27), "전자·반도체"),
    (range(27, 28), "의료·정밀기기"),
    (range(28, 29), "전기장비"),
    (range(29, 30), "기계"),
    (range(30, 32), "자동차·운송장비"),
    (range(32, 35), "기타제조"),
    (range(35, 40), "전기가스·환경"),
    (range(41, 43), "건설"),
    (range(45, 48), "유통"),
    (range(49, 53), "운수·물류"),
    (range(55, 57), "숙박·음식"),
    (range(58, 64), "정보통신·SW"),
    (range(64, 67), "금융·보험"),
    (range(68, 69), "부동산"),
    (range(70, 74), "전문과학기술"),
    (range(74, 77), "사업지원"),
    (range(84, 100), "기타"),
)


def _ksic_to_sector(induty_code: str) -> str:
    digits = "".join(ch for ch in str(induty_code or "") if ch.isdigit())
    if len(digits) < 2:
        return ""
    division = int(digits[:2])
    for bucket, name in _KSIC_SECTOR_BUCKETS:
        if division in bucket:
            return name
    return "기타"


def _make_kr_dart_fetcher():
    import requests

    key = ""
    env_path = ROOT / ".env.live"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("DART_API_KEY="):
                key = line.split("=", 1)[1].strip()
                break
    if not key:
        raise RuntimeError("DART_API_KEY not found in .env.live")
    corp_codes = json.loads((ROOT / "data" / "dart_corp_codes.json").read_text(encoding="utf-8"))
    session = requests.Session()

    def fetch(ticker: str) -> tuple[str, str]:
        corp = corp_codes.get(str(ticker).strip())
        if not corp:
            return "", ""
        resp = session.get(
            "https://opendart.fss.or.kr/api/company.json",
            params={"crtfc_key": key, "corp_code": corp},
            timeout=10,
        )
        payload = resp.json()
        if str(payload.get("status")) != "000":
            return "", ""
        induty = str(payload.get("induty_code") or "").strip()
        return _ksic_to_sector(induty), f"KSIC:{induty}" if induty else ""

    return fetch


def main() -> int:
    ap = argparse.ArgumentParser(description="티커→섹터 매핑 캐시 생성")
    ap.add_argument("--market", default="US", choices=["US", "KR"])
    ap.add_argument("--since", default="2026-05-23", help="이 날짜 이후 등장한 티커만")
    ap.add_argument("--refresh", action="store_true", help="이미 있는 값도 다시 조회")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--sleep", type=float, default=0.15)
    args = ap.parse_args()

    data = load_map()
    known = data.get(args.market) or {}
    targets = recent_tickers(args.market, args.since)
    if args.market == "KR":
        # 2026-08-05: KR은 yfinance 섹터 품질이 낮아 DART 기업개황(induty_code,
        # 표준산업분류 KSIC)으로 만든다. 키는 이미 라이브에서 사용 중이고
        # corp_code 매핑(data/dart_corp_codes.json)도 매일 갱신된다.
        # 급락 레인 유니버스(641)를 대상에 합친다 — 캡의 실수요처가 그쪽이다.
        try:
            universe = json.loads((ROOT / "data" / "analysis" / "kr_fallen_universe.json").read_text(encoding="utf-8"))
            targets = sorted(set(targets) | {str(t).strip() for t in universe if str(t).strip()})
        except (OSError, ValueError):
            pass
    todo = [t for t in targets if args.refresh or not known.get(t)]
    if args.limit:
        todo = todo[: args.limit]
    print(f"[{args.market}] 대상 {len(targets)} / 조회 필요 {len(todo)}")

    ok = 0
    if args.market == "KR":
        fetch = _make_kr_dart_fetcher()
        source = "dart_company_induty_ksic"
    else:
        import yfinance as yf

        def fetch(ticker: str) -> tuple[str, str]:
            info = yf.Ticker(ticker).info or {}
            return (str(info.get("sector") or "").strip(),
                    str(info.get("industry") or "").strip())

        source = "yfinance_info"

    for i, ticker in enumerate(todo, 1):
        try:
            sector, industry = fetch(ticker)
        except Exception:
            sector, industry = "", ""
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
        "source": source,
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
