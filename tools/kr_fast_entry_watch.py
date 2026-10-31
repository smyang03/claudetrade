# -*- coding: utf-8 -*-
"""KOSPI200·KOSDAQ150 특례편입 후보 감시 — 알림만, 주문·CSV 쓰기 없음 (2026-10-31, 운영자 "감시도구 진행").

L7은 특례편입도 같은 계약으로 산다(docs/operating_model.md L7, 10-31). 특례편입은 정기변경(5·11월)과 달리 수시로
나오고, 명단 입력은 사람이 한다(KRX 자동수집 금지). 이 도구는 "발표가 날 만한 종목"을 미리 알려 입력을 놓치지 않게 한다.

규칙(근사, 판정용 아님): 상장 15거래일 평균 시총이 KOSPI 상위 50위(KOSPI200) / KOSDAQ 상위 30위(KOSDAQ150, 2024-02~)
안이면 특례편입 → 15거래일째 무렵 KRX 발표. 여기서는 오늘 시총 순위(보통주, ETF 제외)로 근사한다.
- 신규 상장 판별: 네이버 일봉 수 < NEW_MAX_BARS. 이력이 긴 종목은 상태 파일에 'old'로 기억해 다시 조회하지 않는다.
- 이전 상장(KOSDAQ→KOSPI) 판별: 전에 KOSDAQ 목록에서 본 종목이 KOSPI 목록에 나타나면 그날부터 센다(첫 실행 이전 이전상장은 못 잡는다).
- 알림: 후보가 ALERT_FROM~ALERT_TO 거래일째이고 아직 kr_index_changes.csv에 그 상장 이후 add 행이 없으면 텔레그램 1통/일.
데이터: m.stock.naver.com 시총 순위(시장당 1회) + fchart 일봉(신규 의심 종목만). 장 마감 후 1회 실행(장중 루프 아님).
상태 data/shadow/kr_fast_entry_watch_state.json · 원장 data/shadow/kr_fast_entry_watch.jsonl
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

STATE = ROOT / "data" / "shadow" / "kr_fast_entry_watch_state.json"
LEDGER = ROOT / "data" / "shadow" / "kr_fast_entry_watch.jsonl"
CHANGES = ROOT / "data" / "reference" / "kr_index_changes.csv"
RULES = {"KOSPI": {"index": "KOSPI200", "cutoff": 50, "fetch": 120}, "KOSDAQ": {"index": "KOSDAQ150", "cutoff": 30, "fetch": 80}}
MARGIN = 10                    # 컷오프 + 10위까지는 '경계'로 기록(시총은 15일 평균이라 오늘 순위와 다를 수 있다)
NEW_MAX_BARS = 40              # 일봉이 이보다 적으면 신규 상장으로 본다
ALERT_FROM, ALERT_TO = 8, 20   # 알림 창(상장 후 거래일째) — 발표는 대개 15거래일째 무렵
URL = "https://m.stock.naver.com/api/stocks/marketValue/{mkt}?page={page}&pageSize={n}"
PAGE = 50


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def fetch_ranking(mkt: str, n: int) -> list[dict]:
    """보통주만(ETF·ETN 제외) 시총 순위. 반환 [{ticker, name, rank, mcap_eok}]."""
    stocks: list[dict] = []
    for page in range(1, n // PAGE + 2):              # pageSize 상한이 있어(120은 400) 50개씩 넘긴다
        req = urllib.request.Request(URL.format(mkt=mkt, page=page, n=PAGE), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=20) as r:
            got = json.loads(r.read().decode("utf-8")).get("stocks") or []
        stocks += got
        if len(got) < PAGE or len(stocks) >= n:
            break
    return rank_stocks(stocks)


def rank_stocks(stocks: list[dict]) -> list[dict]:
    out = []
    for x in stocks:
        if x.get("stockEndType") != "stock":
            continue
        code = str(x.get("itemCode") or "").strip().upper()
        if len(code) != 6 or not code.isalnum():
            continue
        try:
            mcap = int(str(x.get("marketValue") or "0").replace(",", ""))
        except ValueError:
            mcap = 0
        out.append({"ticker": code, "name": x.get("stockName") or code, "rank": len(out) + 1, "mcap_eok": mcap})
    return out


def _bars(ticker: str) -> list[str]:
    from buyback_shadow import naver_daily
    return sorted(naver_daily(ticker, count=NEW_MAX_BARS + 5))


def _csv_has_add_since(ticker: str, since: str, path: Path | None = None) -> bool:
    path = path or CHANGES
    if not path.exists():
        return False
    with path.open(encoding="utf-8-sig") as fh:
        return any((r.get("ticker") or "").strip().upper() == ticker and (r.get("action") or "").strip() == "add"
                   and (r.get("announce_date") or "") >= since for r in csv.DictReader(fh))


def scan(state: dict, rankings: dict[str, list[dict]], bars_fn=_bars, today: str | None = None,
         csv_path: Path | None = None) -> tuple[list[dict], dict]:
    """순수에 가까운 판정부(네트워크는 bars_fn·rankings로 주입). 반환 (후보 목록, 갱신 상태)."""
    today = today or datetime.now().strftime("%Y-%m-%d")
    seen = state.setdefault("tickers", {})
    cands = []
    for mkt, rows in rankings.items():
        rule = RULES[mkt]
        for x in rows:
            if x["rank"] > rule["cutoff"] + MARGIN:
                continue
            t = x["ticker"]
            info = seen.get(t, {})
            moved = info.get("market") == "KOSDAQ" and mkt == "KOSPI"
            if moved and not info.get("transfer_since"):
                info["transfer_since"] = today
            if info.get("old") and not info.get("transfer_since"):
                info["market"] = mkt
                seen[t] = info
                continue
            if info.get("transfer_since"):
                days = info.setdefault("transfer_days", {})
                days[today] = 1
                if len(days) >= NEW_MAX_BARS:               # 이전상장도 충분히 지나면 일반 종목으로
                    seen[t] = {"market": mkt, "old": True}
                    continue
                n, since, kind = len(days), info["transfer_since"], "이전상장"
            else:
                b = bars_fn(t)
                if len(b) >= NEW_MAX_BARS:
                    seen[t] = {"market": mkt, "old": True}
                    continue
                n, since, kind = len(b), (b[0] if b else today), "신규상장"
            info.update(market=mkt, last_rank=x["rank"])
            seen[t] = info
            cands.append({"ticker": t, "name": x["name"], "market": mkt, "index": rule["index"], "rank": x["rank"],
                          "cutoff": rule["cutoff"], "in_cutoff": x["rank"] <= rule["cutoff"], "mcap_eok": x["mcap_eok"],
                          "kind": kind, "since": since, "session_n": n,
                          "in_csv": _csv_has_add_since(t, since, csv_path)})
    return cands, state


def alerts(cands: list[dict]) -> list[dict]:
    return [c for c in cands if c["in_cutoff"] and not c["in_csv"] and ALERT_FROM <= c["session_n"] <= ALERT_TO]


def message(al: list[dict]) -> str:
    lines = ["📌 [L7] 특례편입 후보 — KRX 발표 확인 후 data/reference/kr_index_changes.csv 에 add 행을 넣어야 매수한다"]
    for c in al:
        lines.append(f"- {c['name']}({c['ticker']}) {c['kind']} {c['since']}~ {c['session_n']}거래일째 · "
                     f"{c['market']} 시총 {c['rank']}위(기준 {c['cutoff']}위) · {c['mcap_eok']:,}억 → {c['index']}")
    lines.append("절차: docs/runbooks/l7_index_round_checklist.md (특례편입은 시즌 경고가 없다)")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-telegram", action="store_true")
    a = ap.parse_args()
    try:
        state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {}
    except (OSError, ValueError):
        state = {}
    rankings = {m: fetch_ranking(m, r["fetch"]) for m, r in RULES.items()}
    # 알림 범위 밖 종목도 처음 본 시장을 기억한다 — 나중에 KOSPI 목록에 나타나면 이전상장으로 잡는다
    for m, rows in rankings.items():
        for x in rows:
            state.setdefault("tickers", {}).setdefault(x["ticker"], {}).setdefault("market", m)
    cands, state = scan(state, rankings)
    al = alerts(cands)
    today = datetime.now().strftime("%Y-%m-%d")
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    with LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"date": today, "candidates": cands, "alerted": [c["ticker"] for c in al], "at": _now_iso()},
                            ensure_ascii=False) + "\n")
    for c in cands:
        print(f"[fast-entry] {c['ticker']} {c['name']} {c['market']} {c['rank']}위/{c['cutoff']} {c['kind']} "
              f"{c['session_n']}거래일째 csv={c['in_csv']}")
    print(f"[fast-entry] 후보 {len(cands)} · 알림 {len(al)}")
    if al and not a.no_telegram:
        try:
            from dotenv import load_dotenv
            load_dotenv(str(ROOT / ".env.live"), override=False)
            import telegram_reporter as tg
            tg.send(message(al), parse_mode=None, critical=False)
        except Exception as exc:  # noqa: BLE001
            print(f"[fast-entry] telegram 실패 {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
