"""KIS 컨센서스 엔드포인트 1회 실측 (read-only, 2026-09-30 운영자 승인).

- 대상: estimate-perform(HHKST668300C0), invest-opinion(FHKST663300C0), invest-opbysec(FHKST663400C0)
- 규칙: 주문 없음. 16:05 KST 이전 호출 금지. 총 호출 <= 40, 간격 >= 1.1s.
- 토큰: kis_api.load_token("KR")로 봇과 공유하는 캐시 토큰만 재사용한다. 캐시가 없거나
  만료 임박이면 새로 발급하지 않고 중단한다(get_access_token 호출 금지).
  _kis_get도 쓰지 않는다(EGW00123 시 force_refresh 재발급 경로가 있어서다).
- 비밀값(토큰·앱키·계좌) 출력/저장 금지. 응답 JSON은 계좌 유사 키를 가린 뒤 저장한다.

사용:
  python tools/research/research_kis_consensus_probe_20260930.py --dry-run   # 네트워크 없음
  python tools/research/research_kis_consensus_probe_20260930.py             # 16:05 KST까지 대기 후 실행
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

OUT_DIR = ROOT / "data" / "analysis" / "T1kr_consensus_20260930" / "kis_probe"
KST = ZoneInfo("Asia/Seoul")
START_HHMM = (16, 5)
MAX_CALLS = 40
GAP_SEC = 1.1

TICKERS = ["005930", "000660", "035420", "036930", "053610", "247540", "091990", "068270"]
EST_PATH = "/uapi/domestic-stock/v1/quotations/estimate-perform"
OPN_PATH = "/uapi/domestic-stock/v1/quotations/invest-opinion"
SEC_PATH = "/uapi/domestic-stock/v1/quotations/invest-opbysec"
MAX_PAGES_OPN = 3  # 종목당 invest-opinion 페이지 상한

REDACT_KEYS = {"cano", "acnt_prdt_cd", "acct", "account", "appkey", "appsecret", "authorization", "access_token"}


def _load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=str(ROOT / ".env.live"), override=True)
    # 봇과 동일하게 start-config env_overrides 중 KIS_* 만 반영(09-30 실측: 해당 키 0개)
    try:
        cfg = json.loads((ROOT / "config" / "v2_start_config.json").read_text(encoding="utf-8"))
        for k, v in (cfg.get("env_overrides") or {}).items():
            if str(k).startswith("KIS_"):
                os.environ[str(k)] = str(v)
    except Exception:
        pass


def _redact(obj):
    if isinstance(obj, dict):
        return {k: ("<redacted>" if str(k).lower() in REDACT_KEYS else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(x) for x in obj]
    return obj


def build_plan(today: str) -> list[dict]:
    plan: list[dict] = []
    for t in TICKERS:
        plan.append({"name": f"est_{t}", "path": EST_PATH, "tr_id": "HHKST668300C0",
                     "params": {"SHT_CD": t}, "paginate": False})
    for t in TICKERS:
        plan.append({"name": f"opn_{t}_2018", "path": OPN_PATH, "tr_id": "FHKST663300C0",
                     "params": {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16633",
                                "FID_INPUT_ISCD": t, "FID_INPUT_DATE_1": "20180101",
                                "FID_INPUT_DATE_2": today}, "paginate": True})
    # 깊이 확인: 과거 좁은 창만 따로 요청(행 상한 때문에 최근분만 오는지 구분)
    for d1, d2 in (("20180101", "20181231"), ("20210101", "20211231")):
        plan.append({"name": f"opn_005930_{d1[:4]}only", "path": OPN_PATH, "tr_id": "FHKST663300C0",
                     "params": {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16633",
                                "FID_INPUT_ISCD": "005930", "FID_INPUT_DATE_1": d1,
                                "FID_INPUT_DATE_2": d2}, "paginate": False})
    plan.append({"name": "opn_053610_2021only", "path": OPN_PATH, "tr_id": "FHKST663300C0",
                 "params": {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16633",
                            "FID_INPUT_ISCD": "053610", "FID_INPUT_DATE_1": "20210101",
                            "FID_INPUT_DATE_2": "20211231"}, "paginate": False})
    # invest-opbysec: FID_INPUT_ISCD 의미(종목 vs 회원사) 미확정 → 공식 예제값 그대로 1회
    plan.append({"name": "sec_005930_2026", "path": SEC_PATH, "tr_id": "FHKST663400C0",
                 "params": {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16634",
                            "FID_INPUT_ISCD": "005930", "FID_DIV_CLS_CODE": "0",
                            "FID_INPUT_DATE_1": "20180101", "FID_INPUT_DATE_2": today}, "paginate": False})
    return plan


def _rows(body: dict) -> dict:
    out = {}
    for k, v in (body or {}).items():
        if k.startswith("output"):
            if isinstance(v, list):
                out[k] = v
            elif isinstance(v, dict):
                out[k] = [v]
    return out


def _date_span(rows: list[dict]) -> dict:
    spans = {}
    for r in rows:
        for k, v in r.items():
            kl = k.lower()
            if ("dt" in kl or "date" in kl) and isinstance(v, str) and v.strip().isdigit() and len(v.strip()) in (6, 8):
                s = spans.setdefault(k, [v, v])
                s[0] = min(s[0], v)
                s[1] = max(s[1], v)
    return spans


def summarize(results: list[dict]) -> dict:
    summ = []
    for r in results:
        pages = r.get("pages", [])
        agg: dict[str, list] = {}
        meta = []
        for p in pages:
            body = p.get("body") or {}
            meta.append({"http": p.get("http"), "rt_cd": body.get("rt_cd"), "msg_cd": body.get("msg_cd"),
                         "msg1": body.get("msg1"), "tr_cont": p.get("tr_cont")})
            for k, rows in _rows(body).items():
                agg.setdefault(k, []).extend(rows)
        summ.append({
            "name": r["name"], "params": r["params"], "pages": meta,
            "outputs": {k: {"rows": len(v), "fields": sorted({f for row in v for f in row}),
                            "date_span": _date_span(v)} for k, v in agg.items()},
        })
    return {"generated_kst": datetime.now(KST).isoformat(timespec="seconds"), "calls": summ}


def run(dry: bool) -> int:
    _load_env()
    today = datetime.now(KST).strftime("%Y%m%d")
    plan = build_plan(today)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    status_path = OUT_DIR / "run_status.json"

    import kis_api  # noqa: E402  (env 로드 후 import)

    base = kis_api.get_kis_market_profile("KR").base_url
    if dry:
        fake = {"Content-Type": "application/json", "authorization": "Bearer <dry>", "tr_id": "", "custtype": "P"}
        for p in plan:
            print(p["name"], base.split("//")[-1].split(":")[0], p["path"], p["tr_id"], json.dumps(p["params"]),
                  "paginate" if p["paginate"] else "")
        print(f"plan={len(plan)} base_calls; hard cap={MAX_CALLS}; is_paper={kis_api.IS_PAPER}; headers_keys={list(fake)}")
        return 0

    # 16:05 KST 대기
    while True:
        now = datetime.now(KST)
        if (now.hour, now.minute) >= START_HHMM:
            break
        time.sleep(30)

    import requests

    tok = kis_api.load_token("KR")
    if not tok or not tok.get("access_token"):
        status_path.write_text(json.dumps({"status": "ABORT_NO_CACHED_TOKEN",
                                           "at": datetime.now(KST).isoformat()}, ensure_ascii=False), encoding="utf-8")
        print("ABORT: cached KR token unavailable; not issuing a new one")
        return 2
    token = tok["access_token"]

    calls = 0
    results = []
    last = 0.0
    for p in plan:
        rec = {"name": p["name"], "path": p["path"], "tr_id": p["tr_id"], "params": p["params"], "pages": []}
        tr_cont = ""
        for page in range(MAX_PAGES_OPN if p["paginate"] else 1):
            if calls >= MAX_CALLS:
                break
            wait = GAP_SEC - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
            headers = kis_api._headers(token, p["tr_id"], "KR")
            headers["tr_cont"] = tr_cont
            try:
                resp = requests.get(base + p["path"], headers=headers, params=p["params"], timeout=15)
                resp.encoding = "utf-8"
                last = time.monotonic()
                calls += 1
                try:
                    body = resp.json()
                except Exception:
                    body = {"_raw_text": resp.text[:2000]}
                resp_tr_cont = resp.headers.get("tr_cont", "")
                rec["pages"].append({"page": page, "http": resp.status_code, "tr_cont": resp_tr_cont,
                                     "body": _redact(body)})
                msg = body.get("msg_cd") if isinstance(body, dict) else ""
                print(f"{p['name']} p{page} http={resp.status_code} rt={body.get('rt_cd') if isinstance(body, dict) else '?'} "
                      f"msg={msg} tr_cont={resp_tr_cont}")
                if msg == "EGW00123":  # 토큰 만료 → 재발급하지 않고 중단
                    results.append(rec)
                    status_path.write_text(json.dumps({"status": "ABORT_TOKEN_EXPIRED", "calls": calls}), encoding="utf-8")
                    (OUT_DIR / "raw_responses.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
                    return 3
                if resp_tr_cont in ("M", "F") and p["paginate"]:
                    tr_cont = "N"
                    continue
                break
            except Exception as e:  # 네트워크 오류: 기록 후 다음 항목
                last = time.monotonic()
                calls += 1
                rec["pages"].append({"page": page, "error": type(e).__name__ + ": " + str(e)[:200]})
                break
        results.append(rec)
        if calls >= MAX_CALLS:
            break

    (OUT_DIR / "raw_responses.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    (OUT_DIR / "summary.json").write_text(json.dumps(summarize(results), ensure_ascii=False, indent=1), encoding="utf-8")
    status_path.write_text(json.dumps({"status": "DONE", "calls": calls,
                                       "at": datetime.now(KST).isoformat()}, ensure_ascii=False), encoding="utf-8")
    print(f"DONE calls={calls}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    sys.exit(run(a.dry_run))
