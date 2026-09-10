# -*- coding: utf-8 -*-
"""네이버 종목별 기관·외국인 순매매 수집 (2026-09-10, 기관 수급 축 판정용).

왜 네이버인가: KRX Data Marketplace는 **자동화 수단을 통한 대량 조회가 약관 위반**이고 실제로 IP가 1일 차단됐다
(`docs/reports/preregistration_kr_institutional_flow_20260910.md` §5). KRX Open API 키는 13개 서비스 전부 미승인(401).
네이버 `finance.naver.com/item/frgn.naver`는 이 저장소가 이미 매일 쓰는 소스이고, **기관 순매매**를 준다(개인은 없지만 개인 축은 이미 기각됐다).

대체 타당성 실측(2025-10-16, 거래대금 상위 20종목): KRX 기관 순매수 비율(금액) vs 네이버 기관 순매매 비율(수량)
**부호 20/20 일치, 상관 +0.971**, 값도 소수점 둘째자리까지 근사. 대체 가능.

수집 방식: 종목당 한 번만 페이지를 거슬러 올라가며(20행/페이지) 필요한 최소 날짜까지 훑고, 그 구간 전체를 저장한다.
같은 종목이 창 A(인샘플 잔여)와 창 B(OOS)에 모두 필요하면 **한 번의 훑기로 둘 다 채운다.**
재실행하면 이미 받은 종목은 건너뛴다(부분 수집도 최소 날짜 기준으로 판단).

사용:
  python tools/research/research_naver_inst_flow_collect.py <out_jsonl> <targets_json> [--throttle=0.35] [--max-tickers=N]
  targets_json = {"005930": "2024-12-01", ...}  종목 → 그 종목에 필요한 가장 이른 날짜
"""
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
      "Referer": "https://finance.naver.com/"}
MAX_PAGE = 260          # 20행/페이지 → 약 21년치. 안전 상한.
ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
TAG_RE = re.compile(r"<[^>]+>")
DATE_RE = re.compile(r"\d{4}\.\d{2}\.\d{2}")


def _num(x: str) -> int:
    x = x.replace(",", "").replace("+", "").strip()
    if not x or x in ("-",):
        return 0
    try:
        return int(x)
    except ValueError:
        return 0


def fetch_page(code: str, page: int, timeout: float = 15.0) -> list[list[str]]:
    url = f"https://finance.naver.com/item/frgn.naver?code={code}&page={page}"
    html = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read().decode("euc-kr", "ignore")
    out = []
    for tr in ROW_RE.findall(html):
        tds = [TAG_RE.sub("", c).replace("&nbsp;", "").strip() for c in TD_RE.findall(tr)]
        if len(tds) >= 7 and DATE_RE.match(tds[0]):
            out.append(tds)
    return out


def collect_ticker(code: str, since: str, throttle: float) -> dict:
    """since 이후 모든 거래일의 {date: [vol, inst, foreign]}. 실패하면 부분이라도 반환."""
    rows: dict[str, list[int]] = {}
    for page in range(1, MAX_PAGE + 1):
        try:
            tds_list = fetch_page(code, page)
        except Exception:  # noqa: BLE001
            time.sleep(2.0)
            try:
                tds_list = fetch_page(code, page)
            except Exception:  # noqa: BLE001
                return rows
        if not tds_list:
            return rows
        oldest = None
        for tds in tds_list:
            d = tds[0].replace(".", "-")
            oldest = d
            if d >= since:
                rows[d] = [_num(tds[4]), _num(tds[5]), _num(tds[6])]
        if oldest and oldest < since:
            return rows
        time.sleep(throttle)
    return rows


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=")[0]: (a.split("=", 1)[1] if "=" in a else "") for a in sys.argv[1:] if a.startswith("--")}
    if len(args) < 2:
        print(__doc__)
        return 1
    out = Path(args[0])
    targets: dict[str, str] = json.loads(Path(args[1]).read_text(encoding="utf-8"))
    throttle = float(flags.get("--throttle", "0.35") or 0.35)
    max_tk = int(flags.get("--max-tickers", "0") or 0)

    done: dict[str, str] = {}
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("rows"):
                prev = done.get(r["ticker"])
                cur = min(r["rows"])
                done[r["ticker"]] = min(prev, cur) if prev else cur
    todo = [(tk, since) for tk, since in sorted(targets.items())
            if tk not in done or done[tk] > since]
    if max_tk:
        todo = todo[:max_tk]
    print(f"[NAVER] 대상 종목 {len(todo)} / 전체 {len(targets)} (이미 충족 {len(targets) - len(todo)})", flush=True)
    if not todo:
        return 0

    out.parent.mkdir(parents=True, exist_ok=True)
    ok = empty = 0
    t0 = time.time()
    with out.open("a", encoding="utf-8") as fh:
        for i, (tk, since) in enumerate(todo, 1):
            rows = collect_ticker(tk, since, throttle)
            fh.write(json.dumps({"ticker": tk, "since": since, "rows": rows}, ensure_ascii=False) + "\n")
            fh.flush()
            if rows:
                ok += 1
            else:
                empty += 1
            if i % 25 == 0 or i == len(todo):
                el = time.time() - t0
                print(f"[NAVER] {i}/{len(todo)} 수집 {ok} 빈값 {empty} · {el:.0f}s "
                      f"(잔여 {el / i * (len(todo) - i) / 60:.0f}분)", flush=True)
    print(f"[NAVER] 완료 — 수집 {ok} 빈값 {empty} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
