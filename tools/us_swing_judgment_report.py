"""US swing forward 30건 판정 리포트 (read-only, 사전 고정 지표).

A안 (2026-08-05 운영자 승인): 판정 날 지표를 급조하면 결과를 보고 지표를 고르게
된다(골대 이동). 판정에 쓸 지표를 지금 코드로 고정하고, 부분 데이터로 미리
돌려 배선 구멍을 사전에 찾는다.

판정 코호트 = 현재 실행 계약(contract_id)과 일치하는 execution shadow 정산분.
교차 확인 = 실체결 원장(execution_shortfall_ledger)의 실현 손익.

사전 고정 지표(순서 포함 — 판정 시 이 순서로 본다):
  1. 표본 진행률 (정산 n / 30)
  2. 평균 net, 승률, PF
  3. 세션 블록 부트스트랩 LCB(5%)
  4. SPY 동일구간 알파 (같은 보유창의 SPY 수익률 차감)
  5. 꼬리 집중도 (상위 3건이 합에서 차지하는 비중)
  6. 최장 연패, 누적 최대 낙폭
  7. 정책 허들 대조 (micro/probe forward 요건)

사용: python tools/us_swing_judgment_report.py [--contract-id <id>]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
SPY = ROOT / "data" / "price" / "us" / "us_SPY.csv"
POLICY = ROOT / "config" / "us_swing_accelerated.json"
SHORTFALL = ROOT / "data" / "shadow" / "execution_shortfall_ledger.jsonl"
SECTOR_MAP = ROOT / "data" / "sector_map.json"
TARGET_N = 30


def _spy_series() -> pd.Series:
    frame = pd.read_csv(SPY, usecols=[0, 4], names=["date", "close"], header=0)
    frame["date"] = frame["date"].astype(str)
    return frame.set_index("date")["close"].astype(float)


def _block_lcb(values: np.ndarray, seed: int = 20260710) -> float | None:
    values = values[np.isfinite(values)]
    if len(values) < 5:
        return None
    rng = np.random.default_rng(seed)
    block = min(5, len(values))
    starts = np.arange(max(1, len(values) - block + 1))
    means = []
    for _ in range(2000):
        sample: list[float] = []
        while len(sample) < len(values):
            start = int(rng.choice(starts))
            sample.extend(values[start:start + block].tolist())
        means.append(float(np.mean(sample[:len(values)])))
    return float(np.quantile(means, 0.05))


def main() -> int:
    parser = argparse.ArgumentParser(description="US swing forward judgment report")
    parser.add_argument("--contract-id", default="", help="기본: 원장의 최신 contract_id")
    args = parser.parse_args()

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    contract = args.contract_id
    if not contract:
        row = con.execute(
            """SELECT execution_shadow_contract_id FROM signals
               WHERE execution_shadow_contract_id IS NOT NULL AND execution_shadow_contract_id<>''
               ORDER BY signal_date DESC LIMIT 1"""
        ).fetchone()
        contract = str(row[0]) if row and row[0] else ""
    frame = pd.read_sql_query(
        """SELECT signal_date, ticker, candidate_source, entry_date, execution_shadow_exit_date,
                  execution_shadow_exit_reason, execution_shadow_net_krw_pct, execution_shadow_qty,
                  breadth_context_state
           FROM signals
           WHERE execution_shadow_eligible=1
             AND COALESCE(execution_shadow_contract_id,'')=?
           ORDER BY signal_date""",
        con, params=(contract,),
    )
    con.close()

    settled = frame[frame["execution_shadow_net_krw_pct"].notna()].copy()
    pending = frame[frame["execution_shadow_net_krw_pct"].isna()]
    print(f"=== US swing forward 판정 리포트 (contract {contract or '미기록'}) ===")
    print(f"[1] 표본: 정산 {len(settled)} / {TARGET_N}  (보유중 {len(pending)}건: "
          f"{', '.join(pending['ticker'].tolist()) or '-'})")
    if settled.empty:
        print("정산 표본 없음 — 이하 지표는 표본 축적 후 산출된다. (배선 점검용 실행 완료)")
        _cross_check_real_fills()
        return 0

    nets = settled["execution_shadow_net_krw_pct"].to_numpy(float)
    pos_sum = nets[nets > 0].sum()
    neg_sum = -nets[nets <= 0].sum()
    print(f"[2] 평균 net {nets.mean():+.3f}% | 승률 {100 * (nets > 0).mean():.0f}% | "
          f"PF {pos_sum / neg_sum:.2f}" if neg_sum > 0 else
          f"[2] 평균 net {nets.mean():+.3f}% | 승률 {100 * (nets > 0).mean():.0f}% | PF inf")
    lcb = _block_lcb(nets)
    print(f"[3] 세션 블록 LCB(5%): {lcb:+.3f}%" if lcb is not None else "[3] LCB: 표본<5 미산출")

    # [4] SPY 동일구간 알파 — 진입일~청산일 SPY 수익률 차감
    spy = _spy_series()
    alphas = []
    for _, row in settled.iterrows():
        d_in = str(row["entry_date"] or "")[:10]
        d_out = str(row["execution_shadow_exit_date"] or "")[:10]
        if d_in in spy.index and d_out in spy.index and spy[d_in] > 0:
            alphas.append(float(row["execution_shadow_net_krw_pct"]) - 100 * (spy[d_out] / spy[d_in] - 1))
    if alphas:
        arr = np.asarray(alphas)
        print(f"[4] SPY 알파: 평균 {arr.mean():+.3f}% (측정 {len(arr)}/{len(settled)}건, "
              f"양수 {100 * (arr > 0).mean():.0f}%)")
    else:
        print("[4] SPY 알파: SPY 봉 매칭 실패 — us_SPY.csv 갱신 확인 필요")

    ordered = np.sort(nets)[::-1]
    top3_share = 100 * ordered[:3].sum() / nets.sum() if nets.sum() > 0 else float("nan")
    print(f"[5] 꼬리 집중: 상위 3건이 합의 {top3_share:.0f}% | 최고 {nets.max():+.1f} 최저 {nets.min():+.1f}")

    streak = worst = 0
    for value in nets:
        streak = streak + 1 if value <= 0 else 0
        worst = max(worst, streak)
    cumulative = np.cumsum(nets)
    drawdown = float((np.maximum.accumulate(cumulative) - cumulative).max())
    print(f"[6] 최장 연패 {worst}건 | 누적 최대 낙폭 {drawdown:.1f}%p")

    policy = json.loads(POLICY.read_text(encoding="utf-8"))
    fwd = policy.get("forward", {})
    print("[7] 허들 대조 (probe 승격 기준):")
    for name, need, value in (
        ("정산 건수", fwd.get("probe_min_matured", 60), len(settled)),
        ("평균 net", fwd.get("probe_min_mean_net_pct", 0.25), round(float(nets.mean()), 3)),
        ("PF", fwd.get("probe_min_profit_factor", 1.2),
         round(pos_sum / neg_sum, 2) if neg_sum > 0 else float("inf")),
        ("block LCB", fwd.get("probe_min_block_lcb_pct", 0.0), lcb),
    ):
        ok = value is not None and value >= need
        print(f"    {name}: {value} (요건 {need}) {'충족' if ok else '미충족'}")

    print("[보조] breadth 분해:", settled.groupby("breadth_context_state")["execution_shadow_net_krw_pct"]
          .agg(["count", "mean"]).round(2).to_dict("index"))
    _sector_distribution(settled)
    _cross_check_real_fills()
    return 0


def _sector_distribution(settled: pd.DataFrame) -> None:
    try:
        sector_map = json.loads(SECTOR_MAP.read_text(encoding="utf-8")).get("US", {})
    except (OSError, ValueError):
        return
    counts: dict[str, int] = {}
    for ticker in settled["ticker"]:
        entry = sector_map.get(str(ticker))
        sector = (entry or {}).get("sector") if isinstance(entry, dict) else ""
        counts[sector or "?"] = counts.get(sector or "?", 0) + 1
    print("[보조] 섹터 분포:", dict(sorted(counts.items(), key=lambda kv: -kv[1])))


def _cross_check_real_fills() -> None:
    """실체결 원장에서 BUY/SELL 짝을 맞춰 실현 수익률을 교차 표시한다."""
    if not SHORTFALL.exists():
        return
    buys: dict[str, dict] = {}
    trades = []
    for line in SHORTFALL.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if str(row.get("source") or "") == "us_swing_5d" and row.get("side") == "BUY":
            buys[row["ticker"]] = row
        elif row.get("side") == "SELL" and row.get("ticker") in buys:
            buy = buys.pop(row["ticker"])
            gross = 100 * (float(row["fill_px"]) / float(buy["fill_px"]) - 1)
            trades.append((row["ticker"], buy["session_date"], row["session_date"], gross))
    print("[교차] 실체결 왕복(us_swing_5d):",
          [f"{t} {a}->{b} {g:+.2f}%(gross)" for t, a, b, g in trades] or "아직 없음",
          f"| 보유중 {sorted(buys)}" if buys else "")


if __name__ == "__main__":
    raise SystemExit(main())
