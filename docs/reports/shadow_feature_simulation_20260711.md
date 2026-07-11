# 쉐도우 기능 전체 DB 시뮬레이션 — 기능별 개선점 축 분석 (2026-07-11)

현 시스템의 모든 쉐도우/게이트/출구 기능을 기존 DB(`data/ml/decisions.db` closed 315건, 2026-04-27~07-06,
`data/v2_event_store.db`, `candidate_audit.db`, us_swing OOS 293세션)로 리플레이해 각 기능의 net 개선효과와
개선 축을 실측했다. 판정 기준 = **pnl_pct_net(실net)**, KR/US 분리, 좌측/우측 꼬리, 유효표본 우선. read-only.

## ★★ 공통 병목 (모든 MFE 기반 판정에 걸림 — 먼저 읽을 것)
`mfe_pct`가 **실보유 window 최고점이 아니라 고정 forward-window 백필**로 확인됨: net>mfe_pct 위반 10/313(3%),
mfe<0.5% 버킷의 net max가 **+2.74**(실보유 max라면 불가능). 따라서 **early-tier·capture·peak_floor 등 MFE 기반
counterfactual의 net 개선치는 방향은 신뢰하되 크기는 낙관 ceiling**이다. 깨끗한 판정의 유일 해제책 =
**forward `mfe_time`/`mae_time`(7/10 배선, 현재 0/315) 축적** → held-window·순서 인식 MFE 확보.

---

## 기능별 판정 (Tier 순)

### TIER 1 — net 가치 실증 (이미 라이브)
**red-tape US 게이트 (enforce)** — 진입 순간 QQQ 개장대비 −0.3% 미만이면 신규진입 차단.
- RED(would-block) n=49 **net합 −60.42**·평균 −1.23·승14%·net<−1% 30건 / GREEN n=147 평균 −0.03·승37%.
- **Simpson 통과**: RED가 GREEN보다 CAUTIOUS·MILD_BEAR·MILD_BULL·NEUTRAL 및 **6월 내부에서도** 뚜렷이 나쁨 = 국면/월 착시 아님. 예외 MODERATE_BULL(강세국면 tape 변별 소멸).
- **개선 축**: 진입 순간 시장 intraday 방향(종목 아님). 좌측꼬리 집중.
- 판정: **유일하게 실net 가치 실증**. 이미 라이브 enforce. (KR은 red-tape 진입도 흑자라 부적합, 미적용 유지)

### TIER 2 — 강한 방향성, 낙관 ceiling (다음 최우선 검증)
**목표 캘리브레이션 / 조기익절 tier (early-tier)** — 계획 목표 과대 → 도달가능 레벨 조기익절.
- 목표 중앙 US 5.72%(도달률17%)·KR 7.19%, MFE 중앙 US 2.52%. 조기익절 부분 f=0.25~0.33(US 2.3%/KR 3.6%):
  US net −0.18→+0.07~0.16·KR −0.22→+0.52~0.69. **top-3 제외·월별·국면 전부 견고**, 러너 보존(f 낮을수록).
- **개선 축**: per-name 출구 타이밍(목표를 MFE 분포에 맞춤). 시장타이밍 아님.
- ★caveat: mfe_pct 낙관ceiling(위 공통병목) → 크기 과대 가능, 방향 견고. RR 커플링상 목표하향 금지=**LADDER 조기익절 tier**로만.
- 판정: **가장 유망한 개선 레버**. shadow 도구 `early_tier_shadow_review.py` 배치됨. forward held-MFE 검증 후 enforce.

### TIER 3 — 방향성 시사, 라이브 net 미확립 (표본/tautology/배포후 부재)
**peak_floor / entry-floor stops** — entry기준 stop이 정점 대비 얼마나 반납하는가.
- 도구 "발동 24/24 개선 +32.6%p"는 **정의상 tautology**(발동=actual≤trail_trigger, cf=trail_trigger → 구조상 항상 cf≥actual). 실측된 것은 **undershoot 크기**지 전환 delta 아님(지는 쪽·좋은출구 제외).
- 실증: entry-floor stop이 정점 반납함(최대=US **CLAUDE_PRICE_STOP** undershoot 합 +24.5). = **개선 여지 지점**.
- 판정: undershoot 방향 실증O, 전환 net 미측정.

**weak_mfe cut (shadow)** — 약MFE+손실 조기컷.
- 실컷 US n=6 net −0.91(승0%) vs 안끊은 약손실 코호트 −1.96(n=39) = 좌측꼬리 축소 방향O.
- ★결함: **slow-starter false-cut 실재**(CRCL mfe3.4·AAOI mfe5.4가 30분 관측 mfe로 컷됨). net 부호 불결.
- 판정: 방향 유효, false-cut율 계측(mfe_time forward) 선결.

**LADDER A/B peak-trail (US enforce)** — 배포후(06-27+) claude_price 출구 표본 **2건**(진입 고갈→rule_direct 전환).
- 자주 인용된 PROFIT_LADDER **+0.35%p는 배포전 노이즈**(배포전 A·B 둘 다 policy A 실행, 해시 무작위 반쪽 차이). 인용 금지.
- 판정: 배포후 표본부재로 미측정.

**TAIL_CAPTURE (shadow)** — 러너 오버나잇 캐리. activation(+4%) 도달 US 4/46·KR 0.
- 판정: 활성 n=4 미성숙. 오버나잇 forward 재구성 없이는 net 판정 불가. (US MFE4+ 러너 상당분은 이미 당일 CLAUDE_PRICE_TARGET n=21 +96.5로 수확 중 — 오버나잇이 당일 TARGET을 이기는지 미해결)

### TIER 4 — forward 전용 (미배선/inert, 소급 측정 불가)
**profit_evidence gate (shadow)** — 과거 payload에 profit_evidence **0건** → shadow=전량 allow(개선효과 측정불가), **enforce=전량 ABSTAIN(신규매수 100% 차단)**. 승격모델+evidence 파이프 흐르기 전 구조적 forward 전용.

**profit_path shadow predictor** — forward 0. validation **selected_n=0 양시장**(게이트 prob≥0.55·비용후net≥0.25 동시충족 표본 0). US AUC 0.638 변별은 있으나 cost 0.50에서 hurdle 미달. validation 창 3세션뿐=inert 강증거·무엣지 약증거.

**KR bullish probe** — `candidate_audit.db`에 `bullish_probe_selected` **컬럼 부재**=미배선. 표본 0. (7/10 003280 +1.08~1.44%는 수동 replay·미영속)

**US Swing OOS sleeve** — 55 executable rank1, 0.5% 슬리피지 mean **+2.09%**/PF1.51지만 **block_lcb −0.11(음)**. 엣지 **2026 집중**(2025 40tr PF1.09 mean+0.42=사실상 사망), worst −27%, 단일 Yahoo 벤더. "passed"=완화밴드(−0.25)이지 LCB>0 아님. 판정: 하한 미돌파, 독립벤더 교차검증 선결.

### TIER 5 — 사망/무레버 (실측 종결)
- **VIX term (S2)**: 방어 OOS기각·공격 부호불안정. 우리 창 100% 콘탱고=신호 미발화. 종결.
- **breadth green-tape (S3)**: 우리 top-net일 breadth 무시그니처(진입일 r=0.079). narrow melt-up. 종결.
- **calendar (S1)**: 이벤트수 2~3개=검정력 부족, US 이벤트일 손실꼬리 안두꺼움. 무레버.
- **correlation cluster (A3)**: 동시청산일 평균상관 max 0.46(≥0.5 0일). 동시손실=상관 아닌 시장베타. 무레버(도구 버그 수정=4b3e994).
- **repeat_loss cooldown (C3)**: 실wired 게이트 코호트 극소(MAX3 n=4 +1.38·MAX2 n=11 −2.32), 무신호+로그 미영속 배선결함. (bucket도구 −81.97은 별개 규칙, 오인용 금지)

---

## 개선점 우선순위 (수익 방향)
1. **held-window MFE 확보 (공통 unlock)**: forward `mfe_time`/`mae_time` 축적 → early-tier·capture·peak_floor·weak_mfe false-cut·risk-recovery(P2-3) 전부의 깨끗한 판정 전제. 봇 재시작으로 축적 시작(7/10 배선).
2. **early-tier(Tier2)**: 유일하게 방향 강건한 개선 레버. held-MFE로 크기 재확인 → LADDER 조기익절 tier shadow → enforce(운영자).
3. **entry-floor stop undershoot(US CLAUDE_PRICE_STOP)**: peak-trail 치환의 실제 이득 지점 — 전환 net-delta를 양측 측정.
4. **forward 배선/축적**: profit_evidence(evidence 파이프)·profit_path·KR probe(컬럼 배선)·us_swing(독립벤더) = 재시작 후 데이터 필요.
5. **종결 유지**: VIX·breadth·calendar·correlation·repeat_loss는 재발굴 금지.

## 결론
- **현재 net 가치가 실증된 쉐도우/게이트 기능은 red-tape US 하나**(이미 라이브).
- **가장 유망한 미적용 개선 = early-tier(목표 캘리)**, 단 mfe_pct 낙관ceiling이라 held-MFE forward 검증 후 확정.
- **대부분 출구 레버는 방향성만 유망·라이브 net 미확립** — 공통 원인은 mfe_pct 계측 한계 + 표본부족(claude_price 진입 고갈).
- **forward 축적(재시작+시간축)이 거의 모든 미판정 기능의 공통 해제책.** 시장레벨 신호(VIX·breadth·calendar)는 종결.

관련: [[target-calibration-lever-20260711]], [[workplan-execution-status-20260711]], [[session-handoff-signal-discovery-20260710]]
