#!/usr/bin/env python3
"""급등일 밤 — 패닉 레인의 거울상 검정 (read-only, 2026-09-12).

## 왜 이걸 보나

09-12 전면 감사에서 가장 강한 **부호 있는 실패 신호**는 `xkr_rise5`였다
(급등 다음날 매수, 지수 초과 −3.22% · t −7.42 · 26,923건/310일). 실패가 그만큼 일관되면
반대쪽에 구조가 있을 수 있다. 그리고 우리는 같은 이동을 이미 한 번 했다 —
개별주 급락 신호를 용량 때문에 지수 ETF로 옮긴 것이 패닉 레인이다.

그래서 **같은 이동을 급등 쪽에 그대로 적용한다**: 개별주 급등 신호를 지수 ETF로 옮기고,
패닉 레인과 **정확히 대칭인 조건**으로 밤 수익을 잰다.

  패닉:  코스닥 시가 갭 ≤ −1%  **또는** 종가 등락 ≤ −2.5%  → 종가 매수 · 다음 시가 매도
  거울:  코스닥 시가 갭 ≥ +1%  **또는** 종가 등락 ≥ +2.5%  → 같은 보유 규약

## 무엇이 살아남는가 — 숫자 보기 전에 고정

패닉 레인과 같은 문턱을 쓴다. **연도매칭 초과의 |t| ≥ 2 이고 부호가 연도 12개 중 9개 이상
일관되면** 방향성 있는 축으로 등록한다. 못 넘으면 기각이다.

⚠️ 음수가 나와도 **"인버스 레인"이 되는 건 아니다.** 코스닥 레버리지 ETF는 인버스 상품이
따로 필요하고, 여기서 재는 것은 롱 보유의 밤 수익뿐이다. 음수는 "그 밤엔 들지 마라"는
회피 정보이지 새 매수 레인이 아니다.

벤치는 **연도매칭 무조건 오버나이트**다(11년 풀드 스칼라를 쓰면 고drift 연도가 부풀려진다
— `research_panic_etf_year_decomp.py`의 보정 1과 같은 이유).

라이브 쉐도우 모듈(`tools/kr_index_etf_panic_shadow.py`)은 **읽기만** 한다.
"""
from __future__ import annotations

import statistics as st
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tools.kr_index_etf_panic_shadow as P  # noqa: E402  (read-only)
from tools.research.research_panic_etf_year_decomp import t_stat, uncond_by_year  # noqa: E402

GAP_UP = 1.0        # 패닉 GAP_THR = −1.0 의 거울
CLOSE_UP = 2.5      # 패닉 종가 문턱 −2.5 의 거울
T_BAR = 2.0
SIGN_YEARS_BAR = 9


def arms():
    """(라벨, 조건) — 패닉 v2의 세 갈래를 그대로 뒤집는다."""
    return [
        (f"종가 ≥ +{CLOSE_UP}%",
         lambda c: c.get("kq_chg") is not None and c["kq_chg"] >= CLOSE_UP),
        (f"갭 ≥ +{GAP_UP}%",
         lambda c: c.get("kq_gap") is not None and c["kq_gap"] >= GAP_UP),
        (f"갭≥+{GAP_UP} or 종가≥+{CLOSE_UP} (v2 대칭)",
         lambda c: (c.get("kq_gap") is not None and c["kq_gap"] >= GAP_UP)
         or (c.get("kq_chg") is not None and c["kq_chg"] >= CLOSE_UP)),
    ]


CUT_YEAR = "2026"     # 패닉 사전등록이 2026을 "다른 국면"으로 분리한 것과 같은 절단


def overnight(cache, dates, i, tk):
    """i일 종가 매수 → i+1일 시가 매도, 비용 전 %."""
    a, b = cache[dates[i]], cache[dates[i + 1]]
    ca, ob = a.get(f"{tk}_close"), b.get(f"{tk}_open")
    if not ca or not ob:
        return None
    return (ob / ca - 1.0) * 100.0


def main():
    cache = P._read_cache()
    dates = sorted(cache)
    L = [f"# 급등일 밤 — 패닉 레인의 거울상 ({date.today().isoformat()})", "",
         "read-only. `tools/research/research_kq_updays_overnight_mirror.py`(사전 고정 기준은 docstring).",
         f"등록 문턱: 연도매칭 초과 |t| ≥ {T_BAR} 이고 부호 일관 연도 ≥ {SIGN_YEARS_BAR}/12.", "",
         "⚠️ 음수가 나와도 인버스 **레인**이 아니다 — 롱 보유의 밤 수익만 잰 것이고,",
         "음수는 '그 밤엔 들지 마라'는 회피 정보다.", ""]

    summary = {}
    for tk, name in P.ETFS.items():
        by_year = uncond_by_year(cache, tk)
        pooled = P._uncond_overnight(cache, tk)
        L += ["", f"## {tk} {name}", "",
              "| 조건 | n | 원 오버나이트 | **초과(연도매칭)** | t | 양수연도 | 판정 |",
              "|---|---:|---:|---:|---:|---:|---|"]
        pairs = [(lb, cd, False) for lb, cd in arms()]
        pairs.append((f"갭≥+{GAP_UP} or 종가≥+{CLOSE_UP} · {CUT_YEAR} 제외", arms()[-1][1], True))
        for label, cond, cut in pairs:
            recs = []
            for i in range(len(dates) - 1):
                c = cache[dates[i]]
                if cut and dates[i][:4] >= CUT_YEAR:
                    continue
                if not cond(c):
                    continue
                raw = overnight(cache, dates, i, tk)
                if raw is None:
                    continue
                y = dates[i][:4]
                recs.append((y, raw, raw - by_year.get(y, pooled or 0.0)))
            if not recs:
                L.append(f"| {label} | 0 | - | - | - | - | 표본없음 |")
                continue
            ys = sorted({r[0] for r in recs})
            pos = sum(1 for y in ys if st.mean(r[2] for r in recs if r[0] == y) > 0)
            t = t_stat([r[2] for r in recs])
            ex = st.mean(r[2] for r in recs)
            ok = t is not None and abs(t) >= T_BAR and max(pos, len(ys) - pos) >= SIGN_YEARS_BAR
            L.append(f"| {label} | {len(recs)} | {st.mean(r[1] for r in recs):+.3f}% | "
                     f"**{ex:+.3f}%** | {t:+.2f} | {pos}/{len(ys)} | "
                     f"{'**등록**' if ok else '기각'} |")
            if "대칭" in label:
                summary.setdefault(tk, []).append((label, ex, t, pos, len(ys), ok))

    L += ["", "## 읽기", ""]
    for tk, items in summary.items():
        for label, ex, t, pos, ny, ok in items:
            L.append(f"- **{tk}** {label}: 초과 **{ex:+.3f}%** · t {t:+.2f} · 양수연도 {pos}/{ny} → "
                     f"{'등록' if ok else '**기각**'}")
    L += ["",
          "두 종목 모두 음수이지만 |t| 문턱을 못 넘는다 → **기각**. 2026을 빼면 233740이",
          f"t −1.98로 문턱 {T_BAR}에 0.02 모자라는 근접 미달인데, **근접은 통과가 아니다**.",
          "부호가 음수로 치우친 것(양수연도가 절반 아래)은 방향은 있다는 뜻이라",
          "회피·관측 항목으로만 남긴다.",
          "급락일(+)·무조건(0 기준)·급등일(−)이 단조로 늘어서는 것이 이 레인의 실체다 —",
          "밤 프리미엄은 **방향 조건부**다."]

    out = ROOT / "docs" / "reports" / f"kq_updays_overnight_mirror_{date.today().strftime('%Y%m%d')}.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\n리포트 저장: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
