"""US swing forward 30건 판정 리포트 (read-only, 사전 고정 지표).

A안 (2026-08-05 운영자 승인): 판정 날 지표를 급조하면 결과를 보고 지표를 고르게
된다(골대 이동). 판정에 쓸 지표를 지금 코드로 고정하고, 부분 데이터로 미리
돌려 배선 구멍을 사전에 찾는다.

판정 코호트 = **실체결 원장(v2_canonical_performance)의 sleeve 정산분**.
shadow 정산은 근사 검증치로 분리 표기한다.

2026-08-23 정정 (Codex 리뷰 P1-1/P1-3):
  이전 구현은 `execution_shadow_eligible=1` 행을 그대로 표본으로 셌다. 그 결과
    · 계약 발효 전(지문 없음) 7월 행 3건(SMCI·NVTS×2)
    · 라이브가 차단한 건(STEP 08-10)
    · 제출됐으나 미체결로 만료된 건(DIOD 08-14, SUBMITTED_UNCONFIRMED)
  이 모두 표본에 들어가 "정산 9/30, 평균 +3.07%"가 나왔다. 실제로 돈이 오간 건 4건뿐이다.
  게다가 08-20부터 라이브는 거래대금 밴드·MAX로 재선별한 종목을 사는데 shadow는 원 rank1을
  평가하고 있어(08-20 shadow=VOYG / live=MXL) **다른 전략을 재고 있었다.**
  사전등록 코호트 정의는 1항 "실주문과 동일 계약 전체로 선정된 건만", 2항 "정산 수치의
  정본은 실체결 원장이고 shadow 정산은 근사 검증치"다. 그 규약대로 되돌린다.

  · 정산 표본  = 실체결 CLOSED + net 정본 존재
  · 엄격 표본  = 그중 CLEAN(learning_allowed=1)만 — 품질 게이트를 통과한 부분집합
  · 미체결 제출건 = 체결률 통계에만 넣고 손익 평균에서는 뺀다(운영자 판단 08-23)

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
CANON_DB = ROOT / "data" / "ml" / "decisions.db"
SLEEVE_STRATEGY = "us_swing_5d"
SPY = ROOT / "data" / "price" / "us" / "us_SPY.csv"
IWM = ROOT / "data" / "price" / "us" / "us_IWM.csv"
POLICY = ROOT / "config" / "us_swing_accelerated.json"
SHORTFALL = ROOT / "data" / "shadow" / "execution_shortfall_ledger.jsonl"
SECTOR_MAP = ROOT / "data" / "sector_map.json"
TARGET_N = 30


def _bench_series(path: Path) -> pd.Series:
    frame = pd.read_csv(path, usecols=[0, 4], names=["date", "close"], header=0)
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
    # 2026-08-22 수리: 지문별로만 세던 것을 **누적 정본 + 지문 분해 병기**로 바꾼다.
    #
    # 사전등록 규약(08-16, 주문금액 변경 시 코호트 정의 보충):
    #   "30건 판정 카운트는 코호트 정의 2항(실주문 전건 포함) 그대로 이어간다.
    #    판정 리포트는 지문별 코호트를 분해 병기한다."
    # 즉 **누적이 정본이고 지문은 분해**인데, 이전 구현은 현재 지문만 필터링해서
    # 지문이 바뀔 때마다 표본이 0으로 돌아갔다(08-22 실측: 실체결 왕복 6건인데 "정산 0/30").
    # 지문은 08-14 이후 세 번 바뀌었고(50만→100만→76만), 이대로면 30건 도달을 영원히
    # 표시하지 못한다.
    #
    # --contract-id를 명시하면 그 지문만 보는 기존 동작을 유지한다(분해 확인용).
    contract_filter = "AND COALESCE(execution_shadow_contract_id,'')=?" if args.contract_id else ""
    params = (args.contract_id,) if args.contract_id else ()
    frame = pd.read_sql_query(
        f"""SELECT signal_date, ticker, candidate_source, entry_date, execution_shadow_exit_date,
                  execution_shadow_exit_reason, execution_shadow_net_krw_pct, execution_shadow_qty,
                  breadth_context_state, COALESCE(execution_shadow_contract_id,'') AS contract_id
           FROM signals
           WHERE execution_shadow_eligible=1
             {contract_filter}
           ORDER BY signal_date""",
        con, params=params,
    )
    con.close()

    scope = f"contract {args.contract_id}" if args.contract_id else "누적(전 지문)"
    print(f"=== US swing forward 판정 리포트 ({scope}) ===")

    # ── 정본: 실체결 원장 ──────────────────────────────────────────────────────
    contract_start = _contract_start_date(frame)
    real = _real_cohort(contract_start)
    settled = _settled_view(real, frame)
    holding = real[real["closed"] == 0]
    net_missing = real[(real["closed"] == 1) & (real["pnl_pct_net"].isna())]
    strict = settled[settled["learning_allowed"] == 1]

    print(f"[1] 표본(정본=실체결): 정산 {len(settled)} / {TARGET_N}"
          f"  | 엄격(CLEAN) {len(strict)}건"
          f"  | 보유중 {len(holding)}건: {', '.join(holding['ticker'].tolist()) or '-'}")
    print(f"    계약 발효일 {contract_start or '미상'} 이후만 집계 — 그 이전 행은 계약 밖이다.")
    if not net_missing.empty:
        print(f"    ⚠ 청산됐으나 net 정본 결손 {len(net_missing)}건: "
              f"{', '.join(net_missing['ticker'].tolist())} — 원장 동기화 확인 필요(평균에서 제외됨)")
    if not settled.empty:
        print("    [등급 분해] " + " | ".join(
            f"{grade or '미기록'} {len(g)}건" for grade, g in settled.groupby("quality_grade", dropna=False)
        ))
    _shadow_observation_view(frame, contract, args.contract_id, contract_start)
    _cross_check_real_fills()

    if settled.empty:
        print("정산 표본 없음 — 이하 지표는 표본 축적 후 산출된다. (배선 점검용 실행 완료)")
        _observation_views(contract)
        return 0

    nets = settled["execution_shadow_net_krw_pct"].to_numpy(float)
    if len(strict) and len(strict) != len(settled):
        s_nets = strict["execution_shadow_net_krw_pct"].to_numpy(float)
        print(f"[2-엄격] CLEAN {len(strict)}건 평균 net {s_nets.mean():+.3f}% | "
              f"승률 {100 * (s_nets > 0).mean():.0f}% "
              f"(전체 정산과 분리해서 본다 — 품질 게이트 통과분)")
    _fee_regime_view(settled)
    pos_sum = nets[nets > 0].sum()
    neg_sum = -nets[nets <= 0].sum()
    print(f"[2] 평균 net {nets.mean():+.3f}% | 승률 {100 * (nets > 0).mean():.0f}% | "
          f"PF {pos_sum / neg_sum:.2f}" if neg_sum > 0 else
          f"[2] 평균 net {nets.mean():+.3f}% | 승률 {100 * (nets > 0).mean():.0f}% | PF inf")
    lcb = _block_lcb(nets)
    print(f"[3] 세션 블록 LCB(5%): {lcb:+.3f}%" if lcb is not None else "[3] LCB: 표본<5 미산출")

    _alpha_regime_view(settled)

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
    print("[보조] source 분해:", settled.groupby("candidate_source")["execution_shadow_net_krw_pct"]
          .agg(["count", "mean"]).round(2).to_dict("index"))
    _sector_distribution(settled)
    _observation_views(contract)
    return 0


def _fee_regime_view(settled: pd.DataFrame) -> None:
    """[2b] 수수료 규약 분해 — 표본에 두 규약이 섞이는 구간을 자동 보정 병기한다.

    2026-08-23 이전 청산은 `close_position`이 **매도 수수료만** 뺀 값을 net으로 인증했다
    (매수 수수료는 진입 때 현금에서만 빠지고 손익 보고값에는 없었다). 그날 수리 이후
    청산분은 왕복을 뺀다. 두 규약이 한 평균에 섞이면 판정이 흔들리므로:

      · 구규약 행은 매수측 수수료율만큼 net이 **정확히** 과대계상돼 있다
        (buy_fee/cost_basis = 매수 수수료율 그 자체 — 가격·수량과 무관하다).
      · 따라서 보정치는 시장별 상수다: US −0.25%p / KR −0.015%p.

    판정일에 손으로 보정하지 않도록 여기서 자동 병기한다. 원장은 건드리지 않는다 —
    이미 인증된 값을 소급 수정하면 그게 골대 이동이다.
    """
    if settled.empty or "fee_regime" not in settled.columns:
        return
    try:
        from risk_manager import FEE_RATES  # noqa: PLC0415 - 요율 정본 재사용(드리프트 방지)
    except Exception:
        return
    counts = settled["fee_regime"].value_counts().to_dict()
    print("[2b] 수수료 규약: " + " | ".join(f"{k} {v}건" for k, v in sorted(counts.items())))
    legacy = settled[settled["fee_regime"] == "매도측만(구)"]
    if legacy.empty:
        return
    adjusted = settled["execution_shadow_net_krw_pct"].astype(float).copy()
    for index, row in legacy.iterrows():
        market = str(row.get("market") or "US").upper()
        rate = float(FEE_RATES.get(market, FEE_RATES["KR"])["buy"])
        adjusted.loc[index] -= rate * 100.0
    raw_mean = float(settled["execution_shadow_net_krw_pct"].astype(float).mean())
    print(f"     구규약 {len(legacy)}건 매수측 보정 시 전체 평균 "
          f"{raw_mean:+.3f}% → {float(adjusted.mean()):+.3f}% "
          f"(보정폭 {float(adjusted.mean()) - raw_mean:+.3f}%p) — 원장은 불변, 판정용 병기값")


def _contract_start_date(frame: pd.DataFrame) -> str:
    """계약 발효일 = 지문이 처음 찍힌 세션.

    상수로 박지 않고 원장에서 끌어온다 — 지문 이력이 바뀌어도 따라간다.
    이 날짜 이전 행(07월 SMCI·NVTS·AXTI)은 계약 밖이라 판정 표본이 아니다.
    """
    if frame.empty or "contract_id" not in frame.columns:
        return ""
    tagged = frame[frame["contract_id"].astype(str).str.strip() != ""]
    return str(tagged["signal_date"].min()) if not tagged.empty else ""


def _real_cohort(contract_start: str) -> pd.DataFrame:
    """정본 실체결 코호트 — 라이브에서 실제로 체결된 sleeve 건만."""
    empty = pd.DataFrame(columns=[
        "session_date", "ticker", "market", "closed", "quality_grade", "learning_allowed",
        "pnl_pct_net", "pnl_krw_net", "first_closed_at", "fee_pct_round_trip",
    ])
    if not CANON_DB.exists():
        print("[1] ⚠ 정본 원장 없음 — data/ml/decisions.db 확인 필요")
        return empty
    con = sqlite3.connect(f"file:{CANON_DB}?mode=ro", uri=True, timeout=10)
    try:
        frame = pd.read_sql_query(
            """SELECT session_date, ticker, market, closed, quality_grade, learning_allowed,
                      pnl_pct_net, pnl_krw_net, first_closed_at, fee_pct_round_trip
               FROM v2_canonical_performance
               WHERE strategy=? AND filled=1 AND runtime_mode='live'
               ORDER BY session_date""",
            con, params=(SLEEVE_STRATEGY,),
        )
    finally:
        con.close()
    if contract_start and not frame.empty:
        frame = frame[frame["session_date"] >= contract_start]
    return frame


def _settled_view(real: pd.DataFrame, shadow: pd.DataFrame) -> pd.DataFrame:
    """정산 표본을 기존 지표 함수들이 읽는 모양으로 맞춘다.

    net은 **정본(pnl_pct_net)** 을 쓰고, breadth·source·진입일 같은 메타만 shadow
    signals에서 가져온다. net이 비어 있는 행은 표본이 아니다 — gross로 대체하지 않는다.
    """
    if real.empty:
        return pd.DataFrame(columns=[
            "signal_date", "ticker", "entry_date", "execution_shadow_exit_date",
            "execution_shadow_net_krw_pct", "breadth_context_state", "candidate_source",
            "quality_grade", "learning_allowed",
        ])
    settled = real[(real["closed"] == 1) & (real["pnl_pct_net"].notna())].copy()
    if settled.empty:
        return settled.assign(**{
            "signal_date": [], "entry_date": [], "execution_shadow_exit_date": [],
            "execution_shadow_net_krw_pct": [], "breadth_context_state": [], "candidate_source": [],
        })
    settled["fee_regime"] = settled["fee_pct_round_trip"].map(
        lambda v: "왕복(신)" if pd.notna(v) and float(v or 0) > 0 else "매도측만(구)"
    )
    meta_cols = ["signal_date", "ticker", "entry_date", "breadth_context_state", "candidate_source"]
    meta = shadow[[c for c in meta_cols if c in shadow.columns]].copy() if not shadow.empty else pd.DataFrame()
    settled = settled.rename(columns={"session_date": "signal_date"})
    if not meta.empty:
        settled = settled.merge(meta, on=["signal_date", "ticker"], how="left")
    for column in ("entry_date", "breadth_context_state", "candidate_source"):
        if column not in settled.columns:
            settled[column] = ""
    settled["entry_date"] = settled["entry_date"].fillna(settled["signal_date"])
    settled["breadth_context_state"] = settled["breadth_context_state"].fillna("미기록")
    settled["candidate_source"] = settled["candidate_source"].fillna("미기록")
    settled["execution_shadow_exit_date"] = settled["first_closed_at"].astype(str).str[:10]
    settled["execution_shadow_net_krw_pct"] = settled["pnl_pct_net"].astype(float)
    return settled.sort_values("signal_date").reset_index(drop=True)


def _shadow_observation_view(
    frame: pd.DataFrame, contract: str, explicit_contract: str, contract_start: str
) -> None:
    """[S] shadow 근사 관측 — **판정 표본이 아니다.** 계약기 행만 보고 실주문 여부를 병기한다."""
    if frame.empty:
        print("[S] shadow 관측: 행 없음")
        return
    scoped = frame[frame["contract_id"].astype(str).str.strip() != ""]
    if contract_start:
        scoped = scoped[scoped["signal_date"] >= contract_start]
    settled = scoped[scoped["execution_shadow_net_krw_pct"].notna()]
    dropped = len(frame) - len(scoped)
    nets = settled["execution_shadow_net_krw_pct"].to_numpy(float)
    summary = f"정산 {len(settled)}건" + (f", 평균 {nets.mean():+.3f}%" if len(nets) else "")
    print(f"[S] shadow 근사 관측(판정 표본 아님): {summary}"
          + (f" | 계약 전 행 {dropped}건 제외" if dropped else ""))
    if not explicit_contract and not scoped.empty:
        # 지문 분해 병기 — 금액·슬롯·선별이 다른 구간을 섞어 읽지 않게 한다.
        print("    [지문 분해] " + " | ".join(
            f"{cid[:8]}: 정산 {int(g['execution_shadow_net_krw_pct'].notna().sum())}/{len(g)}"
            for cid, g in scoped.groupby("contract_id", dropna=False)
        ))
        print(f"    현재 실행 지문: {contract or '미기록'}")


def _alpha_regime_view(settled: pd.DataFrame) -> None:
    """[4] 동일구간 알파 — 사전등록(2026-08-04) 정본은 IWM 대비, SPY는 보조 축.

    [4b] 국면 분해는 같은 보유창의 IWM 수익률로 나눈다(KR 리포트의 ±1% 규약과 동일).
    """
    regime_nets: dict[str, list[float]] = {}
    for label, path in (("IWM(정본)", IWM), ("SPY(보조)", SPY)):
        try:
            bench = _bench_series(path)
        except OSError:
            print(f"[4] {label} 알파: 가격 CSV 없음 — {path.name} 갱신 확인 필요")
            continue
        alphas = []
        for _, row in settled.iterrows():
            d_in = str(row["entry_date"] or "")[:10]
            d_out = str(row["execution_shadow_exit_date"] or "")[:10]
            if d_in in bench.index and d_out in bench.index and bench[d_in] > 0:
                mkt = 100 * (bench[d_out] / bench[d_in] - 1)
                alphas.append(float(row["execution_shadow_net_krw_pct"]) - mkt)
                if label.startswith("IWM"):
                    regime = "상승" if mkt > 1 else ("하락" if mkt < -1 else "횡보")
                    regime_nets.setdefault(regime, []).append(float(row["execution_shadow_net_krw_pct"]))
        if alphas:
            arr = np.asarray(alphas)
            print(f"[4] {label} 알파: 평균 {arr.mean():+.3f}% (측정 {len(arr)}/{len(settled)}건, "
                  f"양수 {100 * (arr > 0).mean():.0f}%)")
        else:
            print(f"[4] {label} 알파: 봉 매칭 실패 — {path.name} 갱신 확인 필요")
    if regime_nets:
        parts = [f"{reg} {sum(v)/len(v):+.2f}%({len(v)})"
                 for reg in ("상승", "횡보", "하락") if (v := regime_nets.get(reg))]
        print("[4b] 국면별 net(IWM 보유창 ±1%):", " / ".join(parts))


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
        # [A1] rank1~5 paired — 계약 코호트의 모델 원장(net_krw_pct) 기준 동일 잣대 비교.
        # 2026-08-07: rank4~5까지 확장 — 엣지가 rank1에만 있는지(선별 집중도)가 판정 축.
        frame = pd.read_sql_query(
            """SELECT rank, net_krw_pct FROM signals
               WHERE COALESCE(execution_shadow_contract_id,'')=? AND net_krw_pct IS NOT NULL
                 AND rank<=5""",
            con, params=(contract,),
        )
        if len(frame):
            view = frame.groupby("rank")["net_krw_pct"].agg(["count", "mean"]).round(3)
            print("[A1] rank1~5 forward(계약 코호트):", view.to_dict("index"))
            r1 = frame[frame["rank"] == 1]["net_krw_pct"]
            rest = frame[frame["rank"] > 1]["net_krw_pct"]
            if len(r1) and len(rest):
                print(f"[A1b] rank1 vs rank2~5: {r1.mean():+.2f}%(n={len(r1)}) vs "
                      f"{rest.mean():+.2f}%(n={len(rest)}) — 격차가 좁으면 모델 변별력 없음")
        else:
            print("[A1] rank1~5 forward: 정산 대기 (rank2~5도 원장에서 자동 정산됨)")

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

    _gap_through_view(contract)
    _capacity_view(contract)
    _tp_capture_view()
    _tp_ladder_counterfactual_view()
    _candidate_age_view()


def _candidate_age_view() -> None:
    """[A11] 후보 관측 연령 forward 관측 — 2026-08-11 B2 최대 발견.

    오프라인(33만 행): D0 +0.16% / D1-3 +3.77% / D4-10 +6.24% / D10+ +9.97%
    (유동성 통제 후에도 단조). forward에서 같은 단조성이 재현되는지 이 뷰가 판정한다.
    """
    path = ROOT / "data" / "shadow" / "us_candidate_age_shadow.jsonl"
    if not path.exists():
        print("[A11] 후보 연령: 원장 없음 (다음 runner 실행부터 축적)")
        return
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    if not rows:
        print("[A11] 후보 연령: 기록 없음")
        return
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    try:
        nets = {(str(d), str(t)): float(v) for d, t, v in con.execute(
            "SELECT signal_date,ticker,net_krw_pct FROM signals WHERE net_krw_pct IS NOT NULL")}
    finally:
        con.close()
    parts = []
    for bucket in ("D0", "D1-3", "D4-10", "D10+", "unknown"):
        group = [r for r in rows if r.get("bucket") == bucket]
        if not group:
            continue
        vals = [nets[(str(r["session_date"]), str(r["ticker"]))] for r in group
                if (str(r["session_date"]), str(r["ticker"])) in nets]
        parts.append(f"{bucket} {len(group)}건"
                     + (f"(정산 {len(vals)} 평균 {sum(vals)/len(vals):+.2f}%)" if vals else ""))
    print(f"[A11] 후보 연령 분해(세션 {len({r['session_date'] for r in rows})}): " + " | ".join(parts))


def _tp_capture_view() -> None:
    """[A10] TP 포획 조건(ATR 하한) forward 관측 — 2026-08-11 사전등록 레인.

    오프라인 실측: 조건 통과 코호트 net +2.71%/TP 55% vs 모델 rank1 +0.12%/TP 31%.
    forward에서 같은 우위가 재현되는지 이 뷰가 판정한다(조건 통과분 vs 미통과분,
    그리고 실제 진입한 rank1 대비). 조건 성과는 실주문이 아니라 관측이다.
    """
    path = ROOT / "data" / "shadow" / "us_tp_capture_shadow.jsonl"
    if not path.exists():
        print("[A10] TP 포획 조건: 원장 없음 (다음 runner 실행부터 축적)")
        return
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    if not rows:
        print("[A10] TP 포획 조건: 기록 없음")
        return
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    try:
        nets = {(str(d), str(t)): float(v) for d, t, v in con.execute(
            "SELECT signal_date,ticker,net_krw_pct FROM signals WHERE net_krw_pct IS NOT NULL")}
    finally:
        con.close()
    ready = [r for r in rows if r.get("threshold_p75_past_only") is not None]
    passed = [r for r in ready if r.get("passed")]
    sessions = {str(r.get("session_date")) for r in ready}
    parts = []
    for label, group in (("조건 통과", passed), ("미통과", [r for r in ready if not r.get("passed")])):
        vals = [nets[(str(r["session_date"]), str(r["ticker"]))] for r in group
                if (str(r["session_date"]), str(r["ticker"])) in nets]
        parts.append(f"{label} {len(group)}건"
                     + (f"(정산 {len(vals)} 평균 {sum(vals)/len(vals):+.2f}%)" if vals else ""))
    print(f"[A10] TP 포획 조건(ATR past-only 상위25%): 세션 {len(sessions)} | " + " | ".join(parts)
          + ("" if ready else " — 임계 산출까지 이력 150건 필요"))


def _tp_ladder_counterfactual_view() -> None:
    """[A11] TP 상향 counterfactual — 30건 판정일 즉답용 (2026-08-18 배선).

    사전등록 미결(design_tp_capture_lane §5-1): 고변동 코호트 그리드에서 TP15 +3.67% /
    TP20 +4.00%가 현행 TP12(+2.71%)를 양 기간 앞섰고, "레인 도입/판정 시점에 함께
    결정"으로 보류됐다. 판정일에 우리 실거래 코호트로 같은 질문에 답하려면 보유 중
    고점(MFE)이 필요하다 — integrity_check가 sleeve_mfe_path.jsonl에 상시 수집 중.

    근사 규약(명시): TP{t} 도달(peak>=t) 시 그 건의 counterfactual net ≈ t − 0.5(비용).
    갭 보너스·FX 변동은 무시하므로 보수적 하한이다. 미도달 건은 실제 net 유지.
    MFE 수집 이전 정산분(FRMI 등)은 "미관측"으로 표시하고 집계에서 뺀다.
    """
    path = ROOT / "data" / "shadow" / "sleeve_mfe_path.jsonl"
    if not path.exists():
        print("[A11] TP 사다리: MFE 원장 없음")
        return
    peaks: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if str(row.get("source_strategy") or "") != "us_swing_5d":
            continue
        key = str(row.get("ticker") or "").upper()
        peak = row.get("peak_pnl_pct")
        if peak is None:
            continue
        prev = peaks.get(key)
        if prev is None or float(peak) > float(prev.get("peak_pnl_pct") or 0):
            peaks[key] = row

    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    try:
        settled = con.execute(
            """SELECT ticker, signal_date, execution_shadow_net_krw_pct, execution_shadow_exit_reason
               FROM signals
               WHERE execution_shadow_eligible=1 AND execution_shadow_net_krw_pct IS NOT NULL
               ORDER BY signal_date"""
        ).fetchall()
    finally:
        con.close()

    lines = []
    base, cf15, cf20 = [], [], []
    for ticker, signal_date, net, exit_reason in settled:
        key = str(ticker or "").upper()
        peak_row = peaks.get(key)
        if peak_row is None:
            lines.append(f"    {signal_date} {key:6s} net {float(net):+7.2f}%  peak 미관측(수집 이전)")
            continue
        peak = float(peak_row.get("peak_pnl_pct") or 0)
        net_f = float(net)
        base.append(net_f)
        cf15.append(15.0 - 0.5 if peak >= 15.0 else net_f)
        cf20.append(20.0 - 0.5 if peak >= 20.0 else net_f)
        lines.append(f"    {signal_date} {key:6s} net {net_f:+7.2f}%  peak {peak:+6.2f}%  "
                     f"TP15 {'도달' if peak >= 15 else '미달'} · TP20 {'도달' if peak >= 20 else '미달'}")
    print("[A11] TP 사다리 counterfactual (근사: 도달 시 t−0.5, 갭보너스 무시 — 보수 하한):")
    for text in lines:
        print(text)
    if base:
        print(f"    관측 {len(base)}건 합계: 현행 {sum(base):+.2f}% | TP15였다면 {sum(cf15):+.2f}% | TP20였다면 {sum(cf20):+.2f}%")
    holding = {k: v for k, v in peaks.items()}
    if holding:
        tops = ", ".join(f"{k} peak {float(v.get('peak_pnl_pct') or 0):+.2f}%" for k, v in sorted(holding.items()))
        print(f"    (참고) MFE 수집 중: {tops}")
    print("    판정 규약: 30건 시점에 TP15/TP20 합계가 현행을 넘으면 사전등록 결정(§5-1)을 발동한다.")


def _capacity_view(contract: str) -> None:
    """[A9] 세션 후보 수 분해 — "후보 수=신호"(2026-08-07 F2 발견) forward 검증 축.

    풀 크기는 runner가 남기는 us_swing_pool_size.jsonl(top_k 절단 없음)에서 읽고,
    성과는 계약 코호트의 rank1 정산과 조인한다. 오프라인 단조성(1~2개 +0.22 <
    6개+ +2.03)이 forward에서 재현되는지 판정일에 이 축이 답한다.
    """
    pool_path = ROOT / "data" / "shadow" / "us_swing_pool_size.jsonl"
    pools: dict[str, int] = {}
    if pool_path.exists():
        for line in pool_path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
                pools[str(row.get("session_date"))] = int(row.get("scored_n") or 0)
            except (ValueError, TypeError):
                continue
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    try:
        settled = con.execute(
            """SELECT signal_date, execution_shadow_net_krw_pct FROM signals
               WHERE COALESCE(execution_shadow_contract_id,'')=? AND rank=1
                 AND execution_shadow_net_krw_pct IS NOT NULL""",
            (contract,),
        ).fetchall()
    finally:
        con.close()
    if not pools:
        print("[A9] 후보수 분해: 풀 기록 없음 (다음 runner 실행부터 자동 축적)")
        return
    nets_by = {str(d): float(v) for d, v in settled}
    parts = []
    for name, lo, hi in (("1~2개", 1, 2), ("3~5개", 3, 5), ("6개+", 6, 10 ** 6)):
        days = [d for d, n in pools.items() if lo <= n <= hi]
        nets = [nets_by[d] for d in days if d in nets_by]
        label = f"{name} {len(days)}일"
        if nets:
            label += f" 정산 {len(nets)} 평균 {sum(nets)/len(nets):+.2f}%"
        parts.append(label)
    print("[A9] 후보수 분해(veto 후 풀):", " | ".join(parts))


def _gap_through_view(contract: str) -> None:
    """[A8] 갭스루 분해 — exit_reason별 계약 경계(TP/SL) 대비 초과 gross.

    2026-08-07 관측 축 사전 고정: 수익 본체가 오버나이트 갭(new_edge_prospect P2)
    이므로, TP 초과분(갭 보너스)과 SL 미달분(갭 스루 초과손실)을 같은 자로 잰다.
    gross USD 기준(FX 제외) — 경계는 USD 가격에 걸리므로 net_krw로 재면 FX가 섞인다.
    """
    try:
        policy = json.loads(POLICY.read_text(encoding="utf-8"))
        tp = 100 * float(policy["execution_contract"]["take_profit_pct"])
        sl = 100 * float(policy["execution_contract"]["catastrophe_stop_pct"])
    except (OSError, KeyError, ValueError):
        tp, sl = 12.0, 25.0
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True, timeout=10)
    try:
        rows = con.execute(
            """SELECT execution_shadow_exit_reason, execution_shadow_entry_fill_usd,
                      execution_shadow_exit_price
               FROM signals
               WHERE COALESCE(execution_shadow_contract_id,'')=?
                 AND execution_shadow_exit_reason IS NOT NULL
                 AND execution_shadow_entry_fill_usd>0 AND execution_shadow_exit_price>0""",
            (contract,),
        ).fetchall()
    finally:
        con.close()
    groups: dict[str, list[float]] = {}
    for reason, entry, exit_px in rows:
        gross = 100 * (float(exit_px) / float(entry) - 1)
        key = str(reason or "?").upper()
        if key.startswith("TP"):
            groups.setdefault("TP초과(갭보너스)", []).append(gross - tp)
        elif key.startswith("SL"):
            groups.setdefault("SL미달(갭스루)", []).append(gross - (-sl))
        else:
            groups.setdefault(key, []).append(gross)
    if groups:
        print(f"[A8] 갭스루 분해(gross USD, 경계 TP+{tp:.0f}/SL-{sl:.0f}):",
              {k: f"n={len(v)} 평균{sum(v)/len(v):+.2f}%p" for k, v in sorted(groups.items())})
    else:
        print("[A8] 갭스루 분해: 계약 코호트 청산 없음 (TP/SL 도달 시 자동 축적)")


def _cross_check_real_fills() -> None:
    """실체결 원장에서 BUY/SELL 짝을 맞춰 실현 수익률을 교차 표시한다."""
    if not SHORTFALL.exists():
        return
    buys: dict[str, dict] = {}
    trades = []
    unfilled: list[str] = []
    submitted = 0
    for line in SHORTFALL.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if str(row.get("source") or "") == "us_swing_5d" and row.get("side") == "BUY":
            submitted += 1
            # 2026-08-17: 접수만 되고 체결 확정이 없는 주문(DIOD 08-14)은 보유가 아니다.
            # 이 가드가 없으면 미체결 건이 "보유중"으로 영구히 남는다.
            # 2026-08-23: 이런 건은 **체결률 통계에만** 넣는다. 미체결에 shadow 손익을
            # 붙여 실적처럼 세면 안 된다(운영자 판단 08-23).
            if str(row.get("fill_status") or "") == "SUBMITTED_UNCONFIRMED":
                unfilled.append(f"{row.get('ticker')}({row.get('session_date')})")
                continue
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
    if submitted:
        fill_rate = 100.0 * (submitted - len(unfilled)) / submitted
        print(f"[체결률] 제출 {submitted}건 중 체결 {submitted - len(unfilled)}건 ({fill_rate:.0f}%)"
              + (f" | 미체결 {unfilled}" if unfilled else ""))


if __name__ == "__main__":
    raise SystemExit(main())
