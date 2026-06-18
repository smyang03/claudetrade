# 후보군 선정 품질 — 최종 분석 리포트

- 생성: 2026-06-16
- 방식: 운영 DB 읽기 전용(`candidate_audit.db`, `decisions.db`, `ticker_selection_log.db`, `v2_event_store.db`). 코드·config·brain·broker 미변경.
- 통합 출처:
  - 원본: `candidate_selection_quality_20260616_122910.md`
  - 심층/검증: `candidate_selection_quality_followup_20260616.md`
- 질문: "시스템이 후보군을 잘 골랐나?"

---

## 0. 최종 결론 (한 줄)

> **"잘 골랐다"는 사실이 아니다.** 시스템이 본 지표(forward/MFE)는 *후보가 준 기회*를 쟀고 *시스템이 챙긴 돈*은 못 쟀다. 실현으로 보면 **US는 강세장(5월)에서만 얇게 됐고 6월엔 음수로 꺾였으며, KR은 어느 국면에도 진다.**

시스템은 **"움직일 종목을 감지"하지만 "돈으로 바꾸지" 못한다.**

---

## 1. 왜 "잘 골랐다"는 착시였나 (측정 편향)

근거: `ticker_selection_db.py:737 _calc_forward_return` — 기준가 = **선정일 종가** → N일 뒤 종가.

시스템의 품질 지표가 구조적으로 좋게 나온 5가지 이유:

1. **기준가가 선정일 종가, 실제 진입가 아님** — 봇은 장중 진입.
2. **N일 버티기 반사실 — 봇 실제 청산 무시** (loss_cap/stop이 러너를 장중에 끊음).
3. **수수료 없음** — 실현 net은 왕복 ~0.5% 차감.
4. **MFE/runup = "움직였나"지 "먹었나"가 아님** — +5% 찍고 −2% 마감해도 "감지 성공".
5. **forward는 체결 안 된 후보에 측정됨** — FORWARD_MEASURED ↔ 실제 closed 매칭 **0건**. "forward 좋다"와 "실현"은 **다른 종목군**.

→ 거짓이 아니라 **다른 축을 측정.**

---

## 2. 실제 실현 성과 (v2 원장, closed=238)

| 시장 | n | win% | gross | net* | fee* | avgMFE | capture(pnl/mfe) |
|---|---|---|---|---|---|---|---|
| US | 190 | 44 | +0.46 | -0.25 | 0.500 | 3.01 | -0.64 |
| KR | 48 | 27 | -1.01 | -1.58 | 0.210 | 2.93 | 0.01 |

(*net/fee는 US 33건·KR 3건만 채워짐)

### 2.1 국면(월별) 분해 — 핵심

| 시장 | 2026-04 | 2026-05 | 2026-06 |
|---|---|---|---|
| US | +0.14 (win57, n21) | **+0.86 (win50, n103)** | **−0.09 (win32, n71)** |
| KR | −1.27 (win26, n23) | −0.69 (win33, n21) | −0.79 (win20, n5) |

- **US "+0.46% 평균"은 5월 강세장(QQQ +11.76%)에 집중. 6월(n=71)은 이미 음수·승률 32%로 꺾임.** → US edge는 안정적 속성이 아니라 **강세장 의존** 가능성.
- **KR은 매월 음수 → 국면 독립 구조적 손실.**

### 2.2 capture gap (KR)

KR은 평균 MFE +2.93%를 만들고도 실현 −1.01%, **회수율 0.01 = 우호 변동 0% 회수.** 전략별 capture: momentum 0.17, gap_pullback 0.00. → KR 문제는 selection만이 아니라 **청산/capture.**

---

## 3. 후보 선별 능력 자체 (forward-1d, 반사실)

| 시장 | 코호트 | n | win% | avgRet |
|---|---|---|---|---|
| US | selected | 541 | 45.5 | **+0.33** |
| US | 미선정 | 1327 | ~49 | ~−0.24 |
| KR | selected | 264 | 37.9 | **−2.21** |
| KR | 미선정 | 815 | ~37 | ~−1.5 |

- US: selected가 미선정보다 좋음 → 순위 능력 있음(단 §1·2.1 한계 적용).
- **KR: selected(−2.21)가 미선정(−1.44)보다 더 나쁨 → "고른 게 안 고른 것보다 나쁨".** KR 후보 확대(broad/narrow 모두) 근거 없음(미선정 어느 bucket·score 밴드도 음수).
- US `blank` 미선정 포켓(win 67.6%, +2.38%)은 11세션 편중·unscreened → promote 아닌 **origin 조사 대상**.

---

## 4. 검증으로 확정/기각된 가설 (same-trade 분해)

v2_event_store 실행 체인(HIT→FILLED→CLOSED)으로 검증:

| 가설 | 결과 | 근거 |
|---|---|---|
| "추격 진입이 edge를 먹는다" | **기각** | 진입슬립 US +0.03%, KR +0.11% (무관) |
| "며칠 보유가 낫다" | **기각** | 1d>3d>5d, 들수록 악화(KR −6~9%) |
| "수수료가 실질 friction" | **확정(US)** | US fee 0.500% vs gross +0.46% → net 본전↓ |
| "forward가 품질 근거" | **기각** | forward는 미체결 후보에 측정(매칭 0) |
| "US edge는 안정적" | **기각** | 5월빨, 6월 음수·win32% |
| "KR은 selection 문제만" | **수정** | selection 약함 + capture 0.01 동시 |

---

## 5. 데이터 한계 / 미검증 축 (정직 공개)

- **실현 net 필드 희소**: `pnl_pct_net`·`fee_pct_round_trip` US 33/KR 3, `mfe_pct` 23건. → capture·net **정밀 크기 미확정.**
- **슬리피지 원장 필드 비어있음**: `first_seen_price`=0, `entry_price_vs_*`=6건. (검증은 event_store HIT가로 우회)
- **6월 US 음수가 국면 전환인지 노이즈인지** 단월(n=71) → 7월 재확인 필요.
- **KR 표본 작음**: closed 48, 6월 5건, slippage 25.
- counterfactual close 라벨 5/18 이후 중심.

---

## 6. 방향 (우선순위, 검증 반영)

| 순위 | 항목 | 근거 | 영역 |
|---|---|---|---|
| 1 | **측정 지표 재정의** (forward → 실제 진입가·실제 청산·net 차감) | §1, 모든 착시의 뿌리 | 코드, 운영자 승인 |
| 2 | **US 6월 꺾임 모니터** + 수수료/거래빈도 정리 | §2.1, §4 | 관찰 → 운영자 판단 |
| 3 | **KR 축소·관찰** (확대·보유연장 금지) | §2.1, §3 | 운영자 승인 |
| 4 | **net/fee/mfe backfill** (capture 정밀화 선결) | §2, §5 | read-only 점검 가능 |
| - | 전략 추가 | **하지 않음** (입력·실행 품질 우선) | - |

**기각된 방향**: 추격 진입 차단(§4), 보유기간 연장(§4), KR 후보 확대(§3), US blank 즉시 promote(§3).

---

## 7. 작업 경계

- 이 리포트는 **분석만** 수행. gate·strategy·PathB profit ladder·broker truth·config·env·brain 미변경.
- §6의 4는 추가 read-only 분석으로 진행 가능. §6의 1·2·3은 코드/운영 변경이라 운영자 승인 후 별도 작업.
- CLAUDE.md 보호계약(US PathB 수익 엔진, KR live 확대 금지, `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS=true`) 위반 없음.
