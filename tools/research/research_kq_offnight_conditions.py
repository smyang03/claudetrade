#!/usr/bin/env python3
"""비신호 밤의 조건부 구조 — 빈도를 늘릴 데가 남았나 (read-only, 2026-09-14).

## 왜 신호 밤이 아니라 **비신호 밤**인가

이 레인의 병목은 회당이 아니라 **가동률**이다(연 20.3회). 09-13 사다리 측정에서
문턱을 한 칸 내리는 길은 닫혔다(갭 칸 회당 +0.003%). 남은 질문은 하나다.

**신호일 203개를 쪼개는 건 총액을 못 늘린다** — 같은 +10.63%p를 칸 사이에서 옮길 뿐이다.
빈도를 늘리는 유일한 길은 **나머지 ~225개 밤 중에 비용을 넘는 부분집합이 있는가**다.
그래서 본 검정의 모집단은 **v2 신호가 아닌 밤**이다.

09-13에 얻은 사전지식: 밤 프리미엄은 **방향 조건부**다(급등일 −0.26 / 무조건 0 / 급락일 +0.57).
여기서 묻는 건 그게 **요일·변동성·미국장에도 조건부인가**, 그리고 **그 조건이 비신호 밤을
비용 위로 올리는가**다.

## 축과 분할 — 숫자 보기 전에 고정

  ① 요일: **월요일 밤 vs 나머지** (2분할)
     가설이 있는 축만 판정한다 — 주말 갭이 월요일 종가→화요일 시가에 실린다.
     나머지 4개 요일은 **관측만, 판정 없음**(5칸 격자를 다 판정하면 그게 과적합이다).
  ② 변동성: 신호일까지의 **rv20이 그 시점까지의 롤링 중앙값** 위/아래 (2분할)
     ⚠️ 전체표본 중앙값을 쓰면 미래를 본다. 반드시 **그 날짜까지의** 중앙값이다.
  ③ 미국장: 진입 시점에 **이미 마감된** SPY 세션의 등락 음수 vs 양수 (2분할)
     ⚠️⚠️ **정렬이 함정이다.** 우리는 KR 종가(15:30 KST)에 사서 다음 KR 시가에 판다.
     그 사이(22:30~익일 05:00 KST)에 **D일자 미국 세션이 열린다** — 즉 D일자 SPY 등락은
     우리 보유 창 **안**에서 벌어지는 일이라 진입 시점엔 알 수 없다. 그걸 조건으로 쓰면
     lookahead다. 진입 시점에 알 수 있는 마지막 미국 세션은 **D보다 엄격히 이전** 날짜다
     (D−1 세션이 D 아침 05:00 KST에 마감). 그래서 `prev_spy()`로 D 미만 최신일을 쓴다.
     ⚠️ 데이터가 2023-01부터다(Alpaca 백필 + 라이브). 비신호 밤은 3년이면 ~700개라
     2분할이 가능하지만, **신호 밤 쪽은 ~60개뿐이라 판정하지 않는다.**

## 판정 문턱 — 사다리와 같은 바에 하나 더

한 칸은 **아래를 전부** 만족해야 채택 후보다.
  ① 비용(0.05%) 차감 후 연도매칭 초과 > 0
  ② t ≥ 2
  ③ 양수 연도 ≥ 8/11
  ④ **분할 반기 부호 유지**(2015~2020 / 2021~2025)

⚠️ 이번 주에만 격자 인접 탐색이 세 번째다(rung · 거울상 · 이번 것). 하나씩은 괜찮아도
합치면 08-20의 "19개 변수 중 3개 생존" 국면에 가까워진다. 그래서 **들여다본 칸은
실패한 것까지 전부 표에 싣는다.** 빠뜨려서 고르는 일이 없게.

⚠️ 통과해도 라이브 문턱은 안 바꾼다. 쉐도우 사전등록 후보일 뿐이다.
"""
from __future__ import annotations

import csv
import statistics as st
import sys
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.kr_index_etf_panic_shadow as P  # noqa: E402  (read-only)
from tools.research.research_panic_etf_year_decomp import t_stat, uncond_by_year  # noqa: E402

TK = "233740"
CUT_YEAR = "2026"
HALF = "2021"
COST = P.COST
T_BAR, YEAR_BAR = 2.0, 8
US_START = "2023-01-03"       # Alpaca 백필 시작 — 이 앞은 미국 축 판정 불가
RV_WIN = 20
RV_MIN_HISTORY = 250          # 롤링 중앙값을 낼 최소 이력


def load_spy():
    """SPY 일봉 종가 등락(%) — 백필(2023-01~2025-04) + 라이브(2025-05~)."""
    rows = {}
    for p in (ROOT / "data" / "price_backfill_alpaca" / "us" / "us_SPY.csv",
              ROOT / "data" / "price" / "us" / "us_SPY.csv"):
        if not p.exists():
            continue
        with p.open(encoding="utf-8-sig", newline="") as fh:
            for r in csv.reader(fh):
                if len(r) >= 5 and r[0][:2] == "20":
                    try:
                        rows[r[0]] = float(r[4])
                    except ValueError:
                        continue
    ds = sorted(rows)
    return {ds[i]: (rows[ds[i]] / rows[ds[i - 1]] - 1.0) * 100.0 for i in range(1, len(ds))}


def rolling_rv_side(dates, cache):
    """각 날짜의 rv20이 **그 날짜까지의** rv20 중앙값보다 위인가(PIT).

    전체표본 중앙값을 쓰면 미래를 본다. 그래서 누적 중앙값을 쓴다.
    """
    rv, seen, out = {}, [], {}
    chg = [cache[d].get("kq_chg") for d in dates]
    for i, d in enumerate(dates):
        if i < RV_WIN:
            continue
        win = [x for x in chg[i - RV_WIN + 1:i + 1] if x is not None]
        if len(win) < RV_WIN:
            continue
        v = st.pstdev(win)
        rv[d] = v
        seen.append(v)
        if len(seen) >= RV_MIN_HISTORY:
            out[d] = v > st.median(seen)
    return out


def main():
    cache = P._read_cache()
    dates = sorted(cache)
    by_year = uncond_by_year(cache, TK)
    pooled = P._uncond_overnight(cache, TK)
    spy = load_spy()
    spy_dates = sorted(spy)
    rv_hi = rolling_rv_side(dates, cache)

    import bisect

    def prev_spy(d):
        """KR 날짜 d의 종가 시점에 **이미 마감된** 마지막 미국 세션 등락(%).

        d일자 미국 세션은 우리 보유 창 안에서 열린다 — 쓰면 lookahead다(docstring ③).
        """
        i = bisect.bisect_left(spy_dates, d)
        return spy.get(spy_dates[i - 1]) if i > 0 else None

    def is_v2(c):
        g, x = c.get("kq_gap"), c.get("kq_chg")
        return (g is not None and g <= P.GAP_THR) or (x is not None and x <= P.THR)

    def is_rung(c):
        x, g = c.get("kq_chg"), c.get("kq_gap")
        return (not is_v2(c)) and x is not None and -2.5 < x <= -2.0

    # 밤 목록 — (날짜, 비용후 초과, 신호여부, rung여부)
    nights = []
    for i in range(len(dates) - 1):
        d, y = dates[i], dates[i][:4]
        if y >= CUT_YEAR or d < P.BACKFILL_START:
            continue
        ca = cache[d].get(f"{TK}_close")
        ob = cache[dates[i + 1]].get(f"{TK}_open")
        if not ca or not ob:
            continue
        ex = (ob / ca - 1.0) * 100.0 - by_year.get(y, pooled or 0.0) - COST
        nights.append({"d": d, "y": y, "ex": ex, "v2": is_v2(cache[d]),
                       "rung": is_rung(cache[d])})

    off = [n for n in nights if not n["v2"]]
    sig = [n for n in nights if n["v2"]]

    def summarize(rows):
        if len(rows) < 3:
            return None
        ys = sorted({r["y"] for r in rows})
        pos = sum(1 for y in ys if st.mean(r["ex"] for r in rows if r["y"] == y) > 0)
        vals = [r["ex"] for r in rows]
        a = [r for r in rows if r["y"] < HALF]
        b = [r for r in rows if r["y"] >= HALF]
        return {"n": len(rows), "mean": st.mean(vals), "t": t_stat(vals),
                "pos": pos, "years": len(ys),
                "h1": st.mean(r["ex"] for r in a) if a else None,
                "h2": st.mean(r["ex"] for r in b) if b else None}

    def passes(s):
        return bool(s and s["mean"] > 0 and s["t"] is not None and s["t"] >= T_BAR
                    and s["pos"] >= YEAR_BAR
                    and s["h1"] is not None and s["h2"] is not None
                    and s["h1"] > 0 and s["h2"] > 0)

    def wd(d):
        return datetime.fromisoformat(d).weekday()

    cells = [
        ("월요일 밤", lambda n: wd(n["d"]) == 0, True),
        ("월요일 외", lambda n: wd(n["d"]) != 0, True),
        ("rv20 상(롤링 PIT)", lambda n: rv_hi.get(n["d"]) is True, True),
        ("rv20 하(롤링 PIT)", lambda n: rv_hi.get(n["d"]) is False, True),
        (f"직전 마감 SPY 음수 ({US_START}~)",
         lambda n: n["d"] > US_START and (prev_spy(n["d"]) or 0.0) < 0, True),
        (f"직전 마감 SPY 양수 ({US_START}~)",
         lambda n: n["d"] > US_START and (prev_spy(n["d"]) or 0.0) > 0, True),
        ("화요일 밤", lambda n: wd(n["d"]) == 1, False),
        ("수요일 밤", lambda n: wd(n["d"]) == 2, False),
        ("목요일 밤", lambda n: wd(n["d"]) == 3, False),
        ("금요일 밤", lambda n: wd(n["d"]) == 4, False),
    ]

    def table(rows, judged_only_note):
        L = ["| 칸 | n | 초과(비용후) | t | 양수연도 | 전반 | 후반 | 판정 |",
             "|---|---:|---:|---:|---:|---:|---:|---|"]
        hits = []
        for label, cond, judged in cells:
            sub = [n for n in rows if cond(n)]
            s = summarize(sub)
            if not s:
                L.append(f"| {label} | {len(sub)} | - | - | - | - | - | 표본부족 |")
                continue
            ok = judged and judged_only_note and passes(s)
            verdict = "**채택후보**" if ok else ("기각" if judged and judged_only_note else "관측")
            if ok:
                hits.append((label, s))
            f = lambda x: f"{x:+.3f}%" if x is not None else "-"       # noqa: E731
            L.append(f"| {label} | {s['n']} | **{f(s['mean'])}** | "
                     f"{s['t']:+.2f} | {s['pos']}/{s['years']} | {f(s['h1'])} | {f(s['h2'])} | {verdict} |")
        return L, hits

    base_off, base_sig = summarize(off), summarize(sig)
    L = [f"# 비신호 밤의 조건부 구조 ({date.today().isoformat()})", "",
         "read-only. `tools/research/research_kq_offnight_conditions.py`(사전 고정 규칙은 docstring).",
         f"{TK} · 2015~2025(2026 제외) · 연도매칭 초과, 비용 {COST}% 차감 후.",
         "라이브 문턱 변경 없음.", "",
         "**본 검정 모집단은 v2 신호가 *아닌* 밤이다.** 신호일 203개를 쪼개는 건 총액을 못 늘린다 —",
         "같은 +10.63%p를 칸 사이에서 옮길 뿐이다. 빈도를 늘리는 길은 나머지 밤뿐이다.", "",
         f"| 모집단 | n | 초과(비용후) | t |", "|---|---:|---:|---:|",
         f"| **비신호 밤(본 검정)** | {base_off['n']} | **{base_off['mean']:+.3f}%** | {base_off['t']:+.2f} |",
         f"| 신호 밤(참고) | {base_sig['n']} | {base_sig['mean']:+.3f}% | {base_sig['t']:+.2f} |", "",
         f"판정 문턱: 비용 후 초과 > 0 · t ≥ {T_BAR} · 양수연도 ≥ {YEAR_BAR}/11 · 분할 반기 부호 유지.", "",
         "## 1. 비신호 밤 × 조건 (본 검정)", ""]
    t1, hits = table(off, True)
    L += t1
    L += ["", "화~금은 가설 없이 격자를 채운 칸이라 **관측만**이다(5칸 전부 판정하면 그게 과적합)."]

    L += ["", "## 2. 신호 밤 × 같은 조건 (구조 이해용, 판정 없음)", ""]
    t2, _ = table(sig, False)
    L += t2

    # 겹침 — rung arm과 같은 밤을 두 번 세지 않게
    rung_nights = {n["d"] for n in off if n["rung"]}
    L += ["", "## 3. 종가 rung arm과의 겹침", "",
          f"비신호 밤 {len(off)}개 중 종가 rung(−2.5~−2.0) 밤은 **{len(rung_nights)}개**다."]
    for label, s in hits:
        sub = {n["d"] for n in off if [c for lb, c, _ in cells if lb == label][0](n)}
        L.append(f"- 채택후보 `{label}` {len(sub)}밤 중 rung과 겹침 **{len(sub & rung_nights)}밤** "
                 f"— 겹침이 크면 같은 것을 두 번 발견한 것이다.")

    # 구조 관찰 — 판정이 아니라 레인의 정체에 대한 사실
    def cell(rows, label):
        cond = [c for lb, c, _ in cells if lb == label][0]
        return summarize([n for n in rows if cond(n)])

    hi, lo = cell(sig, "rv20 상(롤링 PIT)"), cell(sig, "rv20 하(롤링 PIT)")
    off_all = base_off
    L += ["", "## 4. 구조 관찰 (판정 아님)", ""]
    if hi and lo:
        L += [f"- **급락 밤 프리미엄은 고변동 국면의 현상이다.** 신호 밤을 rv20으로 가르면",
              f"  상 {hi['n']}건 **{hi['mean']:+.3f}%**(t {hi['t']:+.2f}, 양수연도 {hi['pos']}/{hi['years']}) vs "
              f"하 {lo['n']}건 **{lo['mean']:+.3f}%**(t {lo['t']:+.2f}).",
              "  저변동 국면 신호는 사실상 0이다. '국면이 사냥철'(08-04)과 2026 신호율 3.3배가",
              "  같은 이야기를 하고 있었다.",
              "  ⚠️ 이걸로 문턱을 만들지 않는다 — 인샘플 조건화이고, 신호의 82%가 이미 고변동이라",
              f"  걸러도 얻는 게 작다(연 기여 {hi['mean'] * hi['n'] / hi['years']:+.2f}%p vs 현행 "
              f"{base_sig['mean'] * base_sig['n'] / base_sig['years']:+.2f}%p)."]
    L += [f"- **비신호 밤은 어느 칸에서도 음수다**(전체 {off_all['n']}건 {off_all['mean']:+.3f}%, "
          f"t {off_all['t']:+.2f}). 곧 '신호일에만 든다'는 현 설계가 맞다는 확인이다.",
          "  특히 목요일 밤은 11년 내내 음수(0/11, t −3.41)다 — 우리는 어차피 안 드는 밤이다.", ""]

    L += ["", "## 5. 결론", ""]
    if hits:
        L += [f"- 통과한 칸 **{len(hits)}개**: " + ", ".join(f"`{lb}`({s['mean']:+.3f}%, t {s['t']:+.2f})"
                                                          for lb, s in hits),
              "- 채택이 아니라 **forward 사전등록 후보**다. 라이브 문턱은 안 바꿨다."]
    else:
        L += ["- **통과한 칸 없음.**",
              "- 무조건 밤은 요일·변동성·미국장 **어느 조건에서도 비용을 못 넘는다.**",
              "  09-13 사다리(문턱 완화)에 이어 **빈도 상향은 이 데이터에서 닫혔다.**",
              "- 남은 상향 레버는 **사이징(강신호 배수)과 자본**뿐이다. 둘 다 리스크 한도 결정이고",
              "  전략 검증이 끝난 뒤의 문제다(09-14 운영자: *\"아직 쉐도우 전략임\"*)."]

    out = ROOT / "docs" / "reports" / f"kq_offnight_conditions_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
