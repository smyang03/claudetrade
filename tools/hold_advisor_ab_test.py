"""hold advisor 이익보호 A/B 테스트 (API 호출 — 비용 발생).

단계 A에서 확인된 핵심 결함(이익 중 HOLD profit_pullback이 -5.17%p giveback)을 대상으로,
과거 profit_pullback HOLD 케이스의 원본 프롬프트를 3변형으로 재판단해 어느 쪽이 익절을
더 잘하는지(giveback 감소) 측정한다.

변형:
  A) 현행 sonnet-4-6 + 원본 프롬프트 (재현 baseline)
  B) sonnet-4-6 + 이익보호 강화 지침 (giveback 통계 주입)
  C) opus-4-8 + 원본 프롬프트

정답 메트릭 — "가상 실현 pnl":
  변형이 SELL 판단 → review 시점 pnl에서 익절(반납 회피)
  변형이 HOLD 판단 → 최종 pnl(반납 반영)
  → 평균 가상 실현 pnl이 높을수록 좋은 변형. 원본(전부 HOLD)은 최종 pnl 평균.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import sys
import time
from statistics import mean

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# .env.live 로드 (API 키)
for line in open(".env.live", encoding="utf-8", errors="ignore"):
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip())

import anthropic  # noqa: E402
from minority_report.hold_advisor import _HOLD_ADVISOR_SYSTEM  # noqa: E402

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))
SONNET = "claude-sonnet-4-6"
OPUS = "claude-opus-4-8"

_RX_T = re.compile(r"종목:\s*(\S+)\s*\(")
_RX_P = re.compile(r"수익률:\s*([-+]?[\d.]+)%")

_PROFIT_GUARD = (
    "\n\n[이익 보호 통계 — 실측]\n"
    "- 이익 중(수익률>0) HOLD는 그 후 평균 -2.36%p 반납했다.\n"
    "- 그중 profit_pullback HOLD는 평균 -5.17%p 반납(최악)으로, 익절 타이밍을 놓친 경우다.\n"
    "- 이익이 이미 났고 모멘텀이 꺾이거나(거래량 둔화·고점 대비 하락) 목표 부근이면 "
    "HOLD로 더 먹으려 하지 말고 SELL(익절)을 우선하라. 반납 위험이 잔여 상승 기대를 넘으면 SELL.\n"
)


def _load_outcomes():
    out = {}
    con = sqlite3.connect("data/ml/decisions.db")
    con.row_factory = sqlite3.Row
    for r in con.execute(
        "SELECT market,ticker,session_date,pnl_pct,close_reason,closed_at FROM v2_learning_performance "
        "WHERE closed=1 AND runtime_mode='live' AND pnl_pct IS NOT NULL"):
        k = (r["market"], str(r["ticker"]).upper(), str(r["session_date"])[:10])
        if k not in out or str(r["closed_at"]) > out[k]["c"]:
            out[k] = {"pnl": r["pnl_pct"], "c": str(r["closed_at"])}
    con.close()
    return out


def _collect_cases(outcomes, limit):
    """반납(giveback) 케이스와 성공 케이스를 균형있게 샘플 — 변별력 측정용."""
    giveback, success = [], []
    files = sorted(glob.glob("logs/raw_calls/*hold_advisor_*.json"))
    for f in files:
        if not any(p in f for p in ("bull", "bear", "neutral")):
            continue
        try:
            d = json.load(open(f, encoding="utf-8"))
        except Exception:
            continue
        pa = d.get("parsed") or {}
        if str(pa.get("action", "")).upper() != "HOLD" or pa.get("hold_mode") != "profit_pullback":
            continue
        pr = d.get("prompt", "") or ""
        mt, mp = _RX_T.search(pr), _RX_P.search(pr)
        if not mt or not mp:
            continue
        rp = float(mp.group(1))
        if rp <= 0:
            continue
        k = (d.get("market"), mt.group(1).upper(), str(d.get("date"))[:10])
        oc = outcomes.get(k)
        if oc is None:
            continue
        delta = oc["pnl"] - rp
        rec = {"prompt": pr, "market": d.get("market"), "ticker": mt.group(1),
               "review_pnl": rp, "final_pnl": oc["pnl"], "delta": delta,
               "label": "giveback" if delta < -0.5 else ("success" if delta > 0.5 else "flat")}
        if rec["label"] == "giveback":
            giveback.append(rec)
        elif rec["label"] == "success":
            success.append(rec)
    half = limit // 2
    return giveback[:half] + success[:half]


def _call(prompt, model):
    try:
        resp = client.messages.create(
            model=model, max_tokens=700,
            system=[{"type": "text", "text": _HOLD_ADVISOR_SYSTEM}],
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.content[0].text.strip()
        m = re.search(r'"action"\s*:\s*"(HOLD|SELL)"', raw)
        return (m.group(1) if m else "?"), resp.usage.input_tokens, resp.usage.output_tokens
    except Exception as e:
        return f"ERR:{e}", 0, 0


def _virtual_pnl(action, review_pnl, final_pnl):
    return review_pnl if action == "SELL" else final_pnl


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    outcomes = _load_outcomes()
    cases = _collect_cases(outcomes, limit)
    print(f"profit_pullback 이익중 HOLD 케이스: {len(cases)}건 (각 3변형 호출)")
    print(f"원본(전부 HOLD) 최종pnl 평균 = {mean(c['final_pnl'] for c in cases):+.2f}%  "
          f"(review시점 평균 {mean(c['review_pnl'] for c in cases):+.2f}%)\n")

    variants = {"A_sonnet_orig": (SONNET, False), "B_sonnet_guard": (SONNET, True), "C_opus_orig": (OPUS, False)}
    results = {k: {"actions": [], "vpnl": [], "in": 0, "out": 0} for k in variants}

    for i, c in enumerate(cases, 1):
        for vname, (model, guard) in variants.items():
            prompt = c["prompt"] + (_PROFIT_GUARD if guard else "")
            act, it, ot = _call(prompt, model)
            results[vname]["actions"].append(act)
            results[vname]["in"] += it
            results[vname]["out"] += ot
            if act in ("HOLD", "SELL"):
                results[vname]["vpnl"].append(_virtual_pnl(act, c["review_pnl"], c["final_pnl"]))
        if i % 5 == 0:
            print(f"  ...{i}/{len(cases)}")

    labels = [c["label"] for c in cases]
    n_gv = labels.count("giveback")
    n_ok = labels.count("success")
    print(f"\n표본: 반납 {n_gv}건 + 성공 {n_ok}건")
    print("\n=== 변형별 변별력 (반납은 SELL해야 좋음, 성공은 HOLD해야 좋음) ===")
    for vname, r in results.items():
        acts = r["actions"]
        gv_sell = sum(1 for a, l in zip(acts, labels) if l == "giveback" and a == "SELL")
        ok_sell = sum(1 for a, l in zip(acts, labels) if l == "success" and a == "SELL")
        vp = mean(r["vpnl"]) if r["vpnl"] else 0
        gv_rate = gv_sell / n_gv * 100 if n_gv else 0
        ok_rate = ok_sell / n_ok * 100 if n_ok else 0
        print(f"  {vname:<16} 반납SELL {gv_sell}/{n_gv}({gv_rate:.0f}%) | 성공SELL {ok_sell}/{n_ok}({ok_rate:.0f}%) | "
              f"변별력 {gv_rate-ok_rate:+.0f}%p | 가상실현 {vp:+.2f}% | tok in={r['in']} out={r['out']}")
    print("\n해석: 변별력(반납SELL률 - 성공SELL률)이 높을수록 좋음(반납만 골라 익절). 가상실현pnl 높을수록 좋음.")


if __name__ == "__main__":
    main()
