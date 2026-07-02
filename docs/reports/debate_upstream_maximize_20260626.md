# 토론 리포트 — 전체 시스템 상류/극대화 개선점 (항목별) 2026-06-26

> 운영자 주제: 전체 시스템에서 부족·극대화·개선 포인트, 특히 아직 분석 안 한 스크리너.
> 4개 항목을 각각 명제로: ①스크리너 소스 품질 ②KR 양수경로 under-utilization ③우측꼬리 capture ④자본 활용.
> 방식: PRO(상류 레버 실재)/CON(측정 함정) 입론 + 사회자 직접 sqlite 재검증. READ-ONLY.

---

## 1. 항목별 판정 (양측 + 사회자 검증)

### ① 스크리너 소스 품질 — **거의 죽은 레버 (no-op)**
- source_type별 forward_3d 격차(opening_fresh_rescreen −10.22%, volume_rank −3.58%, most_actives +0.35%)는 **lookahead 라벨**.
- **결정적:** 음수 소스는 **traded=0** — opening_fresh_rescreen(N=118)·kr_wait60_reeval(N=782)·volume_rank 모두 **실거래로 한 건도 안 감.** 줄여도 net 변화 0.
- forward 순서가 traded net 순서와 **역전**(initial 라벨 −1.31<rescreen −1.72 / traded net +0.15>−0.84).
- 잔존: most_actives traded net +1.9%(N=8) — 유일하게 부호 유지하나 결론 불가 표본.
- **판정: "음수 소스 축소"는 안 거래되니 no-op. 스크리너 품질 차이는 lookahead 착시. 스크리너는 레버 아님.** (운영자 직관은 확인할 가치 있었으나 데이터가 닫음.)

### ② KR 양수경로 under-utilization — **저빈도 정당, 전면완화 금지**
- KR 진입 1.6건/일·41일 중 20일 0건. 미실행 ready=1의 96%(106/110)가 **signal_fired=0**(진입조건 미충족).
- **게이트가 우량 선별 중:** 미발사 forward +2.19% < 발사 +4.06%(win58). 완화하면 열위 진입 추가 = net 악화.
- 단 **KR claude_price는 net 양수**(+0.74%, 6월 +0.955% PF 2.11) — KR 적자라고 같이 죽이지 말 것.
- **판정: KR 저빈도는 손실 회피라 정당. 전면 완화 금지. claude_price 패밀리 한정 소폭 완화만 traded net으로 측정(shadow).**

### ③ 우측꼬리 capture — **가장 유망하나 측정 불가 (MFE 데이터 희소)**
- 신호 존재: capture_net_review에서 TARGET capture 51%(net +4.73/+5.97 win100) vs PROFIT_LADDER capture 10.7%(net +0.13). 승자 giveback US 2.32pp·KR 3.68pp.
- **사회자 검증(v2 mfe_pct, 6/19 배선 이후만):** CLOSED_TRAILING_STOP N=7 MFE +7.37% → 실현 +2.79%(**giveback 4.59%p, capture 20%**). 신호 있으나 PROFIT_LADDER N=2·TARGET N=2로 **결판 불가.**
- CON: 고capture 경로는 승자 생존편향, net 질량은 좌측꼬리(loss_cap), WEAK_MFE capture −22.6%(더 버티려다 반납) = 양날.
- **판정: 가장 실재 가능성 큰 레버지만 라이브 MFE 데이터가 N=2~7로 너무 희소해 지금 결판 불가. 좌측꼬리(loss_cap, A1 처리중) 고정이 선행. MFE 측정 누적이 먼저.**

### ④ 자본 활용 — **기각**
- 일일 캡 시뮬 PF **단조 감소**: per_market cap_1 1.40 > cap_2 0.71 > cap_3 0.69. 더 거래/더 크게 = PF 하락.
- 모집단 net 음수(US 6월 full book PF 0.46, net −48.3%). 자본 키우면 손실 비례 확대.
- **판정: 기각. net 양전(setup-level) 확인 전 자본·사이즈 확대 금지. 양측 합의(PRO도 스케일업 반대).**

---

## 2. 종합 — 항목별 한눈에

| 항목 | 판정 | 행동 |
|---|---|---|
| ①스크리너 소스 품질 | **죽은 레버(no-op)** | 트랙 닫기. 음수 소스 traded=0, 품질차는 lookahead 착시 |
| ②KR under-utilization | **저빈도 정당** | 전면완화 금지. claude_price 한정 traded-net 측정만(shadow) |
| ③우측꼬리 capture | **유망하나 측정 불가** | **MFE 측정 인프라 누적이 선행**(net 백필의 capture 버전) |
| ④자본 활용 | **기각** | 스케일업 금지(net 양전 전) |

---

## 3. 정직한 메타 결론

운영자가 찾는 **"극대화" 상류 레버는 데이터상 거의 없다.** 네 항목 모두:
- ①은 lookahead 착시(안 거래되는 소스),
- ②는 이미 올바르게 보수적(저빈도가 손실 회피),
- ④는 반증(net 음수에 키우면 손실 확대),
- ③만 유망하나 **MFE 라이브 데이터가 희소해 지금 못 잰다.**

반복되는 진실: **net 레버는 여전히 좌측꼬리(loss_cap, A1이 78% 처리중)이고, 검증된 상류 극대화 레버는 현재 없다.** 단 ③(capture)은 *측정만 갖추면* 진짜 레버일 수 있다 — net 백필이 명제1을 결판냈듯, **MFE/capture 백필**이 ③을 결판낼 측정 공백이다.

## 4. 유일하게 가치 있는 다음 측정 (개선 ROI 순)
1. **MFE/capture 측정 인프라 누적** — mfe_pct가 6/19 이후만 존재(N=2~7). yfinance 백필(mfe_backfill_yf) 확장 or 누적 대기 → ③ capture 레버를 결판낼 유일한 길. (코드 아닌 측정.)
2. **KR claude_price 한정 진입기준** — traded net으로만, shadow. 살아있는 +0.74% 경로 극대화 측정.
3. **닫기:** ①스크리너 소스 축소(no-op), ④자본 스케일업(net 양전 전).

## 5. 한 줄 결론
스크리너 포함 상류 극대화 4항목 중 ①(스크리너)은 안 거래되는 소스라 no-op, ②(KR 저빈도)는 정당한 손실회피, ④(자본)는 net 음수라 기각 — **③(우측꼬리 capture)만 유망한데 라이브 MFE 데이터가 N=2~7로 희소해 지금 못 잰다.** net 레버는 여전히 좌측꼬리(A1 처리중)이고, 다음 측정 우선순위는 새 기능이 아니라 **MFE/capture 백필**이다.
