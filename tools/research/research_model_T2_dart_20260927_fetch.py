#!/usr/bin/env python3
"""T2 DART 수집 헬퍼 (read-only 연구, 2026-09-27) — 운영 파일은 건드리지 않는다.

서브커맨드
  list  BGN END   : DART list.json(pblntf_ty I·B, 전 페이지)을 날짜별로 받아 dart_list_{BGN}_{END}.jsonl 로 저장.
                    기존 dart_events_12m.jsonl 이 2026-09-04에서 끝나므로 사후(>=07-01) 창의 끝(09-16)까지 채우고,
                    겹치는 8월을 기존 파일과 대조해 커버리지를 확인하는 용도.
  docs  EVENTS    : 이벤트 파일(rcept_no 열)의 공시 원문 document.xml → 태그 제거 텍스트를 docs/{rcept_no}.txt 로 캐시.
                    재실행 시 캐시된 건 건너뛴다(재개 가능). 호출 간 SLEEP 초 대기, 실행당 MAX_CALLS 상한,
                    KST 08:20 ~ 18:00 평일에는 멈춘다(라이브 레인과 DART 일 한도 공유).

키는 .env.live 의 DART_API_KEY 를 dotenv로 읽고 출력하지 않는다.
원문 정제: <style>/<script> 블록 제거 → 태그 제거 → html unescape → 공백 정규화. 저장은 최대 40,000자.
"""
from __future__ import annotations

import html
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "analysis" / "model_T2_dart_20260927"
DOCS = OUT / "docs"
KST = timezone(timedelta(hours=9))
SLEEP = 0.6
MAX_CALLS = 9000
SAVE_CHARS = 40000

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env.live")
except Exception:
    pass
KEY = (os.getenv("DART_API_KEY") or "").strip()


def _get(url: str, timeout: float = 25.0) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=timeout).read()


def clean_xml(x: str) -> str:
    x = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", x)
    t = re.sub(r"<[^>]+>", " ", x)
    t = html.unescape(t)
    t = re.sub(r"\.xforms[^{}]*\{[^}]*\}", " ", t)          # 인라인 CSS 잔재
    return re.sub(r"\s+", " ", t).strip()


def blocked_now() -> bool:
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 8 * 60 + 20 <= hm < 18 * 60


def cmd_list(bgn: str, end: str) -> None:
    d = datetime.strptime(bgn, "%Y%m%d")
    e = datetime.strptime(end, "%Y%m%d")
    path = OUT / f"dart_list_{bgn}_{end}.jsonl"
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        while d <= e:
            ymd = d.strftime("%Y%m%d")
            if d.weekday() < 5:
                for ty in ("I", "B"):
                    page = 1
                    while True:
                        q = urllib.parse.urlencode({"crtfc_key": KEY, "bgn_de": ymd, "end_de": ymd, "pblntf_ty": ty,
                                                    "page_no": page, "page_count": 100})
                        try:
                            js = json.loads(_get(f"https://opendart.fss.or.kr/api/list.json?{q}"))
                        except Exception as exc:  # 재시도 1회
                            time.sleep(3)
                            try:
                                js = json.loads(_get(f"https://opendart.fss.or.kr/api/list.json?{q}"))
                            except Exception:
                                print("list fail", ymd, ty, page, str(exc)[:80])
                                break
                        time.sleep(SLEEP)
                        st = js.get("status")
                        if st == "013":
                            break
                        if st != "000":
                            print("status", ymd, ty, st, js.get("message"))
                            break
                        for r in js.get("list", []) or []:
                            fh.write(json.dumps({"rcept_no": r.get("rcept_no"), "rcept_dt": r.get("rcept_dt"),
                                                 "stock_code": (r.get("stock_code") or "").strip(),
                                                 "corp_name": r.get("corp_name"), "corp_cls": r.get("corp_cls"),
                                                 "report_nm": (r.get("report_nm") or "").strip(), "ty": ty},
                                                ensure_ascii=False) + "\n")
                            n += 1
                        if page >= int(js.get("total_page") or 1):
                            break
                        page += 1
            d += timedelta(days=1)
    print("rows", n, "->", path)


def cmd_docs(events: str) -> None:
    DOCS.mkdir(parents=True, exist_ok=True)
    rows = [json.loads(l) for l in open(events, encoding="utf-8") if l.strip()]
    todo = [r["rcept_no"] for r in rows if not (DOCS / f"{r['rcept_no']}.txt").exists()]
    print("events", len(rows), "todo", len(todo), flush=True)
    calls = ok = empty = 0
    fail_log = OUT / "docs_fail.jsonl"
    for rn in todo:
        if calls >= MAX_CALLS:
            print("MAX_CALLS reached", flush=True)
            break
        if blocked_now():
            print("blocked window (weekday 08:20-18:00 KST) — stop", flush=True)
            break
        url = f"https://opendart.fss.or.kr/api/document.xml?crtfc_key={KEY}&rcept_no={rn}"
        calls += 1
        txt, err = "", ""
        try:
            raw = _get(url)
            try:
                z = zipfile.ZipFile(io.BytesIO(raw))
                parts = []
                for nm in z.namelist():
                    parts.append(clean_xml(z.read(nm).decode("utf-8", "ignore")))
                txt = " ".join(parts)
            except zipfile.BadZipFile:
                err = raw[:200].decode("utf-8", "ignore")
        except Exception as exc:
            err = str(exc)[:120]
        time.sleep(SLEEP)
        if "020" in err and "요청" in err:   # 사용한도 초과
            print("DART limit hit:", err[:100], flush=True)
            break
        if txt.strip():
            (DOCS / f"{rn}.txt").write_text(txt[:SAVE_CHARS], encoding="utf-8")
            ok += 1
        else:
            empty += 1
            with fail_log.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"rcept_no": rn, "err": err, "ts": datetime.now(KST).isoformat()},
                                    ensure_ascii=False) + "\n")
        if calls % 200 == 0:
            print(f"calls {calls} ok {ok} empty {empty}", flush=True)
    print(f"done calls {calls} ok {ok} empty {empty}", flush=True)


if __name__ == "__main__":
    if not KEY:
        sys.exit("DART_API_KEY missing")
    OUT.mkdir(parents=True, exist_ok=True)
    if sys.argv[1] == "list":
        cmd_list(sys.argv[2], sys.argv[3])
    elif sys.argv[1] == "docs":
        cmd_docs(sys.argv[2])
