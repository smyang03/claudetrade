from __future__ import annotations

"""브로커 기간손익 API로 '실제' 왕복 수수료율을 실측한다.

배경: net 판정에 쓰는 수수료가 전 도구에서 하드코딩 상수였다
(`FEE_PCT = {"US": 0.5, "KR": 0.21}`, 8개 파일). 315건 전건이 정확히 이 값이라
실측이 아니었고, "US는 gross 흑자인데 수수료로 적자(-57.81)"라는 진단 전체가
검증되지 않은 상수 하나에 걸려 있었다.

2026-07-22 최초 실측(2026-06-01~07-06):
  KR  0.2075%  (수수료 0.0070% + 거래세 0.2005%)  vs 가정 0.2100%  → 거의 정확
  US  0.4390%                                     vs 가정 0.5000%  → 12% 과대
KR은 거래세가 비용의 대부분이라 가정이 맞았고, US만 과대 계상이었다.

사용 API(둘 다 조회 전용, 주문·상태 변경 없음):
  KR `inquire_period_trade_profit_kr` — sll/buy_fee_smtl, sll_tltx_smtl
  US `inquire_period_profit_us`       — smtl_fee1

★ 토큰 주의: `get_access_token`은 신규 발급이라 1분 1회 rate limit에 걸리고,
   라이브 봇과 토큰을 공유하므로 봇의 갱신을 방해할 수 있다. 기본은 캐시
   토큰(state/live_kis_token.json)을 읽어 쓰고, --fresh-token을 준 경우에만
   발급한다. 장중에는 --fresh-token을 쓰지 않는 것이 안전하다.

한계:
- 표본이 조회 기간에 한정된다(최초 실측은 US 100건·KR 20건).
- 우대율·환율·거래세율이 바뀌면 값이 달라지므로 주기적 재측정이 필요하다.
- US는 원화 환산 기준이며 환전 스프레드는 이 수치에 포함되지 않을 수 있다.

  python tools/measure_actual_fees.py --start 20260601 --end 20260706
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

TOKEN_CACHE = ROOT / "state" / "live_kis_token.json"


def _f(x) -> float:
    try:
        return float(str(x).replace(",", "") or 0)
    except (TypeError, ValueError):
        return 0.0


def _token(fresh: bool) -> str:
    if not fresh:
        if not TOKEN_CACHE.exists():
            raise SystemExit(f"토큰 캐시가 없다: {TOKEN_CACHE} (--fresh-token 필요)")
        data = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
        tok = str(data.get("access_token") or "")
        if len(tok) < 40:
            raise SystemExit("캐시 토큰이 유효하지 않다.")
        return tok
    from kis_api import get_access_token

    return get_access_token("US")


def main() -> int:
    ap = argparse.ArgumentParser(description="실제 왕복 수수료율 실측")
    ap.add_argument("--start", default="20260601")
    ap.add_argument("--end", default="20260706")
    ap.add_argument("--market", default="both", choices=["KR", "US", "both"])
    ap.add_argument("--fresh-token", action="store_true",
                    help="캐시 대신 신규 발급(장중 사용 금지 — 봇 토큰 갱신을 방해할 수 있다)")
    ap.add_argument("--json-out", default="")
    args = ap.parse_args()

    from dotenv import load_dotenv

    load_dotenv(str(ROOT / ".env.live"), override=True)
    token = _token(args.fresh_token)
    out: dict = {"start": args.start, "end": args.end, "markets": {}}

    if args.market in ("KR", "both"):
        from kis_api import inquire_period_trade_profit_kr

        res = inquire_period_trade_profit_kr(token, args.start, args.end, max_pages=5)
        s = (res.get("summaries") or [{}])[0]
        buy_amt, sell_amt = _f(s.get("buy_tr_amt_smtl")), _f(s.get("sll_tr_amt_smtl"))
        buy_fee, sell_fee = _f(s.get("buy_fee_smtl")), _f(s.get("sll_fee_smtl"))
        buy_tax, sell_tax = _f(s.get("buy_tax_smtl")), _f(s.get("sll_tltx_smtl"))
        base = (buy_amt + sell_amt) / 2 if (buy_amt + sell_amt) else 0.0
        total = buy_fee + sell_fee + buy_tax + sell_tax
        rate = (total / base * 100) if base else 0.0
        print(f"=== KR ({args.start}~{args.end}) 건수 {len(res.get('rows') or [])} ===")
        print(f"  매수 {buy_amt:,.0f} / 매도 {sell_amt:,.0f}")
        print(f"  수수료 {buy_fee + sell_fee:,.0f}  거래세 {buy_tax + sell_tax:,.0f}")
        print(f"  ★ 왕복률 {rate:.4f}%  (수수료 {(buy_fee+sell_fee)/base*100:.4f}% "
              f"+ 세금 {(buy_tax+sell_tax)/base*100:.4f}%)" if base else "  기준금액 0")
        print("    현재 상수 0.2100%")
        out["markets"]["KR"] = {"rate_pct": rate, "rows": len(res.get("rows") or []),
                                "assumed_pct": 0.21}

    if args.market in ("US", "both"):
        from kis_api import inquire_period_profit_us

        res = inquire_period_profit_us(token, args.start, args.end, max_pages=5)
        s = (res.get("summaries") or [{}])[0]
        buy_amt, sell_amt = _f(s.get("stck_buy_amt_smtl")), _f(s.get("stck_sll_amt_smtl"))
        fee = _f(s.get("smtl_fee1"))
        base = (buy_amt + sell_amt) / 2 if (buy_amt + sell_amt) else 0.0
        rate = (fee / base * 100) if base else 0.0
        print(f"\n=== US ({args.start}~{args.end}) 건수 {len(res.get('rows') or [])} ===")
        print(f"  매수 {buy_amt:,.0f} / 매도 {sell_amt:,.0f}  총수수료 {fee:,.0f}")
        print(f"  ★ 왕복률 {rate:.4f}%")
        print("    현재 상수 0.5000%")
        out["markets"]["US"] = {"rate_pct": rate, "rows": len(res.get("rows") or []),
                                "assumed_pct": 0.5}

    print("\n판정: 실측이 상수보다 낮으면 net 적자가 과대 계상돼 있었다는 뜻이다.")
    print("한계: 조회 기간 한정 · 우대율/거래세율 변동 시 재측정 필요 · US 환전 스프레드 미포함 가능.")
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=1),
                                       encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
