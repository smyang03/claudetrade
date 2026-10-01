"""T1kr 컨센서스 데이터 가용성 점검 (2026-09-30) — 네트워크 없음, 캐시 파싱 전용.

목적: T1kr(한국 실적 서프라이즈) 재시도에 필요한 "발표 전 시점(PIT) 컨센서스" 소스가
과거로 얼마나 있는지 판정한다. 프로브 요청(총 ~15회, 1~2초 간격, 페이지뷰 수준)은 curl로
data/analysis/T1kr_consensus_20260930/probe/ 에 캐시했고, 이 스크립트는 그 캐시만 읽어
summary.json을 만든다. 대량 수집은 하지 않았다(아래 약관·robots 판정 때문).

사전 판정 기준(프로브 전 고정):
  - 다년(≥2018) 날짜별 분기 컨센서스가 있으면 Step 2(이벤트 조인·단순 규칙 검정) 진행.
  - 최근 1년 안팎의 개정 이력만 있으면 IS≤2021/OOS≥2022 설계 불가 → Step 2 미실행.
  - 현재값만 있으면 forward 스냅샷 수집기 설계로 전환(구현하지 않음).

Step 2는 위 기준 미충족으로 실행하지 않았다(대체 프록시 금지 — T1kr이 이미 YoY 대체로 실패).
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data" / "analysis" / "T1kr_consensus_20260930"
PROBE = OUT / "probe"


def _load(name: str):
    p = PROBE / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _table(d):
    if d is None:
        return None, None
    t = d.get("tableData")
    if isinstance(t, dict):
        return t.get("tableData"), t.get("tableHeaderData")
    return t, d.get("tableHeaderData")


def naver_mobile_summary():
    d = _load("nv_005930_quarter.json")
    if not d:
        return {"status": "no_cache"}
    cols = d["financeInfo"]["trTitleList"]
    return {
        "periods": [c["key"] for c in cols],
        "consensus_periods": [c["key"] for c in cols if c.get("isConsensus") == "Y"],
        "rows": [r["title"] for r in d["financeInfo"]["rowList"]],
        "verdict": "현재 시점 다음 분기 컨센서스 1칸만. 과거 스냅샷 없음.",
    }


def wisereport_summary():
    res = {}
    for code in ["005930", "036930", "053610", "900140"]:
        rows, hdr = _table(_load(f"wr_sup_q_{code}.json"))
        if rows is None:
            continue
        pre = next((r for r in rows if r["QTR"] == "발표직전(E)"), {})
        act = next((r for r in rows if r["QTR"].endswith("(A)")), {})
        res[code] = {
            "periods": hdr[0] if hdr else None,
            "pre_announce_consensus": {k: pre.get(k) for k in ("FY_2", "FY_1", "FY0")},
            "first_announce_value_date": {k: (act.get(k), act.get(k + "_S")) for k in ("FY_2", "FY_1", "FY0")},
            "has_consensus": any(pre.get(k) not in ("", None) for k in ("FY_2", "FY_1", "FY0")),
        }
    rows, hdr = _table(_load("wr_sup_q_005930_sdt2022.json"))
    res["sDT_20220601_same_periods"] = bool(hdr and hdr[0].get("CNS_FY0") == "2026/06")
    rows_y, hdr_y = _table(_load("wr_sup_y_005930.json"))
    res["annual_periods"] = hdr_y[0] if hdr_y else None
    trend_old = PROBE / "wr_cF5002_202203.txt"
    res["trend_202203_empty"] = trend_old.exists() and '"categories\\":[]' in trend_old.read_text(encoding="utf-8")
    return res


def wayback_summary():
    out = {}
    for code in ["A005930", "A053610"]:
        p = PROBE / f"cdx_fng_{code}.txt"
        if not p.exists():
            continue
        years = {}
        for line in p.read_text(encoding="utf-8").split():
            years[line[:4]] = years.get(line[:4], 0) + 1
        out[code] = dict(sorted(years.items()))
    return out


def main():
    summary = {
        "naver_mobile_finance_quarter": naver_mobile_summary(),
        "wisereport_earnings_surprise": wisereport_summary(),
        "wayback_fnguide_svd_main_capture_days_by_year": wayback_summary(),
        "step2_run": False,
        "step2_reason": "다년 PIT 분기 컨센서스 소스 없음(무료·약관 허용 범위).",
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
