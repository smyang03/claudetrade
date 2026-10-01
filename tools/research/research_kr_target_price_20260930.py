"""KR 애널리스트 목표가 개정 드리프트 — KIS invest-opinion 백필 + 사전등록 검정 (read-only, 2026-09-30).

운영자 승인: 2026-09-30 18:15 "백필". 주문 없음. kis_api.py·라이브 코드·config·.env·스케줄 작업 수정 없음.

## 수집 규약 (backfill)
- 엔드포인트: `/uapi/domestic-stock/v1/quotations/invest-opinion`, TR `FHKST663300C0`,
  `FID_COND_MRKT_DIV_CODE=J`, `FID_COND_SCR_DIV_CODE=16633`, `FID_INPUT_ISCD`, `FID_INPUT_DATE_1/2`.
  호출당 **100행 상한, 창 안 최신순**(09-30 프로브 실측). 최초일 2018-04-02.
- 토큰: `kis_api.load_token("KR")` 캐시만(봇 공유 `state/live_kis_token.json`). **매 호출 전 재확인**,
  None이면 체크포인트 저장 후 종료(`STOP_TOKEN_UNLOADABLE`). 신규 발급 금지 — `_headers`에 빈 토큰을
  넘기면 내부에서 발급하므로 빈 토큰이면 절대 호출하지 않는다. `_kis_get` 미사용(requests 직접).
  ⚠️ load_token은 발급일≠오늘이면 None → **실효 창은 16:05~23:59 KST**(자정 이후는 봇 재발급 전까지 불가).
- 허용 창: KST 16:05~08:00, 단 22:10~22:35는 대기(T1 US 도구). 간격 ≥1.1초, 밤당 ≤6,000회,
  연속 실패 5회 중단, EGW0012x(토큰) 즉시 중단, EGW00201은 실패로 세고 백오프.
- 창 분할: 전체(20180401~오늘) → 100행이면 연 → 분기 → 월 → 일수 반분(월 안에서도 100행일 때만).
  **100행(=상한) 창의 행은 버리고 자식 창만 채택**(정확히 100이면 날짜 범위가 완전해 보여도 상한으로 간주).
  하루짜리 창이 100행이면 그 행을 쓰되 `incomplete` 표시.
- 재개: 종목 단위 + 창 단위 체크포인트(원자적 tmp→rename). 다시 띄우면 이어서 한다.

## 사전등록 (eval 전에 고정, 2026-09-30 — 이 절은 결과를 본 뒤 수정 금지)
데이터: KIS invest-opinion 행 = 리포트 1건(`stck_bsop_date`, `mbcr_name`, `hts_goal_prc`, `invt_opnn`).
가격: `data/price_backfill_yf/kr_fallen_price_cache_2018_2026.json`(yfinance auto_adjust, 2018-01-02~2026-09-11,
현재 상장 1,746종목, amt=close×volume). 지수: 같은 폴더 `kr/kr_069500.csv`(KOSPI200), `kr/kr_229200.csv`(KOSDAQ150).

1. 정리: (종목, 증권사, 날짜)당 1건(응답 첫 행). 목표가 ≤0/결측은 개정 계산 제외(의견만 사용).
2. 개정: 같은 증권사의 **직전 목표가(365일 이내)** 대비 `rev = tgt/prev − 1`. 365일 초과·없음 = 개정 아님.
   분할 가드: |rev| > 60%는 개정 계산에서 제외하고 건수를 따로 보고한다(yf는 수정주가, 목표가 수정 여부 미확정).
3. 신호(종목-날짜): `up` = rev>0 건수, `down` = rev<0 건수, `mean_rev` = 그날 개정 보고서(0% 포함) 평균.
4. 규칙
   - **R1**: 신호일 D와 직전 4세션(D 포함 5세션) 동안 Σ(up−down) ≥ 2. 조건이 처음 참이 되는 날 발화.
   - **R2**: D의 mean_rev ≥ +10%(개정 보고서 ≥1건).
   - **R3**: 증권사의 해당 종목 첫 등장(데이터 내), 날짜 ≥ 2019-04-02(시작 2018-04-02 이후 12개월 번인),
     의견 ∈ {매수, Buy, BUY, Strong Buy, 적극매수, Outperform(비중확대 포함)} — 정확히는 문자열에 `매수`/`Buy`/`BUY`/
     `Outperform`/`비중확대` 포함.
   - 같은 종목·같은 규칙은 발화 후 H세션 안 재발화 무시(겹침 제거, 보유기간별).
5. 진입: D 다음 세션 시가(D가 휴일이면 D 이후 첫 세션). 보유 H ∈ {5, **20**(주), 60}세션, 청산 = 진입 포함 H번째 세션 종가.
   비용 0.25% 왕복 차감. 신호 종목은 벤치 적격(아래)일 때만 거래.
6. 벤치
   - **주 벤치 = 규모매칭 계약**(`research_universe_bench_20260923.py` 정의): 기준일 = 진입 전날 세션(D 이하 마지막 세션).
     적격 = 그날 봉 있음·이력 ≥25봉·거래대금 ≥20억·종가 ≥1,000원. 적격 집합 내 dvol20 십분위,
     같은 십분위 전 종목에 같은 계약(다음 세션 시가 → H번째 세션 종가, −0.25%)의 등가중 평균. 십분위 표본 <20이면 제외.
   - 보조: KOSPI200(069500), KOSDAQ150(229200) 같은 창(D+1 시가 → 종가), 비용 없음.
7. 통계: 신호일별 평균 초과 → 날짜 순 시계열에 Newey-West t(lag = H−1 관측). IS = 2018-04~2021-12,
   OOS = 2022-01~(가격 끝 2026-09-11). 연도별 부호.
8. **합격선(규칙×H=20 주판정, 주 벤치 기준)**: OOS 초과 > 0 · OOS NW t ≥ 2.64 · 연도 부호 과반 양수 · IS 같은 부호.
   H=5/60은 보조(합격 판정에 쓰지 않음). 여러 규칙 중 하나만 통과하면 다중검정(3규칙)을 명시한다.
9. 생존편향: 상장폐지 종목은 KIS가 0행(091990 실측), yf 캐시도 현재 상장 기준(하한 3.3%, manifest). 가격 캐시 중
   2026-09 전 봉이 끊긴 종목 수와 그 종목들의 KIS 행수를 보고한다. 결과는 **위 방향 편향 가능**.
10. 부분 판정: 백필 미완료 시 `PARTIAL` 표시. 백필은 dvol20 내림차순이라 부분 표본은 대형주 편중이다.

## 사전등록 v2 (2026-09-30 20:3x KST, eval2 계산 전 고정 — 결과를 본 뒤 수정 금지)
배경: K1(국민연금 지분) 교훈 — "규모매칭은 이기고 지수는 못 이김"은 그룹 틸트일 수 있다. v1 결과(규모매칭 통과,
KOSPI200 대비 OOS 음수)가 "커버 종목 틸트"인지 "개정 정보"인지 가른다. 신호·진입·청산·비용·겹침 제거는 v1 그대로.
1. **커버리지 벤치(주 판정)**: 기준일 i의 적격 종목 중 **최근 90일(달력, D−89~D) 안에 리포트가 1건 이상** 있는 종목,
   같은 dvol20 십분위, 같은 보유(D+1 시가 → H번째 종가)·비용 0.25%의 등가중 평균. 셀 표본 <10이면 제외.
   신호 종목 자신도 포함한다.
2. **같은 종목 플라시보**: 거래마다 같은 종목·같은 달력월의 세션 중 그 규칙의 신호 기준일이 아닌 날을
   20회 복원추출(seed 20260930)한다. 적격·수익·커버리지 벤치가 있는 날만 쓴다. 플라시보 초과 = 추출일 커버리지 벤치
   초과의 평균. 보고: 신호 초과, 플라시보 초과, 차이(신호 − 플라시보)의 신호일 평균과 NW t.
3. 연도별 H20: 규칙별 vs KOSPI200·KOSDAQ150·규모매칭·커버리지 벤치.
4. 분할(H20, vs 커버리지 벤치, IS/OOS): 시장(KOSPI Y / KOSDAQ K — instiwatch `disclosures.corp_cls`,
   현재 소속 기준이라 이전상장은 오분류 가능), dvol20 삼분위(그날 적격 집합 내 순위).
5. 계약 점검: 저장소 정본 계약 `virtual_books.contract_exit_v2`(TP 12 / SL −25 / 보유 7, KR be_lock=False, fee 0.25).
   신호 vs 같은 계약의 규모매칭·커버리지 벤치. 겹침 제거는 7세션.
6. **합격선(규칙별, H20, vs 커버리지 벤치)**: OOS 초과 > 0 · OOS NW t ≥ 2.64 · IS 같은 부호(양수).
   플라시보·연도·분할·계약은 해석용(합격 판정에 쓰지 않음).

## 사전등록 v3 — R3 전용 (2026-09-30 20:2x KST, eval3 계산 전 고정 — 결과를 본 뒤 수정 금지)
배경: v2 사후 진단에서 신호 이전 날짜 플라시보가 주가 선행 상승으로 부풀었다. 이전 날짜를 빼고 R3 정보만 잰다.
R3 신호·겹침 제거·적격·진입/청산·비용·커버리지 벤치(90일, 같은 dvol20 십분위, 셀 ≥10)는 v2 그대로. 거래 표본도 v2 trades()와 같다.
1. 거래별 신호 초과 = net − 커버리지 벤치(기준일 i).
2. 플라시보 A(주): 같은 종목, **같은 달력월 안에서 i 이후** 세션 k(k > i). 그 종목의 R1·R2·R3 신호 기준일 제외,
   적격·수익·커버리지 벤치 있는 날만. 플라시보 초과 = 해당 k 전부의 (net_k − 커버리지 벤치_k) 평균(추출 없음, 결정적).
   후보가 없으면 거래 제외.
3. 플라시보 B: k ∈ [i+1, i+20] 세션(같은 제외·조건).
4. 거래별 차이 = 신호 초과 − 플라시보 초과. 신호일(기준일)별 평균 → **날짜 클러스터 t(평이한 t, 주)**. NW t(lag H−1)는 참고로 병기.
5. **합격(H20, 플라시보 A)**: OOS 차이 > 0 · 날짜 클러스터 t ≥ 2.0 · IS 차이 > 0. B·층·계약은 보고용.
6. 층(보고용, 합격에 쓰지 않음): (a) 같은 날 매수 의견으로 신규 커버한 증권사 수 1 vs ≥2,
   (b) 목표가 괴리 = max(해당 증권사 `hts_goal_prc`/`stck_prdy_clpr`) − 1(원자료 같은 행), **IS 거래로 정한 삼분위 경계**를 전 구간에 적용,
   (c) 시장 KOSPI/KOSDAQ(instiwatch corp_cls).
7. 계약판: 정본 계약(TP12/SL25/보유7, be_lock 없음) 값과 커버리지 계약 벤치로 같은 A/B 차이(겹침 제거 7세션, B 창은 동일하게 +1~+20).

## 사전등록 v4 — 증권사 이름 정규화 (2026-09-30 22:4x KST, 운영자 승인 옵션 B, eval4 계산 전 고정 — 결과를 본 뒤 수정 금지)
배경: KIS `mbcr_name`은 사명 변경·표시명 축약으로 바뀐다(예: 현대차증권→현대차). 원 문자열 비교에서는 이름이 바뀐 증권사가
'신규 커버'로 잡힌다(forward 298040 2026-09-30 실례). v1~v3는 원 문자열로 비교했다.
1. 맵: `data/analysis/kr_target_price_20260930/broker_map.json`의 `map`(raw → canonical, 11개, 38개 원 이름 → 27개 정규 이름)을
   **계산 전에 고정**한다. 표에 없는 이름은 그대로 통과. 근거(인계 간격·겹침 구간 표시일수·같은 종목 같은 날 동시 등장 0건·
   공개 사명변경 기록)는 파일에 쌍별로 기록. 케이프투자→상상인은 기각(별개 법인). 목표가 연속성은 판별력이 없어 기준에서 뺐다.
   매핑: 현대차투자·현대차증권→현대차, 하나금융투자→하나증권, 이베스트투자→LS증권, 신한금융투자→신한투자증권,
   미래에셋대우·미래에셋증권→미래에셋, 메리츠종금→메리츠, KTB투자→다올투자, DB금융투자→DB증권, 하이투자→IM증권.
2. 적용 범위: `reports.csv`를 읽은 직후 broker를 정규화하고 (종목, 정규 이름, 날짜)당 첫 행으로 다시 중복 제거한다.
   **R1·R2·R3 전부** 정규 이름으로 계산한다(개정 사슬이 사명 변경을 넘어 이어짐 → R1·R2 신호가 바뀌고 v3 플라시보 제외 집합도 바뀐다).
   커버리지 마스크는 (종목, 날짜)만 쓰므로 이름과 무관. v3 층(괴리·시장·증권사 수)은 재계산하지 않는다.
3. 재계산: R3만. (a) v2 주 판정 = H20 vs 커버리지 벤치(NW t lag 19, 거래·신호일) + 규모매칭(v1 주 벤치) 병기,
   (b) v3 = H20 차이 A(신호 − 같은 달 사후 플라시보, 날짜 클러스터 t, NW 병기) + 차이 B. 창·비용·적격·겹침 제거·IS/OOS 구간 동일.
   정본 계약판(TP12/SL25/7)은 재계산하지 않는다.
4. 재현 게이트: 같은 코드로 맵 없이(원 문자열) 돌려 v2 R3(OOS +1.43%, t 3.10, n 1220)·v3(OOS +0.72%, t 2.05, n 1110/515일)을
   정확히 재현해야 v4 수치를 인정한다.
5. 인공물(rename artifact): 원 이름 R3 (종목, 날짜) 이벤트 중 정규 이름 R3 집합에 없는 것(정규 ⊆ 원). 보고: 이벤트 수·비중,
   (종목, 날짜, 증권사) 단위 수, 원 H20 거래 표본 중 비중(IS/OOS), 그 거래들의 커버리지 초과·v3 차이 A(원 실행 표본 기준).
6. **합격(불변)**: v3 차이 A OOS > 0 · 날짜 클러스터 t ≥ 2.0 · IS 같은 부호(>0). v2 커버리지 판정선(OOS>0, NW t ≥ 2.64, IS>0)도 병기.
   결과 산출: `eval4_result.md`(eval2/eval3 산출물은 덮어쓰지 않음).

사용:
  python tools/research/research_kr_target_price_20260930.py eval4
  python tools/research/research_kr_target_price_20260930.py eval3
  python tools/research/research_kr_target_price_20260930.py eval2
  python tools/research/research_kr_target_price_20260930.py universe
  python tools/research/research_kr_target_price_20260930.py backfill [--max-calls N] [--dry-run]
  python tools/research/research_kr_target_price_20260930.py build
  python tools/research/research_kr_target_price_20260930.py eval
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

OUT = ROOT / "data" / "analysis" / "kr_target_price_20260930"
RAW = OUT / "raw"
CKPT = OUT / "checkpoint.json"
UNIV = OUT / "universe.json"
LOG = OUT / "backfill.log"
LOCK = OUT / "backfill.lock"
REPORTS = OUT / "reports.csv"
REPORT_MD = ROOT / "docs" / "reports" / "kr_target_price_20260930.md"
YF_CACHE = ROOT / "data" / "price_backfill_yf" / "kr_fallen_price_cache_2018_2026.json"
YF_IDX = ROOT / "data" / "price_backfill_yf" / "kr"
KR_DIR = ROOT / "data" / "price" / "kr"

KST = ZoneInfo("Asia/Seoul")
PATH = "/uapi/domestic-stock/v1/quotations/invest-opinion"
TR_ID = "FHKST663300C0"
START = "20180401"
CAP = 100
GAP_SEC = 1.1
NIGHT_MAX = 6000
MAX_CONSEC_FAIL = 5


# ─────────────────────────── 공통 ───────────────────────────
def now_kst() -> datetime:
    return datetime.now(KST)


def log(msg: str) -> None:
    line = f"{now_kst().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def atomic_write(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    for k in range(20):  # Windows: 다른 프로세스가 읽는 중이면 replace가 거부된다 → 재시도
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.25 * (k + 1))
    os.replace(tmp, path)


def night_key(t: datetime) -> str:
    return (t - timedelta(days=1)).strftime("%Y-%m-%d") if t.hour < 8 else t.strftime("%Y-%m-%d")


def window_state(t: datetime) -> str:
    """'ok' | 'skip'(22:10~22:35 대기) | 'closed'(08:00~16:05)."""
    hm = t.hour * 60 + t.minute
    if 8 * 60 <= hm < 16 * 60 + 5:
        return "closed"
    if 22 * 60 + 10 <= hm < 22 * 60 + 35:
        return "skip"
    return "ok"


# ─────────────────────────── universe ───────────────────────────
def cmd_universe(_a) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for p in sorted(KR_DIR.glob("kr_*.csv")):
        t = p.stem[3:]
        bars = []
        with p.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.reader(fh):
                if len(r) >= 6 and r[0][:2] == "20":
                    try:
                        bars.append((r[0], float(r[4]), float(r[5])))
                    except ValueError:
                        pass
        bars.sort()
        last = bars[-20:]
        dv = sum(c * v for _, c, v in last) / len(last) / 1e8 if last else 0.0
        rows.append({"ticker": t, "dvol20_eok": round(dv, 2), "last": bars[-1][0] if bars else ""})
    rows.sort(key=lambda r: -r["dvol20_eok"])
    tickers = {r["ticker"] for r in rows}
    # 생존편향 점검용: yf 장기 캐시 중 2026-09 전 봉이 끊긴 종목 + 프로브 상폐 사례 091990
    extra = []
    try:
        m = json.loads(YF_CACHE.read_text(encoding="utf-8"))
        for t, b in m.items():
            if b and b[-1]["d"] < "2026-09-01":
                extra.append({"ticker": t, "dvol20_eok": 0.0, "last": b[-1]["d"], "survivorship_probe": True})
    except Exception as e:  # noqa: BLE001
        log(f"yf 캐시 읽기 실패: {e}")
    if "091990" not in tickers:
        extra.append({"ticker": "091990", "dvol20_eok": 0.0, "last": "delisted", "survivorship_probe": True})
    seen = set()
    order = []
    for r in rows + [x for x in extra if x["ticker"] not in tickers]:
        if r["ticker"] not in seen:
            seen.add(r["ticker"])
            order.append(r)
    atomic_write(UNIV, {"generated_kst": now_kst().isoformat(timespec="seconds"), "n": len(order), "tickers": order})
    print(f"universe {len(order)} (price/kr {len(rows)}, survivorship probe {len(order) - len(rows)})")
    print("top10", [(r["ticker"], r["dvol20_eok"]) for r in order[:10]])
    return 0


# ─────────────────────────── backfill ───────────────────────────
def _d(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:]))


def _s(d: date) -> str:
    return d.strftime("%Y%m%d")


def split_window(d1: str, d2: str) -> list[list[str]]:
    a, b = _d(d1), _d(d2)
    if a >= b:
        return []
    if a.year != b.year:
        out = []
        for y in range(a.year, b.year + 1):
            s, e = max(a, date(y, 1, 1)), min(b, date(y, 12, 31))
            out.append([_s(s), _s(e)])
        return out
    qa, qb = (a.month - 1) // 3, (b.month - 1) // 3
    if qa != qb:
        out = []
        for q in range(qa, qb + 1):
            s = max(a, date(a.year, 3 * q + 1, 1))
            e = min(b, (date(a.year + 1, 1, 1) if q == 3 else date(a.year, 3 * q + 4, 1)) - timedelta(days=1))
            out.append([_s(s), _s(e)])
        return out
    if a.month != b.month:
        out = []
        for mth in range(a.month, b.month + 1):
            s = max(a, date(a.year, mth, 1))
            e = min(b, (date(a.year + 1, 1, 1) if mth == 12 else date(a.year, mth + 1, 1)) - timedelta(days=1))
            out.append([_s(s), _s(e)])
        return out
    mid = a + (b - a) // 2
    return [[_s(a), _s(mid)], [_s(mid + timedelta(days=1)), _s(b)]]


def _load_env() -> None:
    from dotenv import load_dotenv

    load_dotenv(dotenv_path=str(ROOT / ".env.live"), override=True)
    try:
        cfg = json.loads((ROOT / "config" / "v2_start_config.json").read_text(encoding="utf-8"))
        for k, v in (cfg.get("env_overrides") or {}).items():
            if str(k).startswith("KIS_"):
                os.environ[str(k)] = str(v)
    except Exception:
        pass


class Stop(Exception):
    pass


def _pid_alive(pid: int) -> bool:
    try:
        import ctypes

        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259
    except Exception:
        return False


def cmd_backfill(a) -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    RAW.mkdir(parents=True, exist_ok=True)
    if not UNIV.exists():
        cmd_universe(a)
    if LOCK.exists():
        try:
            pid = int(json.loads(LOCK.read_text(encoding="utf-8")).get("pid", 0))
        except Exception:
            pid = 0
        if pid and pid != os.getpid() and _pid_alive(pid):
            log(f"STOP_ALREADY_RUNNING pid={pid}")
            return 4
    atomic_write(LOCK, {"pid": os.getpid(), "started_kst": now_kst().isoformat(timespec="seconds")})
    try:
        return _backfill(a)
    finally:
        try:
            LOCK.unlink()
        except Exception:
            pass


def _backfill(a) -> int:
    univ = json.loads(UNIV.read_text(encoding="utf-8"))["tickers"]
    ck = json.loads(CKPT.read_text(encoding="utf-8")) if CKPT.exists() else {"tickers": {}, "nights": {}, "status": ""}
    today = now_kst().strftime("%Y%m%d")
    _load_env()
    import kis_api  # noqa: E402
    import requests

    base = kis_api.get_kis_market_profile("KR").base_url
    if a.dry_run:
        todo = [u["ticker"] for u in univ if ck["tickers"].get(u["ticker"], {}).get("status") != "done"]
        print(f"dry-run base_host={base.split('//')[-1].split(':')[0]} is_paper={kis_api.IS_PAPER} todo={len(todo)} "
              f"window={window_state(now_kst())} token_cached={bool((kis_api.load_token('KR') or {}).get('access_token'))}")
        print("split 20180401-20260930 ->", split_window("20180401", "20260930")[:3], "...")
        print("split 20210101-20211231 ->", split_window("20210101", "20211231"))
        print("split 20210401-20210630 ->", split_window("20210401", "20210630"))
        print("split 20210401-20210430 ->", split_window("20210401", "20210430"))
        return 0

    log(f"START pid={os.getpid()} night={night_key(now_kst())} used={ck['nights'].get(night_key(now_kst()), 0)}")
    state = {"last": 0.0, "consec": 0}
    max_calls = min(NIGHT_MAX, a.max_calls or NIGHT_MAX)

    def save():
        atomic_write(CKPT, ck)

    def call(ticker: str, d1: str, d2: str):
        while True:
            t = now_kst()
            ws = window_state(t)
            if ws == "closed":
                raise Stop("STOP_OUTSIDE_WINDOW")
            if ws == "skip":
                save()
                log("22:10~22:35 대기")
                time.sleep(60)
                continue
            break
        nk = night_key(now_kst())
        used = ck["nights"].get(nk, 0)
        if used >= max_calls:
            raise Stop(f"STOP_NIGHT_BUDGET night={nk} used={used}")
        tok = kis_api.load_token("KR")
        token = (tok or {}).get("access_token") or ""
        if not token.strip():
            raise Stop("STOP_TOKEN_UNLOADABLE")
        wait = GAP_SEC - (time.monotonic() - state["last"])
        if wait > 0:
            time.sleep(wait)
        headers = kis_api._headers(token, TR_ID, "KR")
        params = {"FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "16633", "FID_INPUT_ISCD": ticker,
                  "FID_INPUT_DATE_1": d1, "FID_INPUT_DATE_2": d2}
        ck["nights"][nk] = used + 1
        try:
            resp = requests.get(base + PATH, headers=headers, params=params, timeout=15)
            state["last"] = time.monotonic()
            resp.encoding = "utf-8"
            body = resp.json()
        except Exception as e:  # noqa: BLE001
            state["last"] = time.monotonic()
            return None, f"EXC {type(e).__name__}"
        msg = str(body.get("msg_cd", ""))
        if msg.startswith("EGW0012"):
            raise Stop(f"STOP_TOKEN_REJECTED {msg}")
        if resp.status_code != 200 or str(body.get("rt_cd")) != "0":
            if msg == "EGW00201":
                time.sleep(3)
            return None, f"http={resp.status_code} rt={body.get('rt_cd')} msg={msg}"
        out = body.get("output") or []
        if isinstance(out, dict):
            out = [out]
        return out, "ok"

    stop_reason = "DONE_ALL"
    n_done_run = 0
    try:
        for u in univ:
            t = u["ticker"]
            st = ck["tickers"].get(t)
            if st and st.get("status") == "done":
                continue
            if not st:
                st = {"status": "partial", "calls": 0, "rows": 0, "complete": True,
                      "pending": [[START, today]], "done_windows": [], "fail": 0}
                ck["tickers"][t] = st
                (RAW / f"{t}.jsonl").unlink(missing_ok=True)
            else:  # 크래시 복구: 완료 창의 행만 남긴다
                st["fail"] = 0
                st["status"] = "partial"
                keep = {f"{w[0]}-{w[1]}" for w in st["done_windows"]}
                p = RAW / f"{t}.jsonl"
                if p.exists():
                    lines = [ln for ln in p.read_text(encoding="utf-8").splitlines()
                             if ln and json.loads(ln).get("_w") in keep]
                    p.write_text("".join(ln + "\n" for ln in lines), encoding="utf-8")
            while st["pending"]:
                d1, d2 = st["pending"][0]
                rows, info = call(t, d1, d2)
                st["calls"] += 1
                if rows is None:
                    state["consec"] += 1
                    st["fail"] += 1
                    log(f"FAIL {t} {d1}-{d2} {info} consec={state['consec']}")
                    if state["consec"] >= MAX_CONSEC_FAIL:
                        raise Stop("STOP_CONSEC_FAIL")
                    if st["fail"] >= 3:
                        st["status"] = "error"
                        break
                    time.sleep(5)
                    continue
                state["consec"] = 0
                st["pending"].pop(0)
                kids = split_window(d1, d2) if len(rows) >= CAP else []
                if len(rows) >= CAP and not kids:
                    st["complete"] = False
                if kids:
                    st["pending"][0:0] = kids
                else:
                    w = f"{d1}-{d2}"
                    with (RAW / f"{t}.jsonl").open("a", encoding="utf-8") as fh:
                        for r in rows:
                            r = dict(r)
                            r["_w"] = w
                            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
                    st["done_windows"].append([d1, d2])
                    st["rows"] += len(rows)
                save()
            if st["status"] != "error":
                st["status"] = "done"
                st["done_kst"] = now_kst().isoformat(timespec="seconds")
            n_done_run += 1
            save()
            if n_done_run % 50 == 0:
                done = sum(1 for v in ck["tickers"].values() if v.get("status") == "done")
                log(f"progress done={done}/{len(univ)} night_calls={ck['nights'].get(night_key(now_kst()), 0)}")
    except Stop as e:
        stop_reason = str(e)
    done = sum(1 for v in ck["tickers"].values() if v.get("status") == "done")
    err = sum(1 for v in ck["tickers"].values() if v.get("status") == "error")
    ck["status"] = stop_reason
    ck["updated_kst"] = now_kst().isoformat(timespec="seconds")
    save()
    log(f"{stop_reason} done={done}/{len(univ)} error={err} night_calls={ck['nights'].get(night_key(now_kst()), 0)} "
        f"total_calls={sum(ck['nights'].values())}")
    return 0


# ─────────────────────────── build ───────────────────────────
BUY_KEYS = ("매수", "Buy", "BUY", "buy", "Outperform", "OUTPERFORM", "비중확대")


def cmd_build(_a) -> int:
    ck = json.loads(CKPT.read_text(encoding="utf-8"))
    out = []
    for t, st in ck["tickers"].items():
        if st.get("status") != "done":
            continue
        p = RAW / f"{t}.jsonl"
        if not p.exists():
            continue
        seen = set()
        for ln in p.read_text(encoding="utf-8").splitlines():
            if not ln:
                continue
            r = json.loads(ln)
            d = r.get("stck_bsop_date", "")
            b = (r.get("mbcr_name") or "").strip()
            if not d or not b or (t, b, d) in seen:
                continue
            seen.add((t, b, d))
            try:
                g = float(r.get("hts_goal_prc") or 0)
            except ValueError:
                g = 0.0
            out.append((t, f"{d[:4]}-{d[4:6]}-{d[6:]}", b, g, (r.get("invt_opnn") or "").strip(),
                        1 if st.get("complete", True) else 0))
    out.sort()
    with REPORTS.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "date", "broker", "target", "opinion", "ticker_complete"])
        w.writerows(out)
    print(f"reports {len(out)} rows, tickers {len({r[0] for r in out})} -> {REPORTS}")
    return 0


# ─────────────────────────── eval ───────────────────────────
def nw_t(xs: list[float], lag: int):
    n = len(xs)
    if n < 10:
        return None
    m = sum(xs) / n
    e = [x - m for x in xs]
    g0 = sum(v * v for v in e) / n
    s = g0
    for k in range(1, min(lag, n - 1) + 1):
        gk = sum(e[i] * e[i - k] for i in range(k, n)) / n
        s += 2 * (1 - k / (lag + 1)) * gk
    if s <= 0:
        return None
    return m / math.sqrt(s / n)


def cmd_eval(_a) -> int:
    import numpy as np
    import pandas as pd

    cmd_build(_a)
    ck = json.loads(CKPT.read_text(encoding="utf-8"))
    univ = json.loads(UNIV.read_text(encoding="utf-8"))["tickers"]
    n_univ = len(univ)
    n_done = sum(1 for v in ck["tickers"].values() if v.get("status") == "done")
    partial = n_done < n_univ
    rep = pd.read_csv(REPORTS, dtype={"ticker": str})

    # 가격 행렬
    m = json.loads(YF_CACHE.read_text(encoding="utf-8"))
    idx = pd.read_csv(YF_IDX / "kr_069500.csv", encoding="utf-8-sig")
    cal = list(idx["date"])
    ci = {d: i for i, d in enumerate(cal)}
    T = sorted(m)
    ti = {t: j for j, t in enumerate(T)}
    O = np.full((len(cal), len(T)), np.nan)
    C = O.copy()
    A = O.copy()
    for t, bars in m.items():
        j = ti[t]
        for b in bars:
            i = ci.get(b["d"])
            if i is not None and b["o"] > 0 and b["c"] > 0:
                O[i, j], C[i, j], A[i, j] = b["o"], b["c"], b["c"] * b["v"]
    have = ~np.isnan(C)
    nbars = np.cumsum(have, axis=0)
    dv20 = pd.DataFrame(A).rolling(20, min_periods=20).mean().to_numpy() / 1e8
    elig = have & (nbars >= 25) & (C >= 1000) & (A / 1e8 >= 20) & ~np.isnan(dv20)
    COST = 0.25
    HS = (5, 20, 60)
    nD = len(cal)

    def fwd(H):  # 진입 i+1 시가 → i+H 종가 (기준일 i)
        R = np.full_like(C, np.nan)
        R[: nD - H] = (C[H:] / O[1: nD - H + 1] - 1) * 100
        return R

    RET = {H: fwd(H) for H in HS}
    # 규모매칭 계약 벤치: 기준일 i, 십분위 → 평균(ret−cost)
    DEC = np.full(C.shape, -1, dtype=int)
    for i in range(nD):
        js = np.where(elig[i])[0]
        if len(js) == 0:
            continue
        order = js[np.argsort(dv20[i, js], kind="stable")]
        DEC[i, order] = np.minimum(9, np.arange(len(order)) * 10 // len(order))
    SZ = {}
    for H in HS:
        B = np.full((nD, 10), np.nan)
        for i in range(nD):
            for dcl in range(10):
                js = np.where(DEC[i] == dcl)[0]
                v = RET[H][i, js]
                v = v[~np.isnan(v)]
                if len(v) >= 20:
                    B[i, dcl] = v.mean() - COST
        SZ[H] = B
    kq = pd.read_csv(YF_IDX / "kr_229200.csv", encoding="utf-8-sig").set_index("date")
    KO, KC = idx["open"].to_numpy(), idx["close"].to_numpy()
    QO = kq.reindex(cal)["open"].to_numpy()
    QC = kq.reindex(cal)["close"].to_numpy()

    def idx_ret(Oa, Ca, i, H):
        if i + H >= nD or np.isnan(Oa[i + 1]) or np.isnan(Ca[i + H]):
            return np.nan
        return (Ca[i + H] / Oa[i + 1] - 1) * 100

    # 신호일 → 기준 세션 인덱스(D 이하 마지막 세션)
    import bisect

    def base_idx(dstr):
        k = bisect.bisect_right(cal, dstr) - 1
        return k if k >= 0 else None

    # 개정 계산
    rep = rep.sort_values(["ticker", "broker", "date"])
    rev_rows, split_guard, first_rows = [], 0, []
    for (t, b), g in rep.groupby(["ticker", "broker"], sort=False):
        prev_d, prev_g = None, None
        first = True
        for d, tg, op in zip(g["date"], g["target"], g["opinion"].fillna("")):
            if first:
                first_rows.append((t, d, b, op))
                first = False
            if tg and tg > 0:
                if prev_g and (datetime.fromisoformat(d) - datetime.fromisoformat(prev_d)).days <= 365:
                    rv = tg / prev_g - 1
                    if abs(rv) > 0.60:
                        split_guard += 1
                    else:
                        rev_rows.append((t, d, rv))
                prev_d, prev_g = d, tg
    rv = pd.DataFrame(rev_rows, columns=["ticker", "date", "rev"])
    daily = rv.groupby(["ticker", "date"]).agg(up=("rev", lambda s: int((s > 0).sum())),
                                               down=("rev", lambda s: int((s < 0).sum())),
                                               mean_rev=("rev", "mean"), n=("rev", "size")).reset_index()

    sig = {"R1": [], "R2": [], "R3": []}
    # R1: 세션 단위 5세션 롤링 순상향
    for t, g in daily.groupby("ticker"):
        net_by_i = defaultdict(int)
        for d, u, dn in zip(g["date"], g["up"], g["down"]):
            k = base_idx(d)
            if k is not None:
                net_by_i[k] += u - dn
        ks = sorted(net_by_i)
        prev_true = False
        for k in range(ks[0], ks[-1] + 1) if ks else []:
            s5 = sum(net_by_i.get(x, 0) for x in range(k - 4, k + 1))
            cond = s5 >= 2
            if cond and not prev_true and k in net_by_i:
                sig["R1"].append((t, cal[k]))
            prev_true = cond
    for t, d, mr in zip(daily["ticker"], daily["date"], daily["mean_rev"]):
        if mr >= 0.10:
            sig["R2"].append((t, d))
    for t, d, b, op in first_rows:
        if d >= "2019-04-02" and any(k in op for k in BUY_KEYS):
            sig["R3"].append((t, d))

    def evaluate(rule, H):
        trades = []
        last_i = {}
        for t, d in sorted(set(sig[rule]), key=lambda x: (x[1], x[0])):
            j = ti.get(t)
            i = base_idx(d)
            if j is None or i is None or i + H >= nD:
                continue
            if t in last_i and i - last_i[t] < H:
                continue
            if not elig[i, j] or np.isnan(RET[H][i, j]):
                continue
            dcl = DEC[i, j]
            b = SZ[H][i, dcl] if dcl >= 0 else np.nan
            if np.isnan(b):
                continue
            last_i[t] = i
            net = RET[H][i, j] - COST
            trades.append((cal[i], t, net, net - b, net - idx_ret(KO, KC, i, H), net - idx_ret(QO, QC, i, H)))
        return trades

    def summarize(trades, col, lo, hi, H):
        by = defaultdict(list)
        for tr in trades:
            if lo <= tr[0] <= hi and not np.isnan(tr[col]):
                by[tr[0]].append(tr[col])
        dates = sorted(by)
        xs = [float(np.mean(by[d])) for d in dates]
        if not xs:
            return None
        return {"n_tr": sum(len(by[d]) for d in dates), "n_d": len(xs), "mean": float(np.mean(xs)),
                "t": nw_t(xs, H - 1)}

    IS, OOS = ("2018-04-01", "2021-12-31"), ("2022-01-01", "2026-12-31")
    L = []
    verdicts = {}
    for rule in ("R1", "R2", "R3"):
        L += ["", f"### {rule} (신호 {len(set(sig[rule])):,}건)", "",
              "| H | 구간 | 거래 | 신호일 | 원 net | vs 규모매칭 | NW t | vs KOSPI200 | t | vs KOSDAQ150 | t |",
              "|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
        for H in HS:
            tr = evaluate(rule, H)
            for name, (lo, hi) in (("IS", IS), ("OOS", OOS)):
                s = [summarize(tr, c, lo, hi, H) for c in (2, 3, 4, 5)]
                if s[1] is None:
                    L.append(f"| {H} | {name} | 0 | - | - | - | - | - | - | - | - |")
                    continue

                def f(x):
                    return f"{x['mean']:+.2f}%" if x else "-"

                def ft(x):
                    return f"{x['t']:+.2f}" if x and x["t"] is not None else "-"
                L.append(f"| {H} | {name} | {s[1]['n_tr']} | {s[1]['n_d']} | {f(s[0])} | **{f(s[1])}** | **{ft(s[1])}** | "
                         f"{f(s[2])} | {ft(s[2])} | {f(s[3])} | {ft(s[3])} |")
            if H == 20:
                yr = defaultdict(list)
                for trr in tr:
                    yr[trr[0][:4]].append(trr)
                ys = {}
                for y, v in sorted(yr.items()):
                    s = summarize(v, 3, "0000", "9999", H)
                    if s:
                        ys[y] = s["mean"]
                is_s = summarize(tr, 3, *IS, H)
                oos_s = summarize(tr, 3, *OOS, H)
                pos = sum(1 for v in ys.values() if v > 0)
                ok = bool(oos_s and is_s and oos_s["mean"] > 0 and (oos_s["t"] or 0) >= 2.64
                          and pos > len(ys) / 2 and is_s["mean"] > 0)
                verdicts[rule] = {"pass": ok, "years": ys, "oos": oos_s, "is": is_s}
                L.append("")
                L.append("연도별(H20, vs 규모매칭, 신호일 평균): " + " · ".join(f"{y} {v:+.2f}%" for y, v in ys.items())
                         + f" → 양수 {pos}/{len(ys)}")
                L.append(f"**H20 판정: {'통과' if ok else '미통과'}**")

    comp = sum(1 for v in ck["tickers"].values() if v.get("status") == "done" and not v.get("complete", True))
    surv = sorted({u["ticker"] for u in univ if u.get("survivorship_probe")}
                  | {t for t, b in m.items() if b and b[-1]["d"] < "2026-09-01"})
    surv_rows = {t: ck["tickers"].get(t, {}).get("rows") for t in surv}
    head = [
        "# KR 목표가 개정 드리프트 — KIS invest-opinion 백필 검정 (2026-09-30)", "",
        f"스크립트 `tools/research/research_kr_target_price_20260930.py`(사전등록은 docstring, eval 전 고정). "
        f"산출 `data/analysis/kr_target_price_20260930/`.", "",
        f"**상태: {'PARTIAL — 백필 미완료, 대형주(dvol20 상위) 편중 표본' if partial else '백필 완료'}** "
        f"(종목 {n_done:,}/{n_univ:,} 완료, 호출 {sum(ck['nights'].values()):,}회, 창 분할 후에도 100행인 하루짜리 창 포함 종목 {comp}).",
        f"평가 {eval_stamp()} · 리포트 행 {len(rep):,} · 개정 {len(rv):,}건 · 분할가드(|rev|>60%) 제외 {split_guard}건.", "",
        f"생존편향: 상장폐지 종목은 KIS가 0행이고 yf 캐시도 현재 상장 기준(하한 3.3%). 점검 종목 행수 {surv_rows}.",
    ]
    atomic_md(head + ["", "## 결과"] + L + ["", "## 판정", json.dumps(
        {k: {"pass": v["pass"], "oos": v["oos"], "is": v["is"]} for k, v in verdicts.items()}, ensure_ascii=False)])
    print("\n".join(head + L))
    print(json.dumps({k: {"pass": v["pass"]} for k, v in verdicts.items()}))
    return 0


def cmd_eval2(_a) -> int:
    """사전등록 v2 — 커버리지 벤치·플라시보·연도·분할·정본 계약."""
    import bisect
    import contextlib
    import random
    import sqlite3

    import numpy as np
    import pandas as pd

    from tools.virtual_books import FEE_KR, HOLD_SESSIONS, SL, TP, contract_exit_v2

    rep = pd.read_csv(REPORTS, dtype={"ticker": str})
    m = json.loads(YF_CACHE.read_text(encoding="utf-8"))
    idx = pd.read_csv(YF_IDX / "kr_069500.csv", encoding="utf-8-sig")
    cal = list(idx["date"])
    ci = {d: i for i, d in enumerate(cal)}
    T = sorted(m)
    ti = {t: j for j, t in enumerate(T)}
    nD, nT = len(cal), len(T)
    O = np.full((nD, nT), np.nan)
    Hh, C, A = O.copy(), O.copy(), O.copy()
    for t, bars in m.items():
        j = ti[t]
        for b in bars:
            i = ci.get(b["d"])
            if i is not None and b["o"] > 0 and b["c"] > 0:
                O[i, j], Hh[i, j], C[i, j], A[i, j] = b["o"], b["h"], b["c"], b["c"] * b["v"]
    have = ~np.isnan(C)
    nbars = np.cumsum(have, axis=0)
    dv20 = pd.DataFrame(A).rolling(20, min_periods=20).mean().to_numpy() / 1e8
    elig = have & (nbars >= 25) & (C >= 1000) & (A / 1e8 >= 20) & ~np.isnan(dv20)
    COST = 0.25
    HS = (5, 20, 60)

    def base_idx(dstr):
        k = bisect.bisect_right(cal, dstr) - 1
        return k if k >= 0 else None

    RET = {}
    for H in HS:
        R = np.full_like(C, np.nan)
        R[: nD - H] = (C[H:] / O[1: nD - H + 1] - 1) * 100 - COST
        RET[H] = R
    DEC = np.full((nD, nT), -1, dtype=int)
    TER = np.full((nD, nT), -1, dtype=int)
    for i in range(nD):
        js = np.where(elig[i])[0]
        if len(js):
            order = js[np.argsort(dv20[i, js], kind="stable")]
            DEC[i, order] = np.minimum(9, np.arange(len(order)) * 10 // len(order))
            TER[i, order] = np.minimum(2, np.arange(len(order)) * 3 // len(order))
    # 커버리지: 리포트일 d → 세션 [d, d+89일]
    COV = np.zeros((nD, nT), dtype=bool)
    for t, g in rep.groupby("ticker"):
        j = ti.get(t)
        if j is None:
            continue
        for d in g["date"].unique():
            k0 = bisect.bisect_left(cal, d)
            end = (datetime.fromisoformat(d) + timedelta(days=89)).strftime("%Y-%m-%d")
            k1 = bisect.bisect_right(cal, end)
            COV[k0:k1, j] = True
    ALL = np.ones_like(COV)

    def bench(val, mask, minn):
        B = np.full((nD, 10), np.nan)
        for i in range(nD):
            for dcl in range(10):
                sel = (DEC[i] == dcl) & mask[i]
                v = val[i, sel]
                v = v[~np.isnan(v)]
                if len(v) >= minn:
                    B[i, dcl] = v.mean()
        return B

    SZ = {H: bench(RET[H], ALL, 20) for H in HS}
    CB = {H: bench(RET[H], COV, 10) for H in HS}
    # 정본 계약(TP/SL/보유) — D0 종가만 쓰므로 저가는 필요 없다(함수 규약)
    HC = HOLD_SESSIONS
    CON = np.full((nD, nT), np.nan)
    for i in range(nD - HC):
        for j in np.where(elig[i])[0]:
            e = O[i + 1, j]
            if np.isnan(e) or np.isnan(C[i + 1: i + 1 + HC, j]).any():
                continue
            win = [(cal[k], O[k, j], Hh[k, j], 0.0, C[k, j], 0.0) for k in range(i + 1, i + 1 + HC)]
            r_ = contract_exit_v2(e, win, fee=FEE_KR, be_lock=False, tp=TP, sl=SL, hold=HC)
            if r_ is not None:
                CON[i, j] = r_[0]
    SZC = bench(CON, ALL, 20)
    CBC = bench(CON, COV, 10)
    kq = pd.read_csv(YF_IDX / "kr_229200.csv", encoding="utf-8-sig").set_index("date")
    KO, KC = idx["open"].to_numpy(), idx["close"].to_numpy()
    QO, QC = kq.reindex(cal)["open"].to_numpy(), kq.reindex(cal)["close"].to_numpy()

    def idx_ret(Oa, Ca, i, H):
        if i + H >= nD or np.isnan(Oa[i + 1]) or np.isnan(Ca[i + H]):
            return np.nan
        return (Ca[i + H] / Oa[i + 1] - 1) * 100

    with contextlib.closing(sqlite3.connect("file:E:/code/instiwatch/data/instiwatch.db?mode=ro", uri=True,
                                            timeout=10)) as c:
        mkt = {r[0]: r[1] for r in c.execute(
            "SELECT stock_code, corp_cls FROM disclosures WHERE stock_code!='' GROUP BY stock_code")}

    # 신호 (v1과 동일 규약)
    rep = rep.sort_values(["ticker", "broker", "date"])
    rev_rows, first_rows = [], []
    for (t, b), g in rep.groupby(["ticker", "broker"], sort=False):
        prev_d, prev_g, first = None, None, True
        for d, tg, op in zip(g["date"], g["target"], g["opinion"].fillna("")):
            if first:
                first_rows.append((t, d, b, op))
                first = False
            if tg and tg > 0:
                if prev_g and (datetime.fromisoformat(d) - datetime.fromisoformat(prev_d)).days <= 365:
                    rv_ = tg / prev_g - 1
                    if abs(rv_) <= 0.60:
                        rev_rows.append((t, d, rv_))
                prev_d, prev_g = d, tg
    rv = pd.DataFrame(rev_rows, columns=["ticker", "date", "rev"])
    daily = rv.groupby(["ticker", "date"]).agg(up=("rev", lambda s: int((s > 0).sum())),
                                               down=("rev", lambda s: int((s < 0).sum())),
                                               mean_rev=("rev", "mean")).reset_index()
    sig = {"R1": [], "R2": [], "R3": []}
    for t, g in daily.groupby("ticker"):
        net_by_i = defaultdict(int)
        for d, u, dn in zip(g["date"], g["up"], g["down"]):
            k = base_idx(d)
            if k is not None:
                net_by_i[k] += u - dn
        ks = sorted(net_by_i)
        prev_true = False
        for k in range(ks[0], ks[-1] + 1) if ks else []:
            cond = sum(net_by_i.get(x, 0) for x in range(k - 4, k + 1)) >= 2
            if cond and not prev_true and k in net_by_i:
                sig["R1"].append((t, cal[k]))
            prev_true = cond
    for t, d, mr in zip(daily["ticker"], daily["date"], daily["mean_rev"]):
        if mr >= 0.10:
            sig["R2"].append((t, d))
    for t, d, b, op in first_rows:
        if d >= "2019-04-02" and any(k in op for k in BUY_KEYS):
            sig["R3"].append((t, d))

    month_sessions = defaultdict(list)
    for i, d in enumerate(cal):
        month_sessions[d[:7]].append(i)

    def trades(rule, H, val, sz, cb):
        out, last_i = [], {}
        sig_i = defaultdict(set)
        for t, d in sig[rule]:
            k = base_idx(d)
            if k is not None:
                sig_i[t].add(k)
        rng = random.Random(20260930)
        rng2 = random.Random(930)  # 사후 진단 전용(주 추출 순서 불변)
        for t, d in sorted(set(sig[rule]), key=lambda x: (x[1], x[0])):
            j, i = ti.get(t), base_idx(d)
            if j is None or i is None or i + H >= nD or (t in last_i and i - last_i[t] < H):
                continue
            if not elig[i, j] or np.isnan(val[i, j]):
                continue
            dcl = DEC[i, j]
            b_sz, b_cb = sz[i, dcl], cb[i, dcl]
            if np.isnan(b_sz) or np.isnan(b_cb):
                continue
            last_i[t] = i
            cand = [k for k in month_sessions[cal[i][:7]] if k not in sig_i[t] and k + H < nD and elig[k, j]
                    and not np.isnan(val[k, j]) and DEC[k, j] >= 0 and not np.isnan(cb[k, DEC[k, j]])]
            pl = np.nan
            if cand:
                ks = [rng.choice(cand) for _ in range(20)]
                pl = float(np.mean([val[k, j] - cb[k, DEC[k, j]] for k in ks]))
            diag = {}
            for side, cs in (("pre", [k for k in cand if k < i]), ("post", [k for k in cand if k > i])):
                diag[side] = (float(np.mean([val[k, j] - cb[k, DEC[k, j]] for k in
                                             [rng2.choice(cs) for _ in range(20)]])) if cs else np.nan)
            net = val[i, j]
            out.append({"d": cal[i], "t": t, "net": net, "sz": net - b_sz, "cov": net - b_cb, "pl": pl,
                        "pl_pre": diag["pre"], "pl_post": diag["post"],
                        "dpl": (net - b_cb) - pl if not np.isnan(pl) else np.nan,
                        "k200": net - idx_ret(KO, KC, i, H), "kq": net - idx_ret(QO, QC, i, H),
                        "mkt": {"Y": "KOSPI", "K": "KOSDAQ"}.get(mkt.get(t), "기타"), "ter": int(TER[i, j])})
        return out

    def summ(tr, col, lo="0000", hi="9999", H=20, filt=None):
        by = defaultdict(list)
        for x in tr:
            if lo <= x["d"] <= hi and (filt is None or filt(x)) and not np.isnan(x[col]):
                by[x["d"]].append(x[col])
        ds = sorted(by)
        xs = [float(np.mean(by[d])) for d in ds]
        if not xs:
            return None
        return {"n": sum(len(by[d]) for d in ds), "nd": len(xs), "m": float(np.mean(xs)), "t": nw_t(xs, H - 1)}

    def fm(s):
        if not s:
            return "-"
        tt = f"{s['t']:+.2f}" if s["t"] is not None else "-"
        return f"{s['m']:+.2f}% (t {tt}, n {s['n']})"

    IS, OOS = ("2018-04-01", "2021-12-31"), ("2022-01-01", "2026-12-31")
    L = ["", "## v2 — 커버리지 벤치·플라시보·연도·분할·정본 계약 (사전등록 v2, docstring)", "",
         f"평가 {eval_stamp()}. 커버 = 최근 90일 리포트 ≥1건. 값은 신호일 평균 초과 (NW t, 거래수).", "",
         "### 1. 주 판정 — H20 vs 커버리지 벤치", "",
         "| 규칙 | IS | OOS | 판정 |", "|---|---|---|---|"]
    verdict = {}
    TR = {r: {H: trades(r, H, RET[H], SZ[H], CB[H]) for H in HS} for r in sig}
    for r in sig:
        si, so = summ(TR[r][20], "cov", *IS), summ(TR[r][20], "cov", *OOS)
        ok = bool(si and so and so["m"] > 0 and (so["t"] or 0) >= 2.64 and si["m"] > 0)
        verdict[r] = ok
        L.append(f"| {r} | {fm(si)} | {fm(so)} | **{'통과' if ok else '미통과'}** |")
    L += ["", "보조 H5/H60 vs 커버리지 벤치:", "", "| 규칙 | H | IS | OOS |", "|---|---:|---|---|"]
    for r in sig:
        for H in (5, 60):
            L.append(f"| {r} | {H} | {fm(summ(TR[r][H], 'cov', *IS, H=H))} | {fm(summ(TR[r][H], 'cov', *OOS, H=H))} |")
    L += ["", "### 2. 같은 종목 플라시보 (H20, 같은 달 비신호일 20회 추출, 커버리지 벤치 초과)", "",
          "| 규칙 | 구간 | 신호 초과 | 플라시보 초과 | 차이(신호−플라시보) |", "|---|---|---|---|---|"]
    for r in sig:
        for name, (lo, hi) in (("IS", IS), ("OOS", OOS)):
            L.append(f"| {r} | {name} | {fm(summ(TR[r][20], 'cov', lo, hi))} | {fm(summ(TR[r][20], 'pl', lo, hi))} | "
                     f"**{fm(summ(TR[r][20], 'dpl', lo, hi))}** |")
    L += ["", "사후 진단(사전등록 아님): 플라시보를 신호일 **이전** 날짜만/**이후** 날짜만으로 나눈 커버리지 벤치 초과.", "",
          "| 규칙 | 구간 | 신호 | 플라시보-이전 | 플라시보-이후 |", "|---|---|---|---|---|"]
    for r in sig:
        for name, (lo, hi) in (("IS", IS), ("OOS", OOS)):
            L.append(f"| {r} | {name} | {fm(summ(TR[r][20], 'cov', lo, hi))} | {fm(summ(TR[r][20], 'pl_pre', lo, hi))} | "
                     f"{fm(summ(TR[r][20], 'pl_post', lo, hi))} |")
    L += ["", "### 3. 연도별 H20 (신호일 평균 초과)", "",
          "| 규칙 | 연도 | 거래 | vs KOSPI200 | vs KOSDAQ150 | vs 규모매칭 | vs 커버리지 |",
          "|---|---|---:|---:|---:|---:|---:|"]
    for r in sig:
        for y in sorted({x["d"][:4] for x in TR[r][20]}):
            cells = [summ(TR[r][20], c, f"{y}-01-01", f"{y}-12-31") for c in ("k200", "kq", "sz", "cov")]
            L.append(f"| {r} | {y} | {cells[3]['n'] if cells[3] else 0} | "
                     + " | ".join(f"{c['m']:+.2f}%" if c else "-" for c in cells) + " |")
    L += ["", "### 4. 분할 (H20, vs 커버리지 벤치)", "", "| 규칙 | 분할 | IS | OOS |", "|---|---|---|---|"]
    splits = (("KOSPI", lambda x: x["mkt"] == "KOSPI"), ("KOSDAQ", lambda x: x["mkt"] == "KOSDAQ"),
              ("dvol 하위1/3", lambda x: x["ter"] == 0), ("dvol 중위1/3", lambda x: x["ter"] == 1),
              ("dvol 상위1/3", lambda x: x["ter"] == 2))
    for r in sig:
        for lab, f in splits:
            L.append(f"| {r} | {lab} | {fm(summ(TR[r][20], 'cov', *IS, filt=f))} | "
                     f"{fm(summ(TR[r][20], 'cov', *OOS, filt=f))} |")
    nm = sum(1 for x in TR["R1"][20] if x["mkt"] == "기타")
    L += ["", f"시장 미분류(기타) R1 거래 {nm}건."]
    L += ["", f"### 5. 정본 계약(TP {TP:g} / SL {SL:g} / 보유 {HC}, be_lock 없음, fee {FEE_KR})", "",
          "| 규칙 | 구간 | 원 net | vs 규모매칭 계약 | vs 커버리지 계약 |", "|---|---|---|---|---|"]
    for r in sig:
        tr = trades(r, HC, CON, SZC, CBC)
        for name, (lo, hi) in (("IS", IS), ("OOS", OOS)):
            L.append(f"| {r} | {name} | {fm(summ(tr, 'net', lo, hi, H=HC))} | {fm(summ(tr, 'sz', lo, hi, H=HC))} | "
                     f"{fm(summ(tr, 'cov', lo, hi, H=HC))} |")
    (OUT / "eval2_result.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(json.dumps(verdict))
    return 0


def cmd_eval3(_a) -> int:
    """사전등록 v3 — R3 신호 − 같은 종목 사후 플라시보."""
    import bisect
    import contextlib
    import random
    import sqlite3

    import numpy as np
    import pandas as pd

    from tools.virtual_books import FEE_KR, HOLD_SESSIONS, SL, TP, contract_exit_v2

    rep = pd.read_csv(REPORTS, dtype={"ticker": str})
    m = json.loads(YF_CACHE.read_text(encoding="utf-8"))
    idx = pd.read_csv(YF_IDX / "kr_069500.csv", encoding="utf-8-sig")
    cal = list(idx["date"])
    ci = {d: i for i, d in enumerate(cal)}
    T = sorted(m)
    ti = {t: j for j, t in enumerate(T)}
    nD, nT = len(cal), len(T)
    O = np.full((nD, nT), np.nan)
    Hh, C, A = O.copy(), O.copy(), O.copy()
    for t, bars in m.items():
        j = ti[t]
        for b in bars:
            i = ci.get(b["d"])
            if i is not None and b["o"] > 0 and b["c"] > 0:
                O[i, j], Hh[i, j], C[i, j], A[i, j] = b["o"], b["h"], b["c"], b["c"] * b["v"]
    have = ~np.isnan(C)
    nbars = np.cumsum(have, axis=0)
    dv20 = pd.DataFrame(A).rolling(20, min_periods=20).mean().to_numpy() / 1e8
    elig = have & (nbars >= 25) & (C >= 1000) & (A / 1e8 >= 20) & ~np.isnan(dv20)
    COST = 0.25
    HS = (5, 20, 60)

    def base_idx(dstr):
        k = bisect.bisect_right(cal, dstr) - 1
        return k if k >= 0 else None

    RET = {}
    for H in HS:
        R = np.full_like(C, np.nan)
        R[: nD - H] = (C[H:] / O[1: nD - H + 1] - 1) * 100 - COST
        RET[H] = R
    DEC = np.full((nD, nT), -1, dtype=int)
    TER = np.full((nD, nT), -1, dtype=int)
    for i in range(nD):
        js = np.where(elig[i])[0]
        if len(js):
            order = js[np.argsort(dv20[i, js], kind="stable")]
            DEC[i, order] = np.minimum(9, np.arange(len(order)) * 10 // len(order))
            TER[i, order] = np.minimum(2, np.arange(len(order)) * 3 // len(order))
    # 커버리지: 리포트일 d → 세션 [d, d+89일]
    COV = np.zeros((nD, nT), dtype=bool)
    for t, g in rep.groupby("ticker"):
        j = ti.get(t)
        if j is None:
            continue
        for d in g["date"].unique():
            k0 = bisect.bisect_left(cal, d)
            end = (datetime.fromisoformat(d) + timedelta(days=89)).strftime("%Y-%m-%d")
            k1 = bisect.bisect_right(cal, end)
            COV[k0:k1, j] = True
    ALL = np.ones_like(COV)

    def bench(val, mask, minn):
        B = np.full((nD, 10), np.nan)
        for i in range(nD):
            for dcl in range(10):
                sel = (DEC[i] == dcl) & mask[i]
                v = val[i, sel]
                v = v[~np.isnan(v)]
                if len(v) >= minn:
                    B[i, dcl] = v.mean()
        return B

    SZ = {H: bench(RET[H], ALL, 20) for H in HS}
    CB = {H: bench(RET[H], COV, 10) for H in HS}
    # 정본 계약(TP/SL/보유) — D0 종가만 쓰므로 저가는 필요 없다(함수 규약)
    HC = HOLD_SESSIONS
    CON = np.full((nD, nT), np.nan)
    for i in range(nD - HC):
        for j in np.where(elig[i])[0]:
            e = O[i + 1, j]
            if np.isnan(e) or np.isnan(C[i + 1: i + 1 + HC, j]).any():
                continue
            win = [(cal[k], O[k, j], Hh[k, j], 0.0, C[k, j], 0.0) for k in range(i + 1, i + 1 + HC)]
            r_ = contract_exit_v2(e, win, fee=FEE_KR, be_lock=False, tp=TP, sl=SL, hold=HC)
            if r_ is not None:
                CON[i, j] = r_[0]
    SZC = bench(CON, ALL, 20)
    CBC = bench(CON, COV, 10)
    kq = pd.read_csv(YF_IDX / "kr_229200.csv", encoding="utf-8-sig").set_index("date")
    KO, KC = idx["open"].to_numpy(), idx["close"].to_numpy()
    QO, QC = kq.reindex(cal)["open"].to_numpy(), kq.reindex(cal)["close"].to_numpy()

    def idx_ret(Oa, Ca, i, H):
        if i + H >= nD or np.isnan(Oa[i + 1]) or np.isnan(Ca[i + H]):
            return np.nan
        return (Ca[i + H] / Oa[i + 1] - 1) * 100

    with contextlib.closing(sqlite3.connect("file:E:/code/instiwatch/data/instiwatch.db?mode=ro", uri=True,
                                            timeout=10)) as c:
        mkt = {r[0]: r[1] for r in c.execute(
            "SELECT stock_code, corp_cls FROM disclosures WHERE stock_code!='' GROUP BY stock_code")}

    # 신호 (v1과 동일 규약)
    rep = rep.sort_values(["ticker", "broker", "date"])
    rev_rows, first_rows = [], []
    for (t, b), g in rep.groupby(["ticker", "broker"], sort=False):
        prev_d, prev_g, first = None, None, True
        for d, tg, op in zip(g["date"], g["target"], g["opinion"].fillna("")):
            if first:
                first_rows.append((t, d, b, op))
                first = False
            if tg and tg > 0:
                if prev_g and (datetime.fromisoformat(d) - datetime.fromisoformat(prev_d)).days <= 365:
                    rv_ = tg / prev_g - 1
                    if abs(rv_) <= 0.60:
                        rev_rows.append((t, d, rv_))
                prev_d, prev_g = d, tg
    rv = pd.DataFrame(rev_rows, columns=["ticker", "date", "rev"])
    daily = rv.groupby(["ticker", "date"]).agg(up=("rev", lambda s: int((s > 0).sum())),
                                               down=("rev", lambda s: int((s < 0).sum())),
                                               mean_rev=("rev", "mean")).reset_index()
    sig = {"R1": [], "R2": [], "R3": []}
    for t, g in daily.groupby("ticker"):
        net_by_i = defaultdict(int)
        for d, u, dn in zip(g["date"], g["up"], g["down"]):
            k = base_idx(d)
            if k is not None:
                net_by_i[k] += u - dn
        ks = sorted(net_by_i)
        prev_true = False
        for k in range(ks[0], ks[-1] + 1) if ks else []:
            cond = sum(net_by_i.get(x, 0) for x in range(k - 4, k + 1)) >= 2
            if cond and not prev_true and k in net_by_i:
                sig["R1"].append((t, cal[k]))
            prev_true = cond
    for t, d, mr in zip(daily["ticker"], daily["date"], daily["mean_rev"]):
        if mr >= 0.10:
            sig["R2"].append((t, d))
    for t, d, b, op in first_rows:
        if d >= "2019-04-02" and any(k in op for k in BUY_KEYS):
            sig["R3"].append((t, d))

    month_sessions = defaultdict(list)
    for i, d in enumerate(cal):
        month_sessions[d[:7]].append(i)

    # ── v3: R3 신호 − 사후 플라시보 ──
    all_sig_i = defaultdict(set)
    for rl in sig:
        for t, d in sig[rl]:
            k = base_idx(d)
            if k is not None:
                all_sig_i[t].add(k)
    init_n = defaultdict(int)
    init_keys = defaultdict(list)
    for t, d, b, op in first_rows:
        if d >= "2019-04-02" and any(k in op for k in BUY_KEYS):
            init_n[(t, d)] += 1
            init_keys[t].append((b, d))
    raw_up = {}
    for t, lst in init_keys.items():
        want = {(b, d.replace("-", "")) for b, d in lst}
        p = RAW / f"{t}.jsonl"
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            r = json.loads(ln)
            key = ((r.get("mbcr_name") or "").strip(), r.get("stck_bsop_date", ""))
            if key in want and key not in raw_up:
                try:
                    g, pc = float(r.get("hts_goal_prc") or 0), float(r.get("stck_prdy_clpr") or 0)
                except ValueError:
                    g, pc = 0.0, 0.0
                raw_up[key] = g / pc - 1 if g > 0 and pc > 0 else np.nan
    upside = {}
    for t, lst in init_keys.items():
        for b, d in lst:
            u = raw_up.get((b, d.replace("-", "")), np.nan)
            if not np.isnan(u):
                upside[(t, d)] = max(upside.get((t, d), -9.0), u)

    def v3_trades(H, val, sz, cb):
        out, last_i = [], {}
        for t, d in sorted(set(sig["R3"]), key=lambda x: (x[1], x[0])):
            j, i = ti.get(t), base_idx(d)
            if j is None or i is None or i + H >= nD or (t in last_i and i - last_i[t] < H):
                continue
            if not elig[i, j] or np.isnan(val[i, j]):
                continue
            dcl = DEC[i, j]
            if np.isnan(sz[i, dcl]) or np.isnan(cb[i, dcl]):
                continue
            last_i[t] = i

            def ok(k):
                return (k not in all_sig_i[t] and k + H < nD and elig[k, j] and not np.isnan(val[k, j])
                        and DEC[k, j] >= 0 and not np.isnan(cb[k, DEC[k, j]]))
            ca = [k for k in month_sessions[cal[i][:7]] if k > i and ok(k)]
            cbb = [k for k in range(i + 1, min(i + 21, nD)) if ok(k)]
            sx = val[i, j] - cb[i, dcl]
            pa = float(np.mean([val[k, j] - cb[k, DEC[k, j]] for k in ca])) if ca else np.nan
            pb = float(np.mean([val[k, j] - cb[k, DEC[k, j]] for k in cbb])) if cbb else np.nan
            out.append({"d": cal[i], "sig": sx, "pa": pa, "pb": pb, "da": sx - pa, "db": sx - pb,
                        "nb": init_n.get((t, d), 1), "up": upside.get((t, d), np.nan),
                        "mkt": {"Y": "KOSPI", "K": "KOSDAQ"}.get(mkt.get(t), "기타")})
        return out

    def st3(tr, col, lo, hi, H, filt=None):
        by = defaultdict(list)
        for x in tr:
            if lo <= x["d"] <= hi and (filt is None or filt(x)) and not np.isnan(x[col]):
                by[x["d"]].append(x[col])
        xs = [float(np.mean(by[d])) for d in sorted(by)]
        if len(xs) < 5:
            return None
        sd = float(np.std(xs, ddof=1))
        return {"n": sum(len(v) for v in by.values()), "nd": len(xs), "m": float(np.mean(xs)),
                "t": float(np.mean(xs)) / (sd / math.sqrt(len(xs))) if sd > 0 else None, "nw": nw_t(xs, H - 1)}

    def f3(s):
        if not s:
            return "-"
        t_ = f"{s['t']:+.2f}" if s["t"] is not None else "-"
        nw_ = f"{s['nw']:+.2f}" if s["nw"] is not None else "-"
        return f"{s['m']:+.2f}% (t {t_}, NW {nw_}, n {s['n']}/{s['nd']}일)"

    IS, OOS = ("2018-04-01", "2021-12-31"), ("2022-01-01", "2026-12-31")
    L = ["", "## v3 — R3 신호 − 같은 종목 사후 플라시보 (사전등록 v3, docstring)", "",
         f"평가 {eval_stamp()}. 값 = 신호일 클러스터 평균 (날짜 클러스터 t, NW t, 거래/신호일).", ""]
    verdict = None
    for lab, H, val, sz, cb in (("H20 보유", 20, RET[20], SZ[20], CB[20]),
                                (f"정본 계약 TP{TP:g}/SL{SL:g}/{HC}세션", HC, CON, SZC, CBC)):
        tr = v3_trades(H, val, sz, cb)
        trA = [x for x in tr if not np.isnan(x["pa"])]
        trB = [x for x in tr if not np.isnan(x["pb"])]
        L += [f"### {lab}", "", "| 구간 | 신호 초과(A 표본) | 플라시보 A | **차이 A** | 신호 초과(B 표본) | 플라시보 B | 차이 B |",
              "|---|---|---|---|---|---|---|"]
        res = {}
        for name, (lo, hi) in (("IS", IS), ("OOS", OOS)):
            res[name] = st3(trA, "da", lo, hi, H)
            L.append(f"| {name} | {f3(st3(trA, 'sig', lo, hi, H))} | {f3(st3(trA, 'pa', lo, hi, H))} | "
                     f"**{f3(res[name])}** | {f3(st3(trB, 'sig', lo, hi, H))} | {f3(st3(trB, 'pb', lo, hi, H))} | "
                     f"{f3(st3(trB, 'db', lo, hi, H))} |")
        L.append(f"\n플라시보 A 후보 없음으로 제외된 거래 {len(tr) - len(trA)}건 / 전체 {len(tr)}건.")
        if H == 20:
            si, so = res["IS"], res["OOS"]
            verdict = bool(si and so and so["m"] > 0 and (so["t"] or 0) >= 2.0 and si["m"] > 0)
            L.append(f"\n**합격 판정(H20, 차이 A): {'통과' if verdict else '미통과'}**")
            ups = sorted(x["up"] for x in trA if IS[0] <= x["d"] <= IS[1] and not np.isnan(x["up"]))
            c1, c2 = (ups[len(ups) // 3], ups[2 * len(ups) // 3]) if len(ups) >= 3 else (np.nan, np.nan)
            L += ["", f"층별 차이 A (H20, 보고용). 괴리 삼분위 경계(IS): {c1:+.1%} / {c2:+.1%}", "",
                  "| 층 | IS | OOS |", "|---|---|---|"]
            strata = (("신규 커버 증권사 1곳", lambda x: x["nb"] == 1), ("신규 커버 증권사 ≥2곳", lambda x: x["nb"] >= 2),
                      ("괴리 하위1/3", lambda x: not np.isnan(x["up"]) and x["up"] < c1),
                      ("괴리 중위1/3", lambda x: not np.isnan(x["up"]) and c1 <= x["up"] < c2),
                      ("괴리 상위1/3", lambda x: not np.isnan(x["up"]) and x["up"] >= c2),
                      ("괴리 결측", lambda x: np.isnan(x["up"])),
                      ("KOSPI", lambda x: x["mkt"] == "KOSPI"), ("KOSDAQ", lambda x: x["mkt"] == "KOSDAQ"))
            for sl_, f in strata:
                L.append(f"| {sl_} | {f3(st3(trA, 'da', *IS, H, f))} | {f3(st3(trA, 'da', *OOS, H, f))} |")
        L.append("")
    (OUT / "eval3_result.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(json.dumps({"R3_v3_pass": verdict}))
    return 0


BROKER_MAP = OUT / "broker_map.json"


def load_broker_map() -> dict:
    return json.loads(BROKER_MAP.read_text(encoding="utf-8"))["map"]


def cmd_eval4(_a) -> int:
    """사전등록 v4 — 증권사 이름 정규화 후 R3 v2(커버리지)·v3(사후 플라시보) 재계산 + 인공물 집계."""
    import bisect
    import contextlib
    import sqlite3

    import numpy as np
    import pandas as pd

    bmap = load_broker_map()
    rep0 = pd.read_csv(REPORTS, dtype={"ticker": str})
    m = json.loads(YF_CACHE.read_text(encoding="utf-8"))
    idx = pd.read_csv(YF_IDX / "kr_069500.csv", encoding="utf-8-sig")
    cal = list(idx["date"])
    ci = {d: i for i, d in enumerate(cal)}
    T = sorted(m)
    ti = {t: j for j, t in enumerate(T)}
    nD, nT = len(cal), len(T)
    O = np.full((nD, nT), np.nan)
    C, A = O.copy(), O.copy()
    for t, bars in m.items():
        j = ti[t]
        for b in bars:
            i = ci.get(b["d"])
            if i is not None and b["o"] > 0 and b["c"] > 0:
                O[i, j], C[i, j], A[i, j] = b["o"], b["c"], b["c"] * b["v"]
    have = ~np.isnan(C)
    nbars = np.cumsum(have, axis=0)
    dv20 = pd.DataFrame(A).rolling(20, min_periods=20).mean().to_numpy() / 1e8
    elig = have & (nbars >= 25) & (C >= 1000) & (A / 1e8 >= 20) & ~np.isnan(dv20)
    COST = 0.25
    H = 20

    def base_idx(dstr):
        k = bisect.bisect_right(cal, dstr) - 1
        return k if k >= 0 else None

    RET = np.full_like(C, np.nan)
    RET[: nD - H] = (C[H:] / O[1: nD - H + 1] - 1) * 100 - COST
    DEC = np.full((nD, nT), -1, dtype=int)
    for i in range(nD):
        js = np.where(elig[i])[0]
        if len(js):
            order = js[np.argsort(dv20[i, js], kind="stable")]
            DEC[i, order] = np.minimum(9, np.arange(len(order)) * 10 // len(order))
    COV = np.zeros((nD, nT), dtype=bool)
    for t, g in rep0.groupby("ticker"):
        j = ti.get(t)
        if j is None:
            continue
        for d in g["date"].unique():
            k0 = bisect.bisect_left(cal, d)
            end = (datetime.fromisoformat(d) + timedelta(days=89)).strftime("%Y-%m-%d")
            k1 = bisect.bisect_right(cal, end)
            COV[k0:k1, j] = True
    ALL = np.ones_like(COV)

    def bench(val, mask, minn):
        B = np.full((nD, 10), np.nan)
        for i in range(nD):
            for dcl in range(10):
                sel = (DEC[i] == dcl) & mask[i]
                v = val[i, sel]
                v = v[~np.isnan(v)]
                if len(v) >= minn:
                    B[i, dcl] = v.mean()
        return B

    SZ, CB = bench(RET, ALL, 20), bench(RET, COV, 10)
    month_sessions = defaultdict(list)
    for i, d in enumerate(cal):
        month_sessions[d[:7]].append(i)

    def run(canon: bool):
        rep = rep0.copy()
        if canon:
            rep["broker"] = rep["broker"].map(lambda b: bmap.get(b, b))
            rep = rep.drop_duplicates(["ticker", "broker", "date"], keep="first")
        rep = rep.sort_values(["ticker", "broker", "date"])
        rev_rows, frs = [], []
        for (t, b), g in rep.groupby(["ticker", "broker"], sort=False):
            prev_d, prev_g, first = None, None, True
            for d, tg, op in zip(g["date"], g["target"], g["opinion"].fillna("")):
                if first:
                    frs.append((t, d, b, op))
                    first = False
                if tg and tg > 0:
                    if prev_g and (datetime.fromisoformat(d) - datetime.fromisoformat(prev_d)).days <= 365:
                        rv_ = tg / prev_g - 1
                        if abs(rv_) <= 0.60:
                            rev_rows.append((t, d, rv_))
                    prev_d, prev_g = d, tg
        rv = pd.DataFrame(rev_rows, columns=["ticker", "date", "rev"])
        daily = rv.groupby(["ticker", "date"]).agg(up=("rev", lambda s: int((s > 0).sum())),
                                                   down=("rev", lambda s: int((s < 0).sum())),
                                                   mean_rev=("rev", "mean")).reset_index()
        sig = {"R1": [], "R2": [], "R3": []}
        for t, g in daily.groupby("ticker"):
            net_by_i = defaultdict(int)
            for d, u, dn in zip(g["date"], g["up"], g["down"]):
                k = base_idx(d)
                if k is not None:
                    net_by_i[k] += u - dn
            ks = sorted(net_by_i)
            prev_true = False
            for k in range(ks[0], ks[-1] + 1) if ks else []:
                cond = sum(net_by_i.get(x, 0) for x in range(k - 4, k + 1)) >= 2
                if cond and not prev_true and k in net_by_i:
                    sig["R1"].append((t, cal[k]))
                prev_true = cond
        for t, d, mr in zip(daily["ticker"], daily["date"], daily["mean_rev"]):
            if mr >= 0.10:
                sig["R2"].append((t, d))
        r3_tdb = []
        for t, d, b, op in frs:
            if d >= "2019-04-02" and any(k in op for k in BUY_KEYS):
                sig["R3"].append((t, d))
                r3_tdb.append((t, d, b))
        all_sig_i = defaultdict(set)
        for rl in sig:
            for t, d in sig[rl]:
                k = base_idx(d)
                if k is not None:
                    all_sig_i[t].add(k)
        out, last_i = [], {}
        for t, d in sorted(set(sig["R3"]), key=lambda x: (x[1], x[0])):
            j, i = ti.get(t), base_idx(d)
            if j is None or i is None or i + H >= nD or (t in last_i and i - last_i[t] < H):
                continue
            if not elig[i, j] or np.isnan(RET[i, j]):
                continue
            dcl = DEC[i, j]
            if np.isnan(SZ[i, dcl]) or np.isnan(CB[i, dcl]):
                continue
            last_i[t] = i

            def ok(k):
                return (k not in all_sig_i[t] and k + H < nD and elig[k, j] and not np.isnan(RET[k, j])
                        and DEC[k, j] >= 0 and not np.isnan(CB[k, DEC[k, j]]))
            ca = [k for k in month_sessions[cal[i][:7]] if k > i and ok(k)]
            cbb = [k for k in range(i + 1, min(i + 21, nD)) if ok(k)]
            net = RET[i, j]
            sx = net - CB[i, dcl]
            pa = float(np.mean([RET[k, j] - CB[k, DEC[k, j]] for k in ca])) if ca else np.nan
            pb = float(np.mean([RET[k, j] - CB[k, DEC[k, j]] for k in cbb])) if cbb else np.nan
            out.append({"d": cal[i], "t": t, "sd": d, "net": net, "sz": net - SZ[i, dcl], "cov": sx,
                        "pa": pa, "pb": pb, "da": sx - pa, "db": sx - pb})
        return {"sig": sig, "r3_tdb": r3_tdb, "tr": out, "nrep": len(rep)}

    def st(tr, col, lo, hi, filt=None):
        by = defaultdict(list)
        for x in tr:
            if lo <= x["d"] <= hi and (filt is None or filt(x)) and not np.isnan(x[col]):
                by[x["d"]].append(x[col])
        xs = [float(np.mean(by[d])) for d in sorted(by)]
        if len(xs) < 5:
            return None
        sd = float(np.std(xs, ddof=1))
        return {"n": sum(len(v) for v in by.values()), "nd": len(xs), "m": float(np.mean(xs)),
                "t": float(np.mean(xs)) / (sd / math.sqrt(len(xs))) if sd > 0 else None, "nw": nw_t(xs, H - 1)}

    def fs(s):
        if not s:
            return "-"
        t_ = f"{s['t']:+.2f}" if s["t"] is not None else "-"
        nw_ = f"{s['nw']:+.2f}" if s["nw"] is not None else "-"
        return f"{s['m']:+.2f}% (t {t_}, NW {nw_}, n {s['n']}/{s['nd']}일)"

    IS, OOS = ("2018-04-01", "2021-12-31"), ("2022-01-01", "2026-12-31")
    R = {"원 문자열(재현)": run(False), "정규 이름(v4)": run(True)}
    L = ["", "## v4 — 증권사 이름 정규화 후 R3 재계산 (사전등록 v4, docstring)", "",
         f"평가 {eval_stamp()}. 맵 `broker_map.json` {len(bmap)}개. 값 = 신호일 평균 (날짜 클러스터 t, NW t lag 19, 거래/신호일).", "",
         "### 1. 신호 수", "", "| 실행 | 리포트 행 | R1 | R2 | R3 이벤트(종목·날짜) | R3 (종목·날짜·증권사) | H20 거래 |",
         "|---|---:|---:|---:|---:|---:|---:|"]
    for k, r in R.items():
        L.append(f"| {k} | {r['nrep']:,} | {len(set(r['sig']['R1'])):,} | {len(set(r['sig']['R2'])):,} | "
                 f"{len(set(r['sig']['R3'])):,} | {len(r['r3_tdb']):,} | {len(r['tr']):,} |")
    L += ["", "### 2. v2 주 판정 — R3 H20 vs 커버리지 벤치 (규모매칭 병기)", "",
          "| 실행 | 구간 | vs 커버리지 | vs 규모매칭 | 판정(v2선: OOS>0·NW t≥2.64·IS>0) |", "|---|---|---|---|---|"]
    out_json = {}
    for k, r in R.items():
        tr = r["tr"]
        si, so = st(tr, "cov", *IS), st(tr, "cov", *OOS)
        ok2 = bool(si and so and so["m"] > 0 and (so["nw"] or 0) >= 2.64 and si["m"] > 0)
        for name, (lo, hi) in (("IS", IS), ("OOS", OOS)):
            L.append(f"| {k} | {name} | {fs(st(tr, 'cov', lo, hi))} | {fs(st(tr, 'sz', lo, hi))} | "
                     f"{('**통과**' if ok2 else '**미통과**') if name == 'OOS' else ''} |")
        out_json[k] = {"v2_pass": ok2}
    L += ["", "### 3. v3 — 신호 − 같은 달 사후 플라시보 (H20)", "",
          "| 실행 | 구간 | 신호 초과(A 표본) | 플라시보 A | **차이 A** | 차이 B | 판정 |", "|---|---|---|---|---|---|---|"]
    for k, r in R.items():
        trA = [x for x in r["tr"] if not np.isnan(x["pa"])]
        trB = [x for x in r["tr"] if not np.isnan(x["pb"])]
        si, so = st(trA, "da", *IS), st(trA, "da", *OOS)
        ok3 = bool(si and so and so["m"] > 0 and (so["t"] or 0) >= 2.0 and si["m"] > 0)
        out_json[k]["v3_pass"] = ok3
        out_json[k]["v3_oos"] = so
        out_json[k]["v3_is"] = si
        for name, (lo, hi) in (("IS", IS), ("OOS", OOS)):
            L.append(f"| {k} | {name} | {fs(st(trA, 'cov', lo, hi))} | {fs(st(trA, 'pa', lo, hi))} | "
                     f"**{fs(st(trA, 'da', lo, hi))}** | {fs(st(trB, 'db', lo, hi))} | "
                     f"{('**통과**' if ok3 else '**미통과**') if name == 'OOS' else ''} |")
        L.append(f"| {k} | 제외 | 플라시보 A 후보 없음 {len(r['tr']) - len(trA)}건 / {len(r['tr'])}건 | | | | |")
    # 인공물
    raw, can = R["원 문자열(재현)"], R["정규 이름(v4)"]
    raw_ev, can_ev = set(raw["sig"]["R3"]), set(can["sig"]["R3"])
    art_ev = raw_ev - can_ev
    can_tdb = set(can["r3_tdb"])
    art_tdb = [x for x in raw["r3_tdb"] if (x[0], x[1], bmap.get(x[2], x[2])) not in can_tdb]
    by_b = defaultdict(int)
    for _, _, b in art_tdb:
        by_b[b] += 1
    trr = raw["tr"]
    art_tr = [x for x in trr if (x["t"], x["sd"]) in art_ev]
    L += ["", "### 4. 인공물(사명 변경으로 '신규 커버'가 된 R3)", "",
          f"- 이벤트(종목·날짜): 원 {len(raw_ev):,} 중 {len(art_ev):,}건({len(art_ev) / len(raw_ev):.1%})이 정규 이름에서 사라짐"
          f"(정규 ⊆ 원: {can_ev <= raw_ev}).",
          f"- (종목·날짜·증권사) 단위: {len(art_tdb):,}건. 원 이름별: "
          + ", ".join(f"{b} {n}" for b, n in sorted(by_b.items(), key=lambda kv: -kv[1])) + ".",
          f"- 원 H20 거래 중 인공물: 전체 {len(art_tr)}/{len(trr)}({len(art_tr) / max(len(trr), 1):.1%}), "
          f"IS {sum(1 for x in art_tr if x['d'] <= IS[1])}/{sum(1 for x in trr if x['d'] <= IS[1])}, "
          f"OOS {sum(1 for x in art_tr if x['d'] >= OOS[0])}/{sum(1 for x in trr if x['d'] >= OOS[0])}.", "",
          "| 표본 | 구간 | 커버리지 초과 | 차이 A | 원 net |", "|---|---|---|---|---|"]
    artA = [x for x in art_tr if not np.isnan(x["pa"])]
    keep = [x for x in trr if (x["t"], x["sd"]) not in art_ev]
    keepA = [x for x in keep if not np.isnan(x["pa"])]
    for lab, a_, aa in (("인공물 거래", art_tr, artA), ("원 표본에서 인공물 제외", keep, keepA)):
        for name, (lo, hi) in (("IS", IS), ("OOS", OOS), ("전체", ("0000", "9999"))):
            L.append(f"| {lab} | {name} | {fs(st(a_, 'cov', lo, hi))} | {fs(st(aa, 'da', lo, hi))} | {fs(st(a_, 'net', lo, hi))} |")
    L.append("")
    (OUT / "eval4_result.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(json.dumps({k: {kk: vv for kk, vv in v.items() if kk.endswith("pass")} for k, v in out_json.items()},
                     ensure_ascii=False))
    return 0


def eval_stamp() -> str:
    return now_kst().isoformat(timespec="minutes")


def atomic_md(lines: list[str]) -> None:
    (OUT / "eval_result.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("universe")
    b = sub.add_parser("backfill")
    b.add_argument("--max-calls", type=int, default=0)
    b.add_argument("--dry-run", action="store_true")
    sub.add_parser("build")
    sub.add_parser("eval")
    sub.add_parser("eval2")
    sub.add_parser("eval3")
    sub.add_parser("eval4")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    sys.exit({"universe": cmd_universe, "backfill": cmd_backfill, "build": cmd_build, "eval": cmd_eval, "eval2": cmd_eval2, "eval3": cmd_eval3, "eval4": cmd_eval4}[a.cmd](a))
