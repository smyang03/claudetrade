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
        _observation_views(contract)
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
    _observation_views(contract)
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


def _observation_views(contract: str) -> None:
    """관측 뷰 A1~A4·A7 (2026-08-06 마스터 플랜).

    설계 발견을 그대로 반영한다 — shadow DB는 rank1~5 전 행과 차단 건까지 이미
    정산하고 있으므로 신규 기록 없이 뷰만 얹는다. 30건 판정일에 "확대 형태·
    허들·창·신호 다양성"까지 같은 날 답하기 위한 사전 축적이다.
    """

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    try:
        # [A1] rank1~3 paired — 계약 코호트의 모델 원장(net_krw_pct) 기준 동일 잣대 비교
        frame = pd.read_sql_query(
            """SELECT rank, net_krw_pct FROM signals
               WHERE COALESCE(execution_shadow_contract_id,'')=? AND net_krw_pct IS NOT NULL
                 AND rank<=3""",
            con, params=(contract,),
        )
        if len(frame):
            view = frame.groupby("rank")["net_krw_pct"].agg(["count", "mean"]).round(3)
            print("[A1] rank1~3 forward(계약 코호트):", view.to_dict("index"))
        else:
            print("[A1] rank1~3 forward: 정산 대기 (rank2·3도 원장에서 자동 정산됨)")

        # [A2] 차단된 rank1의 사후 성적 — 허들이 번 돈/까먹은 돈
        blocked = pd.read_sql_query(
            """SELECT handoff_reason, net_krw_pct FROM signals
               WHERE COALESCE(execution_shadow_contract_id,'')=? AND rank=1
                 AND handoff_status='BLOCKED'""",
            con, params=(contract,),
        )
        if len(blocked):
            done = blocked.dropna(subset=["net_krw_pct"])
            print(f"[A2] 차단 rank1: {len(blocked)}건 (정산 {len(done)}건"
                  + (f", 평균 {done['net_krw_pct'].mean():+.2f}% — 양수면 허들이 그만큼 놓친 것" if len(done) else "")
                  + f") 사유: {blocked['handoff_reason'].value_counts().to_dict()}")
        else:
            print("[A2] 차단 rank1: 아직 없음")
    finally:
        con.close()

    # [A3] 진입창 세분 — 실행 원장의 체결 시각을 개장 후 분(minute) 버킷으로
    if SHORTFALL.exists():
        rows = []
        for line in SHORTFALL.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("side") != "BUY" or r.get("source") not in ("us_swing_5d", "kr_fallen_5d"):
                continue
            if r.get("shortfall_pct_vs_open") is None:
                continue
            ts = str(r.get("ts") or "")
            try:
                hh, mm = int(ts[11:13]), int(ts[14:16])
            except ValueError:
                continue
            open_min = (22 * 60 + 30) if r.get("market") == "US" else (9 * 60)
            minutes = (hh * 60 + mm) - open_min
            if minutes < 0:
                minutes += 24 * 60
            bucket = "0-15분" if minutes <= 15 else ("15-30분" if minutes <= 30 else "30분+")
            rows.append((bucket, float(r["shortfall_pct_vs_open"])))
        if rows:
            agg: dict[str, list[float]] = {}
            for bucket, value in rows:
                agg.setdefault(bucket, []).append(value)
            print("[A3] 진입창 세분(shortfall, 음수=유리):",
                  {k: f"n={len(v)} 평균{sum(v)/len(v):+.3f}%" for k, v in sorted(agg.items())})
        else:
            print("[A3] 진입창 세분: 측정 가능한 체결 없음")

    # [A4] 할인 깊이 — 신호일 MA20 대비 이탈을 가격 CSV에서 오프라인 산출
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    try:
        sigs = pd.read_sql_query(
            """SELECT signal_date, ticker, net_krw_pct FROM signals
               WHERE COALESCE(execution_shadow_contract_id,'')=? AND rank=1""",
            con, params=(contract,),
        )
    finally:
        con.close()
    discs = []
    price_dir = ROOT / "data" / "price" / "us"
    for _, row in sigs.iterrows():
        path = price_dir / f"us_{row['ticker']}.csv"
        if not path.exists():
            continue
        try:
            bars = pd.read_csv(path, usecols=[0, 4], names=["date", "close"], header=0)
        except (OSError, ValueError):
            continue
        bars["date"] = bars["date"].astype(str)
        upto = bars[bars["date"] <= str(row["signal_date"])].tail(20)
        if len(upto) < 20 or float(upto["close"].mean()) <= 0:
            continue
        disc = 100 * (float(upto["close"].iloc[-1]) / float(upto["close"].mean()) - 1)
        discs.append((row["ticker"], str(row["signal_date"]), round(disc, 1), row["net_krw_pct"]))
    if discs:
        print("[A4] rank1 할인깊이(MA20 대비, 시장중립 신호 후보):",
              [(t, d, f"{disc:+.1f}%") for t, d, disc, _ in discs[-5:]])

    # [A7-lite] profit_path 모델 판정 교차 — 봇이 이미 남기는 PROFIT_EVIDENCE 로그 활용
    hits = []
    for path in sorted((ROOT / "logs" / "system").glob("live_trading_*.log"))[-10:]:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "[PROFIT_EVIDENCE shadow]" in line and "us_swing_5d" in line:
                would_block = "would_block=" in line and "would_block=none" not in line.lower()
                ticker = line.split("us_swing_5d")[0].strip().split()[-1]
                hits.append((line[:10], ticker, would_block))
    if hits:
        blocked_n = sum(1 for _, _, b in hits if b)
        print(f"[A7] profit_path 교차: 판정 {len(hits)}건 중 모델이 차단했을 건 {blocked_n}건 "
              f"(GBM과의 이견율 — 30건 시점에 이견 건 성과 비교)")


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
            # PENDING 경로 매도는 체결가가 없다(fill_px=0) — 실현 손익(KRW net)으로 대체.
            if float(row.get("fill_px") or 0) > 0:
                gross = 100 * (float(row["fill_px"]) / float(buy["fill_px"]) - 1)
                label = f"{gross:+.2f}%(gross)"
            elif row.get("realized_pnl_pct") is not None:
                label = f"{float(row['realized_pnl_pct']):+.2f}%(net,KRW)"
            else:
                label = "체결가없음"
            trades.append((row["ticker"], buy["session_date"], row["session_date"], label))
    print("[교차] 실체결 왕복(us_swing_5d):",
          [f"{t} {a}->{b} {label}" for t, a, b, label in trades] or "아직 없음",
          f"| 보유중 {sorted(buys)}" if buys else "")


if __name__ == "__main__":
    raise SystemExit(main())
