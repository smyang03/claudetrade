"""KR 급락 반등 후보 shadow 스캔 (관측 전용 — 주문 없음).

2026-08-01 설계(docs/reports/design_candidate_selection_and_exit_20260801.md) K2 준비물.
8조건 (2026-08-01 실측, 검증기간 n=141 +3.35/건 — 단 피처 선정에 검증기간 참조된
경미한 누출이 있어 이 shadow forward 가 최종 판정이다):
  낙폭 5.27%+ / 갭하락 0.88%p+ / 종가위치 0.14+ / vol_spike 0.64+ /
  mom20 <= +4.72 / 고점20 대비 -21.22% 이하 / rv20 <= 6.24 / 종가 7,110원+
유동성: 20일 평균 거래대금 10억+, 하한가 잠김(-29.7% 초과) 제외.

사용:
  python tools/kr_fallen_shadow_scan.py --update-cache   # 가격 캐시 갱신(장 마감 후, ~10분)
  python tools/kr_fallen_shadow_scan.py --date 20260803  # 캐시 기반 당일 스캔(즉시)
  python tools/kr_fallen_shadow_scan.py --settle         # 만기(D5) 결과 채움(캐시 기반)

2026-08-01 재설계: 스캔 루프 안의 pykrx 호출이 타임아웃 없이 행 걸리는 것을 실측
(첫 실행 5시간 미완, 전종목 스냅샷 API는 이 환경에서 빈 값). 그래서 스캔·정산은
캐시(data/analysis/kr_fallen_price_cache.json)만 읽고, 네트워크는 --update-cache
한 단계로 격리한다(검증된 수집 패턴: 개별 실패 스킵, 25건마다 저장, flush 출력).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "shadow" / "kr_fallen_shadow.jsonl"
CACHE = ROOT / "data" / "analysis" / "kr_fallen_price_cache.json"
_CORP_CODES_PATH = ROOT / "data" / "dart_corp_codes.json"
_corp_codes_cache: dict | None = None


_disclosure_cache: dict | None = None
_earnings_cache: dict | None = None


def _info_event_flags(code: str, session_date: str) -> dict:
    """정보성 이벤트 태그 (2026-08-06, A6 — 관측 전용, 조건 아님).

    thesis "정보성 하락은 안 산다"가 KR 레인 코드에 없다. 수집기 2종
    (kr_disclosure_observer·earnings kr_by_code)은 이미 매일 돌고 있으므로
    신호일 기준 당일/전일 이벤트 유무를 원장에 태그만 한다.
    차단 여부는 태그된 첫 후보가 나타났을 때 운영자가 결정한다.
    조회 실패 시 빈 값(위조 금지 — 없음과 미확인을 구분).
    """
    global _disclosure_cache, _earnings_cache
    if _disclosure_cache is None:
        try:
            _disclosure_cache = json.loads((ROOT / "data" / "kr_disclosure_observer.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _disclosure_cache = {}
    if _earnings_cache is None:
        try:
            _earnings_cache = json.loads((ROOT / "data" / "earnings_calendar.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _earnings_cache = {}
    if not _disclosure_cache and not _earnings_cache:
        return {}
    window = {session_date}
    try:
        prev = datetime.strptime(session_date, "%Y-%m-%d") - timedelta(days=3)  # 주말 포함 여유
        window |= {(prev + timedelta(days=n)).strftime("%Y-%m-%d") for n in range(0, 3)}
    except ValueError:
        pass
    disc_hits = [
        str(item.get("report_name") or "")[:30]
        for item in ((_disclosure_cache.get("by_code") or {}).get(code) or [])
        if str(item.get("date") or "") in window
    ]
    earn = (_earnings_cache.get("kr_by_code") or {}).get(code) or {}
    earn_hit = str(earn.get("date") or "") in window
    return {
        "disclosure_recent": bool(disc_hits),
        "disclosure_names": disc_hits[:3],
        "earnings_recent": earn_hit,
    }


def _instrument_type(code: str) -> str:
    """일반주/우선주/ETF계열 판별. corp_code 조회 실패 시 빈 값(위조 금지)."""
    global _corp_codes_cache
    if _corp_codes_cache is None:
        try:
            _corp_codes_cache = json.loads(_CORP_CODES_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _corp_codes_cache = {}
    if not _corp_codes_cache:
        return ""
    if code in _corp_codes_cache:
        return "일반주"
    return "ETF계열" if str(code).endswith("0") else "우선주"
UNIVERSE = ROOT / "data" / "analysis" / "kr_fallen_universe.json"
# 사각 관측 원장(2026-08-13 운영자 승인): R4 형태(gap<=-4 & disc<=-15)인데 당일
# 종가낙폭이 drop_ge(5.27%) 미만이라 본 원장에 안 잡히는 "장중 회복형"을 별도
# 파일로 기록만 한다. 브리지·게이트·정산 통계는 본 원장(OUT)만 읽으므로 구조적
# 격리다. 외부 백테스트(네이버, 2026-01~08): 사각 105건 +5.69%/건 — 내부 원장으로
# 재확인될 때까지 observe_only. 편입은 별도 승인.
BLIND_OUT = ROOT / "data" / "shadow" / "kr_fallen_blindspot_shadow.jsonl"
BLIND_GAP_LE = -4.0
BLIND_DISC_LE = -15.0
COST = 0.25

CONDS_DOC = {
    "drop_ge": 5.27, "gap_ge": 0.88, "close_pos_ge": 0.14, "vol_spike_ge": 0.64,
    "mom20_le": 4.72, "from_high20_le": -21.22, "rv20_le": 6.24, "price_ge": 7110.0,
}


def scan(date_str: str) -> int:
    day = datetime.strptime(date_str, "%Y%m%d")
    d_iso = day.strftime("%Y-%m-%d")
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    rows = []
    blind_rows = []
    n_hasday = 0
    for code, bars in cache.items():
        if not bars or len(bars) < 25:
            continue
        idx = None
        for k in range(len(bars) - 1, max(-1, len(bars) - 4), -1):
            if bars[k]["d"] == d_iso:
                idx = k
                break
        if idx is None or idx < 22:
            continue
        n_hasday += 1
        b = bars[idx]
        prev = bars[idx - 1]["c"]
        if prev <= 0:
            continue
        chg = 100 * (b["c"] / prev - 1)
        gap = 100 * (b["o"] / prev - 1) if b["o"] > 0 else 0.0
        drop_capture = -29.7 <= chg <= -CONDS_DOC["drop_ge"]
        # 사각 후보: 갭은 R4 대역인데 종가낙폭이 drop_ge 미만(장중 회복형).
        # disc 조건은 ma20 계산 후 최종 판정한다. 하한가 잠김·거래정지(o<=0)는 제외.
        blind_candidate = (not drop_capture) and chg > -29.7 and b["o"] > 0 and gap <= BLIND_GAP_LE
        if (not drop_capture and not blind_candidate) or b["c"] < CONDS_DOC["price_ge"]:
            continue
        w20 = bars[idx - 20:idx]
        amt20 = sum(x["amt"] for x in w20) / 20
        if amt20 < 1e9:
            continue
        rng = b["h"] - b["l"]
        v20 = sum(x["v"] for x in w20) / 20
        ma20 = sum(x["c"] for x in w20) / 20
        hi20 = max(x["h"] for x in w20)
        rets = [100 * (w20[m]["c"] / w20[m - 1]["c"] - 1) for m in range(1, 20)
                if w20[m - 1]["c"] > 0]
        mom20 = 100 * (b["c"] / bars[idx - 21]["c"] - 1) if bars[idx - 21]["c"] > 0 else 0.0
        feats = {
            "chg": chg,
            "gap": gap,
            "close_pos": (b["c"] - b["l"]) / rng if rng > 0 else 0.5,
            "vol_spike": b["v"] / v20 if v20 > 0 else 1.0,
            "mom20": mom20,
            "from_high20": 100 * (b["c"] / hi20 - 1) if hi20 > 0 else 0.0,
            "rv20": st.pstdev(rets) if len(rets) > 3 else 99.0,
            "price": b["c"],
            # 2026-08-03 토론 판정: "MA20 대비 깊은 할인(−25%)+저변동" 규칙이 in-sample
            # +4.32/건·월별 전부 양수로 유망 — 8조건과 shadow 병렬 판정용 관측 피처.
            # 조건이 아니라 기록만 한다(음수=할인). 정본: debate_fallen_hold_and_discount_20260803.md
            "ma20_disc": 100 * (b["c"] / ma20 - 1) if ma20 > 0 else 0.0,
        }
        flags = {
            "drop": -chg >= CONDS_DOC["drop_ge"],
            "gap_down": -gap >= CONDS_DOC["gap_ge"],
            "close_pos": feats["close_pos"] >= CONDS_DOC["close_pos_ge"],
            "vol_spike": feats["vol_spike"] >= CONDS_DOC["vol_spike_ge"],
            "mom20": feats["mom20"] <= CONDS_DOC["mom20_le"],
            "from_high20": feats["from_high20"] <= CONDS_DOC["from_high20_le"],
            "rv20": feats["rv20"] <= CONDS_DOC["rv20_le"],
            "price": feats["price"] >= CONDS_DOC["price_ge"],
        }
        row = {
            "scanned_at": datetime.now().isoformat(timespec="seconds"),
            "session_date": d_iso,
            "ticker": code,
            # 2026-08-05: 상품 유형 태그(게이트 판정 시 코호트 분리용 — 제외 아님).
            # ETF의 급락은 시장 하락이라 thesis(개별 왜곡)와 다르지만, R2 대역
            # 실측(2026 n=4, +11.57%, 승률 100%)이 제외를 지지하지 않아 태그만 남긴다.
            # 판별: DART corp_code 없음 = 비법인(ETF·리츠), 그중 끝자리 0 = ETF 계열.
            "instrument": _instrument_type(code),
            **_info_event_flags(code, d_iso),
            "pass_all": all(flags.values()),
            "pass_count": int(sum(flags.values())),
            "flags": flags,
            "feats": {k: round(v, 4) for k, v in feats.items()},
            "status": "PENDING",
            "entry_rule": "next_open",
            "exit_rule": "TP12_SL25_D5_cost0.25",
            "entry_price": None, "exit_price": None, "net_pct": None,
        }
        if drop_capture:
            rows.append(row)
        elif feats["ma20_disc"] <= BLIND_DISC_LE:
            # 사각 관측 행 — 본 원장과 다른 파일에만 기록(브리지·게이트 격리)
            row["observe_only"] = True
            row["capture_path"] = "blindspot_gap_disc"
            blind_rows.append(row)
    n_new, n_dup = _append_dedupe(OUT, rows)
    n_blind_new, n_blind_dup = _append_dedupe(BLIND_OUT, blind_rows)
    n_pass = sum(1 for r in rows if r["pass_all"])
    print("캐시 내 %s 보유 %d종목 / 낙폭후보 %d건 기록 (8조건 전부 통과 %d건, 중복 스킵 %d건) -> %s" % (
        d_iso, n_hasday, n_new, n_pass, n_dup, OUT))
    print("사각 관측(observe_only) %d건 기록 (중복 스킵 %d건) -> %s" % (
        n_blind_new, n_blind_dup, BLIND_OUT))
    return 0


SHORT_RATIO_OUT = ROOT / "data" / "shadow" / "kr_short_ratio.jsonl"


def record_short_ratio() -> int:
    """공매도 비중 관측 기록 (2026-08-18 운영자 승인 "심어" — 주문·규칙 무접촉).

    검증 실측(정산 58건): 비중 상위가 반등 우위 — 알파 통제 +2.06% vs 하위 −1.68%,
    동일 세션 3/4 우세, p≈0.025. US(FINRA)와 **부호 반대**(KR은 모델 없는 규칙 기반이라
    공선 문제 없음). 사전등록 반증: forward 정산 30건에서 상위−하위 알파 차 ≤0 → 폐기.

    시점 규약: KRX 공매도 통계는 T일 저녁 공표라 15:40 스캔 시점엔 당일치가 없을 수
    있다 → 매 실행마다 원장(본+사각)의 **최근 7세션 중 미기록분**을 소급 채운다.
    진입은 T+1 시가이므로 T일 데이터 사용은 lookahead가 아니다.
    KRX 자격증명은 .env에서 직접 읽는다(스케줄러 env는 .env.live 상속이라 없음).
    """
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            if line.startswith(("KRX_ID=", "KRX_PW=")):
                key, value = line.split("=", 1)
                os.environ.setdefault(key, value.strip())
        from pykrx import stock as _krx_stock

        want: dict[str, set] = {}
        for path in (OUT, BLIND_OUT):
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                d = str(row.get("session_date") or "")
                if d:
                    want.setdefault(d, set()).add(str(row.get("ticker") or ""))
        targets = sorted(want)[-7:]
        done: set = set()
        if SHORT_RATIO_OUT.exists():
            for line in SHORT_RATIO_OUT.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        r = json.loads(line)
                        done.add((str(r.get("session_date")), str(r.get("ticker"))))
                    except ValueError:
                        continue
        written = 0
        stamp = datetime.now().isoformat(timespec="seconds")
        with open(SHORT_RATIO_OUT, "a", encoding="utf-8") as handle:
            for d in targets:
                missing = [t for t in want[d] if (d, t) not in done]
                if not missing:
                    continue
                ratio_map: dict[str, float] = {}
                for mkt in ("KOSPI", "KOSDAQ"):
                    try:
                        df = _krx_stock.get_shorting_volume_by_ticker(d.replace("-", ""), market=mkt)
                        col = next((c for c in df.columns if "비중" in str(c)), None)
                        if col is None:
                            continue
                        for ticker, row in df.iterrows():
                            ratio_map[str(ticker)] = float(row[col])
                    except Exception:
                        continue
                if not ratio_map:
                    continue  # 미공표 세션 — 다음 실행에서 재시도
                for t in missing:
                    value = ratio_map.get(t)
                    handle.write(json.dumps({
                        "session_date": d, "ticker": t,
                        "short_ratio_pct": value,  # None = 그날 공매도 집계에 없음(=0 근사)
                        "captured_at": stamp,
                    }, ensure_ascii=False) + "\n")
                    written += 1
        if written:
            print(f"[short ratio] 관측 기록 {written}건 -> {SHORT_RATIO_OUT.name}")
        return written
    except Exception as exc:  # 관측 결측이 스캔을 막으면 안 된다
        print(f"[short ratio] 기록 실패(스캔 무영향): {exc}")
        return 0


def _append_dedupe(path: Path, rows: list) -> tuple[int, int]:
    """(session_date, ticker) 중복을 건너뛰며 append. (신규, 중복) 건수 반환.

    재실행 중복 방지: append 모드라 --date 재실행 시 PENDING 중복행이 쌓여
    정산·통계를 오염시키던 결함의 재발 방지(본 원장·사각 원장 공통).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: set[tuple[str, str]] = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                old = json.loads(line)
            except Exception:
                continue
            existing.add((str(old.get("session_date")), str(old.get("ticker"))))
    new_rows = [r for r in rows if (r["session_date"], r["ticker"]) not in existing]
    with open(path, "a", encoding="utf-8") as f:
        for r in new_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(new_rows), len(rows) - len(new_rows)


def update_cache() -> int:
    """가격 캐시 갱신 — 검증된 수집 패턴(개별 실패 스킵, 25건마다 저장, flush)."""
    import time

    from pykrx import stock
    universe = json.loads(UNIVERSE.read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    end = datetime.now().strftime("%Y%m%d")
    start = (datetime.now() - timedelta(days=100)).strftime("%Y%m%d")
    ok = 0
    for i, code in enumerate(universe):
        try:
            df = stock.get_market_ohlcv(start, end, code)
        except Exception:
            time.sleep(0.3)
            continue
        bars = []
        if df is not None and len(df):
            has_amt = "거래대금" in df.columns
            for idx, r in df.iterrows():
                try:
                    o, h, l, c, v = (float(r["시가"]), float(r["고가"]), float(r["저가"]),
                                     float(r["종가"]), float(r["거래량"]))
                except Exception:
                    continue
                if c <= 0:
                    continue
                amt = float(r["거래대금"]) if has_amt else c * v
                bars.append({"d": str(idx)[:10], "o": o, "h": h, "l": l, "c": c,
                             "v": v, "amt": amt})
        if bars:
            cached = cache.get(code, [])
            # 2026-08-12 수정주가 경계 가드: 액면분할·감자 후 재조회분은 소급 조정된
            # 가격이라 구캐시와 스케일이 다르다(083660 ×13 실측). 겹치는 날짜의 종가가
            # 5% 넘게 어긋나면 구캐시 전체를 버리고 이번 조회 기준으로 다시 쌓는다.
            # 거래정지 재개 폭락(032980류)은 실제 시세로 겹침이 일치해 여기 걸리지 않는다.
            fresh_by_date = {x["d"]: x for x in bars}
            for x in cached:
                f = fresh_by_date.get(x["d"])
                if f and x.get("c") and abs(f["c"] / x["c"] - 1.0) > 0.05:
                    print("  [기준가 경계] %s %s 캐시 %.1f vs 재조회 %.1f — 구캐시 폐기" % (
                        code, x["d"], x["c"], f["c"]), flush=True)
                    cached = []
                    break
            old = {x["d"]: x for x in cached}
            for x in bars:
                old[x["d"]] = x
            cache[code] = sorted(old.values(), key=lambda x: x["d"])
            ok += 1
        if (i + 1) % 25 == 0:
            CACHE.write_text(json.dumps(cache), encoding="utf-8")
            print("  %d/%d" % (i + 1, len(universe)), flush=True)
        time.sleep(0.15)
    CACHE.write_text(json.dumps(cache), encoding="utf-8")
    print("캐시 갱신 완료: %d/%d 종목" % (ok, len(universe)))
    return 0


def settle() -> int:
    rc = _settle_file(OUT, label="본 원장")
    _settle_file(BLIND_OUT, label="사각 관측")
    return rc


def _settle_file(path: Path, *, label: str) -> int:
    if not path.exists():
        print("%s 없음: %s" % (label, path))
        return 0
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    cache = json.loads(CACHE.read_text(encoding="utf-8"))
    changed = 0
    for r in lines:
        if r.get("status") != "PENDING":
            continue
        bars = cache.get(r["ticker"], [])
        after = [b for b in bars if b["d"] > r["session_date"]]
        if not after:
            continue
        e = after[0]["o"]
        if not e or e <= 0:
            r["status"] = "NO_ENTRY"
            changed += 1
            continue
        tp, sl = e * 1.12, e * 0.75
        win = after[:5]
        net = None
        kind = None
        for k, b in enumerate(win):
            if k > 0:
                if b["o"] <= sl:
                    net, kind = 100 * (b["o"] / e - 1) - COST, "gap_sl"
                    break
                if b["o"] >= tp:
                    net, kind = 100 * (b["o"] / e - 1) - COST, "gap_tp"
                    break
            if b["l"] <= sl:
                net, kind = 100 * (sl / e - 1) - COST, "sl"
                break
            if b["h"] >= tp:
                net, kind = 100 * (tp / e - 1) - COST, "tp"
                break
        if net is None:
            if len(win) < 5:
                continue  # 아직 만기 전
            net, kind = 100 * (win[-1]["c"] / e - 1) - COST, "time"
        r["entry_price"] = e
        r["net_pct"] = round(net, 4)
        r["exit_kind"] = kind
        r["status"] = "SETTLED"
        changed += 1
    if changed:
        with open(path, "w", encoding="utf-8") as f:
            for r in lines:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    settled = [r for r in lines if r.get("status") == "SETTLED" and r.get("pass_all")]
    if settled:
        v = [r["net_pct"] for r in settled]
        print("[%s] 8조건 통과 만기 %d건: 평균 %.3f%% 승률 %.1f%%" % (
            label, len(v), sum(v) / len(v), 100 * sum(1 for x in v if x > 0) / len(v)))
    print("[%s] 갱신 %d건" % (label, changed))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    ap.add_argument("--settle", action="store_true")
    ap.add_argument("--update-cache", action="store_true")
    ap.add_argument("--auto", action="store_true",
                    help="캐시 갱신 -> 당일 스캔 -> 만기 정산 일괄 실행 (스케줄러용)")
    args = ap.parse_args()
    date_str = str(args.date or "").replace("-", "")  # 스케줄러는 ISO(YYYY-MM-DD)로 넘긴다
    if args.auto:
        update_cache()
        scan(date_str)
        record_short_ratio()
        return settle()
    if args.update_cache:
        return update_cache()
    if args.settle:
        return settle()
    return scan(date_str)


if __name__ == "__main__":
    sys.exit(main())
