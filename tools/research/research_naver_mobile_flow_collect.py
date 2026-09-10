# -*- coding: utf-8 -*-
"""네이버 모바일 API로 종목별 투자자 수급 수집 (2026-09-11).

데스크톱 `finance.naver.com/item/frgn.naver`는 09-10에 약 590종목(7,000요청·90분) 뒤 차단됐다(다음날 00:30에도 미해제).
모바일 JSON API가 대안이고 **오히려 낫다**:
  `https://m.stock.naver.com/api/stock/{code}/trend?pageSize=60&page=N`
  → bizdate · organPureBuyQuant(기관) · individualPureBuyQuant(**개인**) · foreignerPureBuyQuant(외국인) · accumulatedTradingVolume(**거래량 분모**)
  데스크톱(20행/페이지, 개인 없음)보다 3배 적은 요청에 필드도 많다.

⚠️ **운영 경로 보호가 이 스크립트의 제1 제약이다.**
`m.stock.naver.com`은 코스닥 ETF 패닉 레인이 매일 15:19에 쓰는 호스트다(`api/index/KOSDAQ/basic`).
여기서 막히면 다음 거래일 레인이 깨진다. 그래서:
  - THROTTLE 기본 1.2초(09-10 차단을 부른 속도의 약 1/3)
  - 연속 실패 ABORT_STREAK회면 **즉시 중단**(두드리지 않는다)
  - `--budget=N` 총 요청 상한
  - `--deadline=HH:MM` 이후에는 새 요청을 시작하지 않는다(장 시작 전에 반드시 멈추게)

사용:
  python tools/research/research_naver_mobile_flow_collect.py <out_jsonl> <targets_json> \
      [--throttle=1.2] [--budget=9000] [--deadline=13:00] [--max-tickers=N]
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

# ⚠️ `page` 파라미터는 **동작하지 않는다**(page=1/2/3이 전부 같은 60행). 09-11 실측.
# 실제 페이징은 `bizdate=YYYYMMDD` 커서다 — 그 날짜 **직전**부터 60행을 준다.
API = "https://m.stock.naver.com/api/stock/{code}/trend?pageSize=60"
UA = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
      "Referer": "https://m.stock.naver.com/"}
MAX_PAGE = 80          # 60행 × 80 = 4,800 거래일(약 19년). 안전 상한.
ABORT_STREAK = 5       # 연속 실패 이만큼이면 차단으로 보고 중단


def _num(x) -> int:
    s = str(x or "").replace(",", "").replace("+", "").strip()
    if not s or s == "-":
        return 0
    try:
        return int(float(s))
    except ValueError:
        return 0


def fetch_page(code: str, cursor: str | None = None, timeout: float = 12.0) -> list[dict]:
    url = API.format(code=code) + (f"&bizdate={cursor}" if cursor else "")
    req = urllib.request.Request(url, headers=UA)
    raw = urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "ignore")
    data = json.loads(raw)
    return data if isinstance(data, list) else []


def collect_ticker(code: str, since: str, throttle: float, budget: list[int]) -> tuple[dict, bool]:
    """(rows, blocked). rows = {YYYY-MM-DD: [vol, inst, indiv, foreign]}"""
    out: dict[str, list[int]] = {}
    since_ymd = since.replace("-", "")
    cursor: str | None = None
    for page in range(1, MAX_PAGE + 1):
        if budget[0] <= 0:
            return out, False
        budget[0] -= 1
        try:
            rows = fetch_page(code, cursor)
        except Exception:  # noqa: BLE001
            return out, True
        if not rows:
            return out, (page == 1)      # 1페이지부터 비면 차단 의심, 뒤쪽이면 정상 종료
        oldest = None
        for r in rows:
            bd = str(r.get("bizdate") or "")
            if len(bd) != 8:
                continue
            oldest = bd
            if bd >= since_ymd:
                out[f"{bd[:4]}-{bd[4:6]}-{bd[6:]}"] = [
                    _num(r.get("accumulatedTradingVolume")),
                    _num(r.get("organPureBuyQuant")),
                    _num(r.get("individualPureBuyQuant")),
                    _num(r.get("foreignerPureBuyQuant")),
                ]
        if not oldest or oldest < since_ymd:
            return out, False
        if cursor == oldest:                    # 커서가 안 움직이면 무한루프 — 중단
            return out, False
        cursor = oldest
        time.sleep(throttle)
    return out, False


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=")[0]: (a.split("=", 1)[1] if "=" in a else "") for a in sys.argv[1:] if a.startswith("--")}
    if len(args) < 2:
        print(__doc__)
        return 1
    out = Path(args[0])
    targets: dict[str, str] = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    throttle = float(flags.get("--throttle", "1.2") or 1.2)
    budget = [int(flags.get("--budget", "9000") or 9000)]
    deadline = flags.get("--deadline", "13:00") or "13:00"
    max_tk = int(flags.get("--max-tickers", "0") or 0)

    done: dict[str, str] = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("rows"):
                cur = min(r["rows"])
                prev = done.get(r["ticker"])
                done[r["ticker"]] = min(prev, cur) if prev else cur
    todo = [(tk, s) for tk, s in sorted(targets.items()) if tk not in done or done[tk] > s]
    if max_tk:
        todo = todo[:max_tk]
    print(f"[MOBILE] 대상 {len(todo)} / 전체 {len(targets)} (이미 충족 {len(targets) - len(todo)}) "
          f"· throttle {throttle}s · budget {budget[0]} · deadline {deadline}", flush=True)
    if not todo:
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    ok = empty = 0
    streak = 0
    t0 = time.time()
    with out.open("a", encoding="utf-8") as fh:
        for i, (tk, since) in enumerate(todo, 1):
            if datetime.now().strftime("%H:%M") >= deadline:
                print(f"[MOBILE] 마감 시각 {deadline} 도달 — 중단(운영 경로 보호)", flush=True)
                break
            if budget[0] <= 0:
                print("[MOBILE] 요청 예산 소진 — 중단", flush=True)
                break
            rows, blocked = collect_ticker(tk, since, throttle, budget)
            fh.write(json.dumps({"ticker": tk, "since": since, "rows": rows, "src": "mobile"}, ensure_ascii=False) + "\n")
            fh.flush()
            if rows:
                ok += 1
                streak = 0
            else:
                empty += 1
                streak = streak + 1 if blocked else streak
                if streak >= ABORT_STREAK:
                    print(f"[MOBILE] 연속 실패 {streak}회 — 차단으로 보고 즉시 중단", flush=True)
                    break
            if i % 25 == 0 or i == len(todo):
                el = time.time() - t0
                print(f"[MOBILE] {i}/{len(todo)} 수집 {ok} 빈값 {empty} · 예산잔여 {budget[0]} · {el:.0f}s "
                      f"(잔여 {el / i * (len(todo) - i) / 60:.0f}분)", flush=True)
    print(f"[MOBILE] 종료 — 수집 {ok} 빈값 {empty} 예산잔여 {budget[0]} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
