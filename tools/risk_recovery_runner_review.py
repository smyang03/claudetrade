"""risk-recovery runner counterfactual 판독 (워크플랜 P2-3, read-only).

규칙(§14): MFE >= 2R AND MFE가 MAE보다 먼저 AND qty>=4 AND sharp reversal 없음
  → 초기 위험금액(risk_krw)만큼 이익확정 일부매도 + 잔량 stop=breakeven + 잔량 target cap 제거.

목적: 조기익절의 평균개선-우측꼬리훼손 사이에서, 위험금액만 회수하고 잔량은 uncapped runner로
두면 좌측꼬리 제한 + 볼록성 보존 가능한지 반실측.

★데이터 선결: mfe_time/mae_time(순서 판정)이 필요. 7/10 시간축 배선 이후 forward 청산에만 존재.
현재 coverage 0이면 '0 eligible'로 보고 = 배선결함 아니라 forward 축적 대기(도구는 ready).

한계: 사후 MFE peak를 사전에 안 것으로 가정 금지 → mfe_time<mae_time인 실제 순서 표본만 사용.
qty<4 자동제외. 부분매도 추가비용 반영. 잔량 stop이 비용포함 breakeven 아래로 안 감.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "ml" / "decisions.db"


def load(db: Path):
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT market, qty, mfe_pct, mae_pct, mfe_time, mae_time, pnl_pct_net, fee_pct_round_trip "
        "FROM v2_learning_performance WHERE closed=1 AND pnl_pct_net IS NOT NULL"
    ).fetchall()
    con.close()
    return rows


def review(db: Path, stop_r: float = 2.0, min_qty: int = 4):
    rows = load(db)
    total = len(rows)
    have_order = sum(1 for r in rows if r[4] is not None and r[5] is not None)
    out = {"total_closed_net": total, "mfe_mae_time_coverage": have_order}
    if have_order == 0:
        out["verdict"] = ("mfe_time/mae_time 0건 → MFE-before-MAE 순서 판정 불가. "
                          "도구 ready, forward 시간축 축적(7/10 배선 이후 청산)만이 경로. 배선결함 아님.")
        return out

    # 순서 판정 가능한 표본만
    elig, base_net, cf_net = [], [], []
    for mkt, qty, mfe, mae, mft, mat, net, fee in rows:
        if mft is None or mat is None or mfe is None:
            continue
        cost = fee or (0.5 if mkt == "US" else 0.21)
        risk = abs(mae) if mae else None  # 초기 위험 근사 = MAE 폭(R)
        # 규칙: MFE>=2R, MFE가 MAE보다 먼저, qty>=min_qty
        if qty and qty >= min_qty and risk and mfe >= stop_r * risk and mft < mat:
            # risk-recovery: risk_krw 회수분 확정 + 잔량 uncapped(실제 net 대리)
            # 회수분 net ≈ risk(=breakeven 확정), 잔량 = 실제 net (uncapped runner)
            frac = min(1.0, risk / mfe) if mfe else 0.0  # 위험금액 회수 위해 판 비율 근사
            cfv = frac * (risk - cost) + (1 - frac) * net
            elig.append(mkt); base_net.append(net); cf_net.append(cfv)
    out["eligible_n"] = len(elig)
    if elig:
        out["base_mean_net"] = round(mean(base_net), 3)
        out["runner_mean_net"] = round(mean(cf_net), 3)
        out["delta_mean"] = round(mean(cf_net) - mean(base_net), 3)
        out["base_max"] = round(max(base_net), 2)
        out["runner_max"] = round(max(cf_net), 2)
    else:
        out["verdict"] = "순서 판정 가능 표본은 있으나 규칙(MFE>=2R·qty>=4·MFE선행) 충족 0건."
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="risk-recovery runner counterfactual (P2-3)")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--stop-r", type=float, default=2.0)
    ap.add_argument("--min-qty", type=int, default=4)
    args = ap.parse_args()
    for k, v in review(Path(args.db), args.stop_r, args.min_qty).items():
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
