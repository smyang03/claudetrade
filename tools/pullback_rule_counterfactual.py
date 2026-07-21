from __future__ import annotations

"""눌림존 규칙(PULLBACK_ZONE_RULE)이 거부한 진입을 '그때 샀다면'으로 반사실 검증한다.

배경: 2026-07-22 진입 0의 직접 원인을 추적하니 judge 거부 사유가 일관됐다 —
"price sits right at VWAP … a pullback zone would fill immediately as a chase".
규칙은 buy_zone_high가 현재가보다 0.5% 아래일 것을 요구하는데, 가격이 VWAP 근처면
그런 존을 만들 수 없어 WAIT_RECHECK가 된다. 이를 완화(0.5%→0.3%)하면 진입이 열리므로
후보로 올렸으나, 먼저 "완화했다면 벌었을까"를 확인해야 한다.

데이터: logs/funnel/intraday_entry_shadow_*.jsonl — 미진입 건의 would_entry_price를
남겨둔 순수 관측 원장(주문·플랜에 영향 없음). 여기에 yfinance 5분봉으로 이후 가격을 붙인다.

결과(2026-07-22 최초 실행, US 26건):
  1시간 -0.485%(승률 38.1%) / 2시간 -1.075%(17.6%) / 4시간 -1.750%(35.3%)
전부 음수이고 gross라 비용 반영 시 더 나쁘다 → 규칙 완화는 기각. judge 판단이 옳았다.

한계: gross(수수료·슬리피지·FX 미반영) · yfinance 60일 창 · 표본 수십 건.

  python tools/pullback_rule_counterfactual.py --reason-filter "fill immediately"
"""

import argparse
import glob
import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    ap = argparse.ArgumentParser(description="눌림존 거부 건 반사실 검증")
    ap.add_argument("--market", default="US", choices=["US", "KR"])
    ap.add_argument("--action", default="WAIT_RECHECK")
    ap.add_argument("--reason-filter", default="fill immediately",
                    help="거부 사유에 포함될 문구(부분일치). 빈 값이면 전체")
    ap.add_argument("--horizons", default="12,24,48", help="5분봉 개수(12=1시간)")
    args = ap.parse_args()

    import yfinance as yf

    rows = []
    for path in sorted(glob.glob(str(ROOT / "logs" / "funnel" / "intraday_entry_shadow_*.jsonl"))):
        if not path.endswith(f"_{args.market}.jsonl"):
            continue
        for line in open(path, encoding="utf-8"):
            try:
                d = json.loads(line)
            except Exception:
                continue
            if args.action and d.get("action") != args.action:
                continue
            px = d.get("would_entry_price")
            if not px or float(px) <= 0:
                continue
            reason = str(d.get("reason") or "")
            if args.reason_filter and args.reason_filter not in reason and \
               args.reason_filter.replace("fill ", "fills ") not in reason:
                continue
            rows.append(d)

    print(f"[{args.market}] {args.action} 중 조건 일치 {len(rows)}건")
    if not rows:
        print("표본이 없다.")
        return 0
    tickers = sorted({str(r["ticker"]).strip() for r in rows})
    print(f"대상 종목 {len(tickers)}개")

    cache: dict = {}

    def series(tk):
        if tk in cache:
            return cache[tk]
        try:
            df = yf.download(tk, period="60d", interval="5m", progress=False, auto_adjust=False)
            c = df["Close"].iloc[:, 0] if hasattr(df["Close"], "columns") else df["Close"]
            idx = df.index
            idx = idx.tz_convert("UTC") if idx.tz is not None else idx.tz_localize("UTC")
            cache[tk] = (idx, c)
        except Exception:
            cache[tk] = None
        return cache[tk]

    horizons = [int(x) for x in str(args.horizons).split(",") if x.strip()]
    res: dict = defaultdict(list)
    for r in rows:
        s = series(str(r["ticker"]).strip())
        if not s:
            continue
        idx, c = s
        try:
            # written_at은 KST 기준이라 UTC로 되돌린다.
            dtu = datetime.fromisoformat(str(r["written_at"])) - timedelta(hours=9)
        except Exception:
            continue
        pos = [i for i, t in enumerate(idx) if t.to_pydatetime().replace(tzinfo=None) <= dtu]
        if not pos:
            continue
        t0 = pos[-1]
        entry = float(r["would_entry_price"])
        for h in horizons:
            if t0 + h < len(c):
                res[h].append((float(c.iloc[t0 + h]) / entry - 1.0) * 100.0)

    print("\n=== 거부된 건을 '그때 샀다면' (gross) ===")
    for h in horizons:
        v = res.get(h)
        if not v:
            continue
        win = sum(1 for x in v if x > 0) / len(v) * 100
        print(f"  {h*5:3d}분 후: n={len(v):4d}  평균 {sum(v)/len(v):+.3f}%  승률 {win:5.1f}%")
    print("\n  판정: 평균이 음수면 규칙이 손실을 막고 있다는 뜻이다(완화 기각).")
    print("  한계: gross(비용 미반영) · yfinance 60일 창 · 소표본.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
