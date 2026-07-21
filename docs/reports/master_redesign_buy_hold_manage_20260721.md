# 마스터 재설계 — 매수 복원 + 가지 정리 + hold_advisor 경량화 (2026-07-21)

운영자 지시: enforce 전제로, "매수도 Claude 판단하지"부터 전체를 원 설계로 복원.
가지(손실 예외처리)를 걷어내고, hold_advisor 경량화까지. 트레이너+개발자 종합.

원 설계(운영자 확인): **좋은 후보를 사서 → 상승·하락을 hold_advisor로 관리 → 볼록 보유.**

## 0. 원 설계가 가려진 서사 (git으로 확증)

| 시점 | 사건 |
|---|---|
| 5/15 `922c6cc` | `CLAUDE_REVIEW_ALL_AUTOMATED_SELLS` — "hold_advisor가 자동매도 다 판단" (원 설계 정점) |
| 5/27 `4242367` | **하드가드 신설** — review_all을 무력화하려 최외곽에 덧붙인 가장 늦은 가지 |
| (진입측) | judge 프롬프트가 `BUY_READY` 금지 — Claude가 "사라"를 낼 권한 박탈 |

결과: **매수 0(2주 4건) + hold_advisor 우회 = 원 설계 마비.** 정교한 관리 기계가 입구도
막히고 관리 권한도 뺏긴 채 놀고 있다.

---

## 축 1 — 매수 복원 ("매수도 Claude 판단")

### 근본 (조사 확증)
`execution/single_symbol_judge.py:116`: *"Do not use BUY_READY or PROBE_READY"* — Claude judge는
즉시매수를 낼 수 없고 `PULLBACK_WAIT`(눌림 대기)만 가능. 그 눌림 조건마저 상승장과 배타적
(프롬프트 "현재가 0.5% 아래" vs 검증 "0.35% 위까지" 모순, `:120` vs `:220`). 상승 모멘텀
후보엔 유효 존이 없어 자동 `WAIT_RECHECK`.

### 재설계
1. **judge에 BUY_READY 판단권 복원** (`single_symbol_judge.py:116` 프롬프트). Claude가 셋업
   성격을 판단해 **즉시매수(추세) vs 눌림대기(평균회귀)를 스스로 선택**. 이게 "매수도 Claude
   판단"의 직접 구현.
2. **시장·국면 차등** (오늘 데이터 근거): US 강세일 급등 +0.45%(추세 이어짐)·볼록꼬리 상위10%
   +8.23% → **US 강세 국면 즉시매수 허용**. KR 강세일 −1.30%(추격 죽음) → **KR·일반은 눌림
   유지**. anti-chase(MAX≥25)·dip gate는 그대로.
3. **눌림존 모순 창 완화** (`SINGLE_SYMBOL_JUDGE_MAX_ZONE_ABOVE_CURRENT_PCT` 등 env) — 상승
   국면 한정.
4. **호출 희소성 완화** — warmup 5분·티커당 2콜·REJECT 180분 TTL이 상승장 초반 기회 차단.
   국면별 조정.

위험: 즉시매수=추격이고 추격은 반증 이력(존상단·anti-chase). **완화책=US 강세 국면 한정 +
좋은 후보 선별 유지.** 넓게 사기(−0.21)와 구분.

---

## 축 2 — 가지 정리 (hold_advisor 관리 복원)

### 우회 3층 구조 (조사 확증)
- **층1** `_auto_sell_review_required:27214`: policy_hard_stop·policy_protective_stop·(review_all=
  false시)mfe_breakeven은 **아예 리뷰 안 함**.
- **층2** `_auto_sell_review_force_sell_required:27707`: recovery_micro·pre_close·손실−2.5%초과는
  리뷰 진입해도 강제 SELL.
- **층3** `_auto_sell_hard_guard_breach:17635`: 손절선 breach 시. **장후(`26566`)·장마감
  (`40561`) carry 경로는 review_all=true여도 hold_advisor 완전 미호출** ← 운영자 지적 지점.

### 재설계 원칙: 하드가드를 없애지 말고 **재앙방지선으로 올리고, 그 안은 hold_advisor에게**
1. **재앙방지선 vs 노이즈선 분리** (급소):
   - 하드가드 발동선 = 재앙방지선(예: −4~5% 또는 구조 이탈)에만.
   - 얕은 선(−2% loss_cap)은 **hold_advisor 판단 대상**으로 (우회 금지).
   - 근거: LOSS_CAP 52건 mfe+1.77% = 봉우리 찍고 −2% 노이즈에 잘림. 멀티데이 +1.94%.
2. **장후/장마감 carry 경로(26566·40561) hold_advisor 호출 복원** — 하드가드 breach여도
   재앙방지선 이내면 hold_advisor에게 carry 판단. 시간 컷(pre_close force_sell) 제거.
3. **policy_hard_stop 항상 우회(층1) 재검토** — PlanA policy가 별도 손절 권한을 갖는 것 축소.
4. **loss_cap −2% 고정 → 종목별/구조 기반** (`risk_manager.py:601-636`). ATR/realized_vol
   배수 또는 진입구조 이탈. 단순 완화 금지(구조 결합).

⚠️**최대 위험 구역**: 손절선 완화는 cap 완화 반증 이력(손절선행 32/36)과 맞닿음. 재앙방지선
자체는 유지하되 발동선 폭을 데이터로. **이 축은 shadow 실측 없이 enforce 금지.**

### 유지할 가지 (진짜 방어 — 제거 금지)
daily_loss_stop·broker_mismatch·operator_kill·pathb_kill(catastrophic), 재앙방지선 하드가드,
anti-chase, dip gate. 이건 가지가 아니라 뿌리.

---

## 축 3 — hold_advisor 경량화 (매수 복원 시 비용 폭증 대비)

### 조사 확증: 매수 15건/일 → 예상 100~120 LLM콜/일 (현 4건)
- 경량 경로(triage 1~2콜) vs 레거시 3-analyst(3콜). SOFT_CACHE(호출 스킵)는 AUTO_SELL_REVIEW
  1곳에만. `LOSS_CAP_CACHE`는 토글만·코드 미구현(죽은 배관).

### 재설계 (품질 유지 경량화 3레버)
1. **INTRADAY_REVIEW/PRE_CLOSE_CARRY에 소프트캐시 확장** (~45콜 절감, 최대 수혜).
2. **TP_REVIEW·MAX_HOLD·PRE_SESSION을 triage 경량 경로로 이관** (3콜→1~2콜).
3. **rule 종결 게이트** — 명백한 큰이익/큰손실은 LLM 없이 종결. `LOSS_CAP_CACHE` 배선.
4. direct 문턱(SELL 0.85) 재조정 + challenge JSON 절단 버그 수정(`_ask_challenge:969`).

목표: 100~120콜 → 절반 이하, 품질 유지. 이 축은 **비용/성능 개선이라 상대적으로 안전** —
enforce 가능(품질 회귀 모니터링).

---

## 실행 순서 & enforce 판단 (위험별 차등)

| 축 | 항목 | 위험 | enforce 판단 |
|---|---|---|---|
| 3 | hold_advisor 경량화 | 낮음(비용개선) | **바로 enforce 가능** (품질 모니터링) |
| 1 | 매수 BUY_READY 복원 (US 강세 한정) | 중(추격 반증) | 국면 한정 + 선별유지로 enforce, 초기 소량 관측 |
| 1 | 눌림존 완화·호출희소성 | 중 | 국면별 조정, 실측 병행 |
| 2 | 하드가드 carry 경로 hold_advisor 복원 | 중 | 재앙방지선 유지 전제로 enforce |
| 2 | **loss_cap −2%→재앙방지선 완화** | **높음(cap완화 반증)** | **shadow 실측 필수 → 승인 후** |
| 2 | 시간컷(pre_close) 제거→멀티데이 | 중 | 손절 유지 전제 enforce |

**운영자 확인 필수 파라미터**(무단변경 금지, 승인 후 두 소스 동시): LOSS_CAP·MAX_SINGLE_LOSS·
CLAUDE_REVIEW_ALL_AUTOMATED_SELLS·PATHB exit policy·BUY_READY 프롬프트. 전부 승인 게이트.

## 결론

원 설계는 옳고, 무너진 곳은 **입구(BUY_READY 금지)와 관리권(하드가드 우회) 두 군데**다.
복원 = ①judge에 매수 판단권 복원(국면차등) ②하드가드를 재앙방지선으로 올리고 그 안은
hold_advisor에게 ③매수 폭증 대비 경량화. 축3·축1은 enforce 가능, **축2의 손절선 완화만
cap 반증과 맞닿아 shadow 필수.** "돈 버는 건 매수다"는 그 매수가 살아남아 관리받을 때 참이다.

미해결: 국면차등 즉시매수의 forward net·재앙방지선 폭의 최적값·경량화 후 품질 회귀 —
전부 첫 실거래 재개 후 실측으로 확정.
