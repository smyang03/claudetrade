# -*- coding: utf-8 -*-
"""매일 아침 운영 요약 텔레그램 (2026-10-05 운영자 권한 D, docs/operating_model.md §4). 조회 전용 — 주문·설정 변경 없음.

내용: 계좌(브로커 스냅샷) · 실전 레인(T1 · KR 자사주 canary · 패닉 ETF 소액 · 파킹) · 지난 24시간 체결 ·
쉐도우 새 신호 수 · 실패한 예약 작업 · 봇 메모리. 이상이 있으면 맨 위에 '⚠️ 확인 필요'로 올린다.
  python tools/daily_ops_report.py [--dry]   (--dry: 전송 없이 출력)
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SH = ROOT / "data" / "shadow"
KST = timezone(timedelta(hours=9))
BOT_MEM_WARN_MB = 1500


def _read(p: Path) -> list[dict]:
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def _recent(rows: list[dict], hours: int = 24) -> list[dict]:
    cut = datetime.now(timezone.utc) - timedelta(hours=hours)
    res = []
    for r in rows:
        try:
            if datetime.fromisoformat(str(r.get("at") or r.get("ran_at") or "")) >= cut:
                res.append(r)
        except ValueError:
            continue
    return res


def account_lines() -> list[str]:
    try:
        s = json.loads((ROOT / "state" / "live_broker_truth_snapshot.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ["계좌: 스냅샷 없음"]
    us = s["markets"]["US"].get("account_summary", {})
    kr = s["markets"]["KR"].get("account_summary", {})
    lines = [f"계좌 총 {float(us.get('kis_total_asset_krw') or 0) / 1e4:,.1f}만 · KR현금 {float(kr.get('cash') or 0) / 1e4:,.1f}만"
             f" · US현금 ${float(us.get('cash') or 0):,.0f}"]
    for m in ("KR", "US"):
        for x in s["markets"][m].get("positions", []):
            lines.append(f"  {x.get('ticker')} {x.get('name') or ''} {float(x.get('pnl_pct') or 0):+.1f}%")
    return lines


def lane_lines() -> list[str]:
    out = []
    t1 = _read(SH / "us_earnings_t1_live.jsonl")
    t1_open = {r["ticker"]: r for r in t1 if r.get("kind") == "position"}
    runs = [r for r in _read(SH / "us_earnings_t1_shadow.jsonl") if r.get("kind") == "run"]
    last = runs[-1] if runs else {}
    out.append(f"T1: 보유 {sum(1 for p in t1_open.values() if p.get('status') == 'OPEN')} · 최근 채점 {last.get('session')} "
               f"후보 {last.get('candidates')} 채점 {last.get('scored')} 선택 {last.get('selected_I1')} 누락 {len(last.get('missed_feed') or [])}")
    for name, fn in (("KR 자사주 canary", "kr_buyback_canary_live.jsonl"), ("패닉 ETF 소액", "kr_panic_etf_micro_live.jsonl"),
                     ("파킹", "cash_parking_live.jsonl")):
        rows = _read(SH / fn)
        pos = {}
        for r in rows:
            if r.get("kind") == "position":
                pos[r["ticker"]] = r
        realized = sum(float(r.get("pnl_krw") or 0) for r in rows if r.get("kind") == "close") + \
            sum(float(p.get("pnl_krw") or 0) for p in pos.values() if p.get("status") == "CLOSED")
        held = [t for t, p in pos.items() if p.get("status") == "OPEN"]
        out.append(f"{name}: 보유 {', '.join(held) or '없음'} · 누적 실현 {realized:,.0f}원")
    fills = []
    for fn in ("us_earnings_t1_live.jsonl", "kr_buyback_canary_live.jsonl", "kr_panic_etf_micro_live.jsonl", "cash_parking_live.jsonl"):
        for r in _recent(_read(SH / fn)):
            if r.get("kind") == "fill":
                bp = r.get("slip_bp_vs_open")
                fills.append(f"  {r.get('side')} {r.get('ticker')} {r.get('filled_qty')}주 @ {r.get('fill_price')}"
                             + (f" (시가 대비 {bp:+.0f}bp)" if isinstance(bp, (int, float)) else ""))
    out.append("지난 24시간 체결: " + ("없음" if not fills else ""))
    out.extend(fills)
    return out


def shadow_lines() -> list[str]:
    def n_new(fn, pred):
        return sum(1 for r in _recent(_read(SH / fn), 30) if pred(r))
    return [f"쉐도우 새 신호(30h): 내부자 {n_new('us_insider_cluster_shadow.jsonl', lambda r: r.get('tradable'))}"
            f" · KR 자사주 {n_new('kr_buyback_shadow.jsonl', lambda r: r.get('qualifies'))}"
            f" · US 자사주 {n_new('us_buyback_shadow.jsonl', lambda r: r.get('qualifies'))}"
            f" · 첫 커버 {n_new('kr_target_price_r3_shadow.jsonl', lambda r: not r.get('superseded'))}"]


def failed_tasks() -> list[str]:
    try:
        raw = subprocess.run(["schtasks", "/query", "/fo", "csv", "/v"], capture_output=True, timeout=60).stdout
        text = raw.decode("cp949", "ignore")
    except Exception as exc:  # noqa: BLE001
        return [f"예약 작업 조회 실패: {type(exc).__name__}"]
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return []
    head = rows[0]
    try:
        i_name = next(i for i, h in enumerate(head) if h in ("TaskName", "작업 이름"))
        i_res = next(i for i, h in enumerate(head) if h in ("Last Result", "마지막 결과"))
        i_last = next(i for i, h in enumerate(head) if h in ("Last Run Time", "마지막 실행 시간"))
    except StopIteration:
        return ["예약 작업 열 이름 인식 실패"]
    bad = set()
    for r in rows[1:]:
        if len(r) <= max(i_name, i_res) or "claudetrade" not in r[i_name]:
            continue
        if r[i_res] not in ("0", "267009", "267011", "267008", "Last Result", "마지막 결과"):
            bad.add(f"{r[i_name].strip(chr(92))} rc {r[i_res]} ({r[i_last]})")
    return sorted(bad)


def bot_memory() -> tuple[str, bool]:
    try:
        pid = json.loads((ROOT / "state" / "live_trading_bot.pid").read_text(encoding="utf-8"))["pid"]
        import psutil
        mb = psutil.Process(pid).memory_info().rss / 1e6
        return f"봇 PID {pid} 메모리 {mb:,.0f}MB", mb > BOT_MEM_WARN_MB
    except Exception as exc:  # noqa: BLE001
        return f"봇 상태 확인 실패({type(exc).__name__}) — 봇이 죽었을 수 있음", True


def build() -> str:
    mem, mem_bad = bot_memory()
    fails = failed_tasks()
    warn = []
    if mem_bad:
        warn.append(mem)
    if fails:
        warn.append(f"실패한 예약 작업 {len(fails)}개")
    lines = [f"[운영 요약] {datetime.now(KST):%m-%d %H:%M}"]
    if warn:
        lines.append("⚠️ 확인 필요: " + " / ".join(warn))
    lines += account_lines() + lane_lines() + shadow_lines() + [mem]
    if fails:
        lines += ["실패 작업:"] + [f"  {f}" for f in fails[:10]]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    a = ap.parse_args()
    text = build()
    print(text)
    if not a.dry:
        from dotenv import load_dotenv
        load_dotenv(str(ROOT / ".env.live"), override=True)
        import telegram_reporter
        ok = telegram_reporter.send(text, parse_mode="", critical=True)
        print(f"[ops-report] 전송 {'성공' if ok else '실패'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
