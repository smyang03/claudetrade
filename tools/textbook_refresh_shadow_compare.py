"""교재 최신화 모델 vs 봉인 모델 — shadow 병렬 비교 (2026-08-24).

배경: us_swing 모델의 학습 교재(`us_yahoo_point_in_time`)는 **2026-04-02에 봉인**돼 있다
(빌드 2026-07-10). 약 5개월 낡은 모델이 매 세션 후보 풀 top_k=10의 순서를 정하고, 실제
진입 종목은 그 풀 안에서 거래대금 밴드·MAX가 고른다. 즉 **풀이 나쁘면 밴드의 천장이 낮다.**

그런데 교재를 최신화하면 선정 조건이 바뀌므로 사전등록상 **판정 코호트 리셋** 사유다
(현재 5/30). 그래서 라이브를 건드리지 않고 shadow로만 두 모델을 나란히 돌려 비교한다.
30건 판정 후 교체 여부를 이 결과로 결정한다.

**설계 — 교란 통제**
  · 유니버스 고정: 최신화 교재도 원본과 **같은 168종목**만 앵커로 쓴다. 유니버스를 넓히면
    "최신성 효과"와 "유니버스 효과"가 섞여 해석이 안 된다(유니버스 확대는 별건).
  · no-lookahead: 최신화 모델은 세션마다 `as_of=session_date`로 학습한다. 그 계약이
    train.session_date < as_of 이고 직전 purge_sessions 구간을 버린다.
    봉인 모델은 교재가 전부 05-02 이전이라 as_of가 no-op이다.
  · 비교 구간: preopen 스냅샷이 있는 2026-05-02~08-21. **전 구간이 봉인 컷오프 이후**라
    봉인 모델에게는 순수 out-of-sample이다.
  · 피처는 두 모델 모두 같은 함수(load_candidate_features)로 만든다 — 모델만 다르다.

사용:
  python tools/textbook_refresh_shadow_compare.py \
      --refreshed-db <경로> --since 2026-05-02 --limit 20
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
import traceback

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime.us_swing_order_bridge import EnvRuntimeConfig, apply_contract_selection  # noqa: E402
from tools.us_daily_alpha_walkforward import load_yahoo_dataset  # noqa: E402
from tools.us_swing_exit_counterfactual import simulate_exit  # noqa: E402
from tools.us_swing_shadow_runner import (  # noqa: E402
    load_candidate_features,
    score_candidates,
)

SEALED_DB = ROOT / "data" / "analysis" / "us_yahoo_point_in_time.db"
PRICE_DIR = ROOT / "data" / "price" / "us"
STATE_DIR = ROOT / "state"
SHADOW_DB = ROOT / "data" / "analysis" / "us_swing_shadow.db"
POLICY = ROOT / "config" / "us_swing_accelerated.json"
TP_PCT, SL_PCT = 0.12, 0.25


def _policy() -> dict:
    """시드·top_k·비용·기간을 **정책 파일에서** 읽는다.

    하드코딩하면 replay가 라이브를 재현하지 못한다(실측: seeds/top_k를 임의값으로 두고
    돌렸더니 sealed replay가 라이브 rank1과 0/6 일치였다). 재현이 안 되면 두 모델
    비교도 의미가 없다.
    """
    data = json.loads(POLICY.read_text(encoding="utf-8"))
    return {
        "seeds": [int(s) for s in (data.get("seeds") or [])],
        "top_k": int(data.get("top_k") or 5),
        "cost_pct": float(data.get("cost_pct") or 0.5),
        "horizon": int(data.get("horizon_sessions") or 5),
    }


def _ensure_live_env() -> str:
    """후보 소스 화이트리스트를 라이브와 같게 맞춘다.

    load_candidate_features가 os.getenv("US_SWING_ALLOWED_SOURCES")로 후보를 거른다.
    이 도구를 셸에서 돌리면 그 env가 없어 **전체 소스**가 들어오고 순위가 통째로 달라진다.
    .env.live + start-config를 읽어 라이브 실효값을 그대로 세팅한다.
    """
    import os
    env: dict[str, str] = {}
    env_path = ROOT / ".env.live"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                env[key.strip()] = value.strip()
    cfg = ROOT / "config" / "v2_start_config.json"
    if cfg.exists():
        try:
            overrides = json.loads(cfg.read_text(encoding="utf-8")).get("env_overrides") or {}
            env.update({str(k): str(v) for k, v in overrides.items()})
        except ValueError:
            pass
    sources = env.get("US_SWING_ALLOWED_SOURCES", "")
    if sources:
        os.environ["US_SWING_ALLOWED_SOURCES"] = sources
    return sources or "(전체 허용)"


def _sessions(since: str, until: str) -> list[str]:
    out = []
    for path in sorted(STATE_DIR.glob("preopen_US_2026*.json")):
        stamp = path.stem.replace("preopen_US_", "")
        session = f"{stamp[:4]}-{stamp[4:6]}-{stamp[6:]}"
        if since <= session <= until:
            out.append(session)
    return out


def _vetoes(session_date: str) -> dict[str, str]:
    """라이브 러너와 **같은 veto**를 적용한다.

    이걸 빼면 replay가 라이브 순위를 재현하지 못한다(08-18 실측: veto 없이 돌리면
    sealed rank1이 RDDT인데 라이브 원장은 FRVO다). 재현이 안 되면 두 모델 비교도
    의미가 없으므로, 아래 _live_rank1로 재현 여부를 매 세션 검증한다.
    """
    path = STATE_DIR / f"us_swing_veto_{session_date.replace('-', '')}.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    raw = payload.get("vetoes")
    return {str(k).upper(): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def _live_rank1(con: sqlite3.Connection, session_date: str) -> str:
    row = con.execute(
        "SELECT ticker FROM signals WHERE signal_date=? AND rank=1", (session_date,)
    ).fetchone()
    return str(row[0]).upper() if row else ""


def _load_bars(ticker: str) -> pd.DataFrame:
    path = PRICE_DIR / f"us_{ticker.upper()}.csv"
    if not path.exists():
        return pd.DataFrame()
    frame = pd.read_csv(path, encoding="utf-8-sig")
    frame["date"] = frame["date"].astype(str).str[:10]
    return frame.sort_values("date").reset_index(drop=True)


def _realized_net_pct(ticker: str, session_date: str, hold_days: int,
                      cost_pct: float) -> tuple[float | None, str]:
    """계약(TP12/SL25/D5)대로 정산했을 때의 net%. 진입은 신호일 **다음** 세션 시가."""
    bars = _load_bars(ticker)
    if bars.empty:
        return None, "price_missing"
    future = bars[bars["date"] > session_date].reset_index(drop=True)
    if future.empty:
        return None, "no_entry_bar"
    entry_price = float(future.iloc[0]["open"])
    if entry_price <= 0:
        return None, "entry_price_invalid"
    window = future.iloc[:hold_days]
    if len(window) < hold_days:
        return None, "hold_window_incomplete"
    try:
        _reason, exit_price, _detail = simulate_exit(
            window, entry_price=entry_price, tp_pct=TP_PCT, sl_pct=SL_PCT, tie_break="sl_first"
        )
    except Exception as exc:
        return None, f"simulate_failed:{str(exc)[:40]}"
    gross = (float(exit_price) / entry_price - 1.0) * 100.0
    return round(gross - cost_pct, 4), ""


def _pick(scored: pd.DataFrame, con: sqlite3.Connection, session_date: str) -> tuple[str, int]:
    """라이브와 같은 선별(밴드→MAX)을 적용해 실제 진입했을 종목을 고른다."""
    pool = [
        {"ticker": str(row["ticker"]).upper(), "rank": int(row["rank"])}
        for _, row in scored.iterrows()
    ]
    picked, _band, _max = apply_contract_selection(EnvRuntimeConfig(), con, session_date, list(pool))
    if not picked:
        return "", 0
    return str(picked[0]["ticker"]), int(picked[0].get("rank") or 0)


def main() -> int:
    parser = argparse.ArgumentParser(description="교재 최신화 모델 shadow 비교")
    parser.add_argument("--refreshed-db", required=True, help="최신화 교재 DB 경로")
    parser.add_argument("--since", default="2026-05-02")
    parser.add_argument("--until", default="2026-08-21")
    parser.add_argument("--limit", type=int, default=0, help="0=전체")
    parser.add_argument("--out", default="", help="세션별 결과 JSONL 저장 경로")
    args = parser.parse_args()

    refreshed_path = Path(args.refreshed_db)
    if not refreshed_path.exists():
        print(f"최신화 교재 없음: {refreshed_path}")
        return 1

    policy = _policy()
    seeds, top_k = policy["seeds"], policy["top_k"]
    cost_pct, hold_days = policy["cost_pct"], policy["horizon"]
    sources = _ensure_live_env()

    sessions = _sessions(args.since, args.until)
    if args.limit:
        sessions = sessions[-args.limit:]
    print(f"=== 교재 최신화 shadow 비교 ===")
    print(f"봉인 교재 {SEALED_DB.name} / 최신화 교재 {refreshed_path.name}")
    print(f"정책: seeds={seeds} top_k={top_k} cost={cost_pct} hold={hold_days} | 소스 ={sources}")
    print(f"세션 {len(sessions)}개 ({sessions[0]} ~ {sessions[-1]})" if sessions else "세션 없음")
    if not sessions:
        return 1

    sealed_con = sqlite3.connect(f"file:{SEALED_DB}?mode=ro", uri=True, timeout=20)
    refreshed_con = sqlite3.connect(f"file:{refreshed_path}?mode=ro", uri=True, timeout=20)
    shadow_con = sqlite3.connect(f"file:{SHADOW_DB}?mode=ro", uri=True, timeout=20)
    # 봉인 교재는 전 구간이 비교창 이전이라 세션마다 다시 읽을 필요가 없다.
    sealed_train = load_yahoo_dataset(sealed_con, horizon=hold_days, cost_pct=cost_pct)

    rows: list[dict] = []
    try:
        for session in sessions:
            snapshot = STATE_DIR / f"preopen_US_{session.replace('-', '')}.json"
            record: dict = {"session_date": session}
            try:
                candidates, _errors = load_candidate_features(
                    snapshot_path=snapshot, price_dir=PRICE_DIR, session_date=session,
                    vetoes=_vetoes(session),
                )
                if candidates.empty:
                    record["skip"] = "no_candidates"
                    rows.append(record); print(f"  {session}: 후보 없음"); continue

                sealed_scored, _ = score_candidates(
                    sealed_train, candidates, seeds=seeds, top_k=top_k
                )
                # 최신화 모델은 세션마다 as_of로 학습한다 (no-lookahead 계약).
                refreshed_train = load_yahoo_dataset(
                    refreshed_con, horizon=hold_days, cost_pct=cost_pct, as_of=session
                )
                refreshed_scored, _ = score_candidates(
                    refreshed_train, candidates, seeds=seeds, top_k=top_k
                )

                sealed_pool = [str(t).upper() for t in sealed_scored["ticker"].tolist()]
                refreshed_pool = [str(t).upper() for t in refreshed_scored["ticker"].tolist()]
                sealed_pick, sealed_rank = _pick(sealed_scored, shadow_con, session)
                refreshed_pick, refreshed_rank = _pick(refreshed_scored, shadow_con, session)

                # 재현 검증 — sealed replay가 라이브 원장의 rank1과 같아야 한다.
                # 다르면 그 세션의 비교는 신뢰할 수 없다(입력이 라이브와 다르다는 뜻).
                live1 = _live_rank1(shadow_con, session)
                record["live_rank1"] = live1
                record["replay_matches_live"] = (
                    bool(live1) and bool(sealed_pool) and sealed_pool[0] == live1
                )
                record.update({
                    "train_rows_sealed": int(len(sealed_train)),
                    "train_rows_refreshed": int(len(refreshed_train)),
                    "pool_overlap": len(set(sealed_pool) & set(refreshed_pool)),
                    "pool_size": len(sealed_pool),
                    "sealed_rank1": sealed_pool[0] if sealed_pool else "",
                    "refreshed_rank1": refreshed_pool[0] if refreshed_pool else "",
                    "sealed_pick": sealed_pick, "sealed_pick_rank": sealed_rank,
                    "refreshed_pick": refreshed_pick, "refreshed_pick_rank": refreshed_rank,
                    "picks_agree": bool(sealed_pick and sealed_pick == refreshed_pick),
                })
                for label, ticker in (("sealed", sealed_pick), ("refreshed", refreshed_pick)):
                    net, reason = (_realized_net_pct(ticker, session, hold_days, cost_pct)
                                   if ticker else (None, "no_pick"))
                    record[f"{label}_net_pct"] = net
                    record[f"{label}_net_reason"] = reason
                marker = "=" if record["picks_agree"] else "*"
                if live1 and not record["replay_matches_live"]:
                    marker += "!"  # 재현 실패 — 이 세션은 요약에서 제외된다
                print(f"  {session} {marker} sealed={sealed_pick or '-'}"
                      f"({record['sealed_net_pct']}) refreshed={refreshed_pick or '-'}"
                      f"({record['refreshed_net_pct']}) pool겹침={record['pool_overlap']}/10"
                      f" 학습표본 {record['train_rows_sealed']}->{record['train_rows_refreshed']}")
            except Exception as exc:
                record["error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
                print(f"  {session}: 실패 {record['error']}")
                if "--debug" in sys.argv:
                    traceback.print_exc()
            rows.append(record)
    finally:
        sealed_con.close(); refreshed_con.close(); shadow_con.close()

    _summarize(rows)
    if args.out:
        Path(args.out).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8"
        )
        print(f"\n세션별 결과 저장: {args.out}")
    return 0


def _summarize(rows: list[dict]) -> None:
    have_pick = [r for r in rows if r.get("sealed_pick") or r.get("refreshed_pick")]
    checked = [r for r in have_pick if r.get("live_rank1")]
    mismatched = [r for r in checked if not r.get("replay_matches_live")]
    if checked:
        print(f"\n[0] 재현 검증: {len(checked) - len(mismatched)}/{len(checked)} 세션에서 "
              f"sealed replay가 라이브 rank1과 일치")
        if mismatched:
            print(f"    ⚠ 불일치 {len(mismatched)}건 — 요약에서 제외: "
                  + ", ".join(f"{r['session_date']}({r.get('sealed_rank1')}!={r.get('live_rank1')})"
                              for r in mismatched[:6]))
    # 라이브 원장이 없는 세션(rank1 미기록)은 검증할 수 없으므로 그대로 포함한다.
    scored = [r for r in have_pick if r.get("replay_matches_live") or not r.get("live_rank1")]
    if not scored:
        print("\n비교 가능한 세션 없음")
        return
    agree = [r for r in scored if r.get("picks_agree")]
    differ = [r for r in scored if not r.get("picks_agree")]
    print(f"\n[1] 세션 {len(scored)} | 같은 종목 {len(agree)} ({100*len(agree)/len(scored):.0f}%)"
          f" | 갈린 세션 {len(differ)}")
    overlaps = [r["pool_overlap"] for r in scored if r.get("pool_overlap") is not None]
    if overlaps:
        size = scored[0].get("pool_size") or len(overlaps)
        print(f"[2] 풀 겹침 평균 {sum(overlaps)/len(overlaps):.1f}/{size}")

    def _mean(items, key):
        vals = [r[key] for r in items if r.get(key) is not None]
        return (sum(vals) / len(vals), len(vals)) if vals else (None, 0)

    for label, items in (("전체", scored), ("갈린 세션만", differ)):
        s_mean, s_n = _mean(items, "sealed_net_pct")
        r_mean, r_n = _mean(items, "refreshed_net_pct")
        if s_n and r_n:
            print(f"[3] {label}: 봉인 {s_mean:+.2f}%(n={s_n}) vs 최신화 {r_mean:+.2f}%(n={r_n})"
                  f" | 차이 {r_mean - s_mean:+.2f}%p")
    print("\n※ 판정 아님 — 표본이 작고 단일 시드 세트다. 30건 판정 후 교체 여부의 입력값이다.")


if __name__ == "__main__":
    raise SystemExit(main())
