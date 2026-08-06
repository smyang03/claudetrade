"""매도 체결 원장 보강 — PENDING 경로로 빠진 체결가·수량을 KIS 체결내역으로 복원.

2026-08-07 (계약·표본 정합 4번): PENDING 경로 매도(FRMI TP 등)는 로그에 가격이
없어 원장에 qty=0/fill_px=0으로 남는다. 30건 판정은 실체결 재현(수량·평균단가·
실현손익)이 요건이므로, KIS 주문체결조회를 order_no로 대조해 원장 행을 제자리
갱신한다(append 아님 — order_no 멱등 원칙 유지).

- 시세 루프 아님: 종목·구간당 주문체결내역 조회 1회. 장 마감 후 실행 권장.
- 갱신 행에는 fill_source / enriched_at 을 남기고 fill_px_unavailable 은 제거한다.
- shortfall 은 당일 시가가 로컬 CSV/캐시에 있으면 함께 재계산한다(원장 규약 동일:
  BUY 는 raw, SELL 은 부호 반전, 음수=유리).
- 수수료는 이 API 가 주지 않는다 — realized_pnl(봇 확정 손익, 수수료 반영)은 보존,
  fee 필드는 fee_source='unavailable_in_ccnl' 로 명시한다.

사용:
  python tools/execution_ledger_enrich.py --dry-run   # 대상·매칭 결과만 표시
  python tools/execution_ledger_enrich.py             # 원장 제자리 갱신 (백업 생성)
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

LEDGER = ROOT / "data" / "shadow" / "execution_shortfall_ledger.jsonl"
US_PRICE_DIR = ROOT / "data" / "price" / "us"
KR_CACHE = ROOT / "data" / "analysis" / "kr_fallen_price_cache.json"


def _norm_no(order_no: str) -> str:
    return str(order_no or "").strip().lstrip("0")


def _open_px(market: str, ticker: str, day: str) -> float | None:
    """로컬 소스만 사용(네트워크 시세 조회 없음)."""
    if market == "US":
        path = US_PRICE_DIR / f"us_{ticker}.csv"
        if not path.exists():
            return None
        try:
            with open(path, encoding="utf-8-sig") as handle:
                for row in csv.DictReader(handle):
                    if str(row.get("date")) == day:
                        value = float(row.get("open") or 0)
                        return value if value > 0 else None
        except (OSError, ValueError):
            return None
        return None
    try:
        cache = json.loads(KR_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for bar in cache.get(ticker, []):
        if bar.get("d") == day:
            value = float(bar.get("o") or 0)
            return value if value > 0 else None
    return None


def _fetch_fills(market: str, ticker: str, session_date: str) -> list[dict]:
    """체결내역 조회 — order_no 대조용. 구간은 세션일 ±3일."""
    from kis_api import get_access_token, inquire_ccnl_us, inquire_daily_ccld_kr

    day = date.fromisoformat(session_date)
    start = (day - timedelta(days=3)).strftime("%Y%m%d")
    end = (day + timedelta(days=3)).strftime("%Y%m%d")
    token = get_access_token(market=market)
    if market == "US":
        return inquire_ccnl_us(token, start, end, ticker=ticker)
    return inquire_daily_ccld_kr(token, start_date=start, end_date=end, ticker=ticker)


def main() -> int:
    parser = argparse.ArgumentParser(description="Enrich execution shortfall ledger from KIS fills")
    parser.add_argument("--dry-run", action="store_true", help="원장을 쓰지 않고 매칭 결과만 표시")
    args = parser.parse_args()

    if not LEDGER.exists():
        print("원장 없음:", LEDGER)
        return 1
    lines = LEDGER.read_text(encoding="utf-8").splitlines()
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            rows.append(line)  # 원문 보존

    targets = [
        r for r in rows
        if isinstance(r, dict) and float(r.get("fill_px") or 0) <= 0 and r.get("order_no")
    ]
    print(f"원장 {len(rows)}행 / 체결가 없는 행 {len(targets)}건")
    if not targets:
        return 0

    fills_cache: dict[tuple, list[dict]] = {}
    updated = 0
    for r in targets:
        market = str(r.get("market") or "")
        ticker = str(r.get("ticker") or "")
        session = str(r.get("session_date") or "")
        key = (market, ticker, session)
        if key not in fills_cache:
            try:
                fills_cache[key] = _fetch_fills(market, ticker, session)
            except Exception as exc:
                print(f"  {ticker} {session}: KIS 조회 실패 — {str(exc)[:120]}")
                fills_cache[key] = []
        matches = [
            f for f in fills_cache[key]
            if _norm_no(str(f.get("order_no") or f.get("odno") or "")) == _norm_no(r["order_no"])
            and int(f.get("filled_qty") or f.get("ccld_qty") or 0) > 0
        ]
        if not matches:
            print(f"  {r.get('side')} {ticker} order_no={r['order_no']}: 매칭 체결 없음")
            continue
        qty = sum(int(m.get("filled_qty") or m.get("ccld_qty") or 0) for m in matches)
        notional = sum(
            int(m.get("filled_qty") or m.get("ccld_qty") or 0)
            * float(m.get("fill_price") or m.get("ccld_unpr") or 0)
            for m in matches
        )
        if qty <= 0 or notional <= 0:
            print(f"  {r.get('side')} {ticker} order_no={r['order_no']}: 체결 수량/금액 불량")
            continue
        avg_px = notional / qty
        r["qty"] = qty
        r["fill_px"] = round(avg_px, 6)
        r["fill_source"] = "kis_inquire_ccnl"
        r["fee_source"] = "unavailable_in_ccnl"
        r["enriched_at"] = datetime.now().isoformat(timespec="seconds")
        r.pop("fill_px_unavailable", None)
        # 체결일이 원장 세션일과 다르면(익일 체결 등) 그날 시가 대비 shortfall이
        # 무의미해진다 — fill_date만 기록하고 shortfall은 미측정으로 남긴다.
        fill_dates = {
            str(m.get("order_date") or m.get("ord_dt") or "").strip()
            for m in matches
        } - {""}
        fill_date = ""
        if fill_dates:
            raw_date = sorted(fill_dates)[0]
            fill_date = (f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:8]}"
                         if len(raw_date) == 8 and raw_date.isdigit() else raw_date)
            r["fill_date"] = fill_date
        date_mismatch = bool(fill_date) and fill_date != session
        open_px = None if date_mismatch else _open_px(market, ticker, session)
        if date_mismatch:
            r["open_px"] = None
            r["shortfall_pct_vs_open"] = None
            r["shortfall_note"] = f"fill_date_mismatch:{fill_date}"
        elif open_px:
            raw = 100 * (avg_px / open_px - 1)
            r["open_px"] = open_px
            r["shortfall_pct_vs_open"] = round(raw if r.get("side") == "BUY" else -raw, 4)
        updated += 1
        print(f"  {r.get('side')} {ticker} order_no={r['order_no']}: "
              f"{qty}@{avg_px:.4f} 복원"
              + (f" | 체결일 불일치({fill_date}) shortfall 미측정" if date_mismatch
                 else (f" | shortfall {r['shortfall_pct_vs_open']:+.3f}%" if open_px
                       else " | 시가 없음(미측정 유지)")))

    if args.dry_run or updated == 0:
        print(f"{'dry-run' if args.dry_run else '갱신 없음'} — 원장 미변경 (복원 가능 {updated}건)")
        return 0

    backup = LEDGER.with_suffix(f".jsonl.bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(LEDGER, backup)
    tmp = LEDGER.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        for r in rows:
            handle.write((json.dumps(r, ensure_ascii=False) if isinstance(r, dict) else r) + "\n")
    tmp.replace(LEDGER)
    print(f"원장 갱신 {updated}건 완료 (백업: {backup.name})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
