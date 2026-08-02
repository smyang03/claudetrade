# 전체 개편 상세 리포트 — 급락 반등 레인 중심 재구성 (2026-08-01 ~ 08-02)

작성 2026-08-02. 대상 기간: 2026-08-01 00시 ~ 08-02 새벽. 커밋 5건(`678604c`→`3349956`), 봇 재기동 5회, 전 변경 effective-config 실측 검증.

---

## 0. 한눈에 보기

**Before (8/1 아침까지)**: Claude selection → 모멘텀/눌림 후보 → judge 판정 → PathA/PathB 주문. 조기익절 3종이 계획 2일 플랜을 1~2시간 만에 잘라냄. 통산 US −29.5만·KR +4.1만(실현), 7월 거래 9건.

**After (현재)**: 매수는 **급락 반등 레인 하나** — day_losers 랭킹 → 알파모델 top5 → micro 1슬롯 실주문(≤30만) → TP+12/SL−25/최대 5일. 그 외 모든 매수 3중 차단. Claude는 실시간 판단에서 제외. 데이터 축적은 전부 유지, API 비용 ~75% 절감.

**전환 근거(실측)**: 원시 급락 스크린은 US/KR 모두 전 조합 음수인데, **알파 모델이 급락주 안에서 고른 것만 유의한 선별력**(7월 17건 +6.94% vs 모집단 −1.48%, 무작위 2만회 중 0회 재현 p=0.0000, AXTI 제외에도 p=0.0054). 기존 Claude selection은 같은 조건에서 실패/역선별(KR rank1-3 **−4.65%**, 전략신호 p=0.99 역선별).

---

## 1. 무엇을 변경했나 (시간순 상세)

### 1-1. 조기익절 3종 해제 (8/1 오전, 운영자 승인 "KR+US 동시 enforce")
**문제**: Claude가 2일 보유·목표 +4.47%·손절 −2.10% 스윙 플랜을 짜는데 실제 보유가 중앙값 US 1.98h/KR 0.65h — `PATHB_EARLY_TIER_TARGET_FRACTION=0.4`(목표의 40%에서 익절)·30분 본전컷·weak MFE컷이 원인. 목표 도달이 318건 중 27건(8.5%)뿐.

| 키 | 변경 | 근거 실측 |
|---|---|---|
| `PATHB_EARLY_TIER_TARGET_FRACTION_KR/_US` | 신설 =1.0 (조기익절 사실상 해제) | 목표배수 단조: KR x0.4 −36.4 → x1.0 +13.5 (시간봉 반사실) |
| `EARLY_PATH_BREAKEVEN_MODE` | enforce → shadow | 30분 본전컷이 러너 차단 |
| `US/KR_PATHB_WEAK_MFE_CUT_ENABLED` | true → false (SHADOW=true 유지) | 30분·MFE 0.5% 컷 동일 문제 |

### 1-2. 보유일수 하한 + 진입 게이트 (8/1 오후, "모두 진행")
- **코드** `runtime/pathb_runtime.py` — `_resolve_max_hold()` 신설: `max_hold=plan.hold_days` 하드코딩 2곳 교체. `PATHB_MIN_HOLD_DAYS_US/KR=5` (0=기존동작). 근거: D1~3은 어떤 필터로도 흑자 불가, D5~7만 흑자(US n=237).
- **코드** `decision/claude_price_plan.py` — `_min_reward_pct()` + `reward_pct_below_minimum` 검증 신설. `PATHB_MIN_REWARD_PCT_US=3.0` (상한은 기존 CAP=6 → 3~6% 밴드). 버킷 실측: <3% −33.2 / 3-4.5% +56.4 / 4.5-6% +112.1 / ≥6% −57.1.
- `PATHB_MIN_REWARD_RISK_US` 1.2 → 2.0. RR 문턱 실측: 1.2 −27.7 → 2.0 +70.2 (그 이상 하락).
- ※ 이후 1-5의 전면 차단으로 PathB 자체가 꺼졌지만, 재활성 시 검증된 기본값으로 작동하도록 커밋에 보존.

### 1-3. us_swing 레인 확정 (8/1 오후~저녁)
**발견 과정**: 7/31 US 수익 +12.1만의 87%가 AXTI(+37.8%) 한 건 → "운빨" 판정 → 운영자 반박("운빨을 시스템으로") → 재검증에서 **shadow 원장 −239%p는 TP/SL 없는 고정보유 값**(실거래와 다른 전략)임을 확인 → 실거래 규칙 적용 시 소스별 부호 분리:

| candidate_source | n | 실거래규칙 적용 합계 |
|---|---|---|
| **day_losers** | 17 | **+117.9** (승률 64.7%, 갭TP 5건 전부 여기) |
| day_gainers | 28 | −87.4 |
| most_actives | 20 | −117.4 |

- **코드** `tools/us_swing_shadow_runner.py` — `US_SWING_ALLOWED_SOURCES` 화이트리스트 신설(빈값=전 소스). 설정 `=day_losers`.
- TP/SL: 0.12/0.25 → **0.10/0.20 적용 → 원복(0.12/0.25)**. 원복 사유: 0.10/0.20 최적값은 익일(t+2) 진입 규약의 시뮬이었고 라이브는 신호당일 진입 — 당일 규약 실측에서 TP10/SL20 = −0.19/건, TP12/SL25 = +3.14/건. **교훈: 파라미터는 라이브와 같은 진입 규약으로 캘리브레이션.**

### 1-4. 대규모 검증 (8/1 저녁, "적용 말고 검증")
- **US 유니버스 백테스트** 822종목×6개월(13,818 이벤트): 원시 day_losers 전 조합 음수(−0.38~−0.01) → 엣지는 소스가 아니라 **모델 선별력** 확정(p=0.0000, 상세 §0).
- **판별 피처**: 학습(2~4월)/검증(5~7월) 분리로 US 5조건(장중투매형, 검증 n=980 **+1.152/건**) / KR 8조건(갭 과잉반응형, n=141 **+3.347/건**) 도출. **갭/장중 방향이 두 시장에서 정반대.** 단, 피처 선정에 검증기간 참조(경미한 누출) → shadow forward가 최종 판정.
- **매도 검증**(n=980): TP+12 스윗스팟, SL 넓을수록 단조(−25 적정), **D2~3 음수·D5 최적**. 현행 유지 확정.
- **KR 알파모델 walk-forward**(34,203행, 80일): 리프트 없음(p=0.70) → **KR 실주문 금지, shadow만**.
- **Claude 저점선정 능력**(selection 6,747건 실측): 급락 상태 Claude 픽 KR −4.65%/US +0.89%, 전략신호 역선별 → **Claude는 선별에서 제외, veto 역할만**.

### 1-5. 전체 재구성 — 기존 매수 차단 (8/1 밤, "즉시 차단·코어 유지·예산 집중")
- **코드** `execution/safety_gate.py` — `LEGACY_NEW_BUY_DISABLED` 마스터 스위치 신설(기본 false). 표준 신규매수 전 경로 차단, 사유코드 `LEGACY_BUY_DISABLED` 등록(`config/v2.py`).
- 3중 방벽: ① 위 스위치=true ② `PATHB_KR/US_LIVE_ENABLED=false` ③ `PROFIT_STRATEGY_ENABLED_IDS=` 비움(코어 신규만 차단, 보유 SCHG·275280·275300 유지).
- **매도·보유관리 무영향**(매도는 SafetyGate 미경유, grep 검증), **us_swing 무영향**(micro probe는 place_order 직행, 함수 본문 검증).

### 1-6. 월경계 재시작 결함 수정 (8/1)
**사고**: 8/1 01:37 재시작이 코어 매니페스트 검증 실패(월경계 mismatch + 신규자산 미허용)로 8개 프로세스 정지 후 기동 중단 — 봇 4분 정지, watchdog도 같은 지점 반복 실패.
**수정** `tools/start_live_stack_headless.ps1`: 매니페스트가 KR/US 모두 존재·15분 내 신선·`NO_LIVE_AUTHORITY`이면 기동 계속(코어는 이미 안전 차단 상태이므로). 권한이 살아있는데 갱신 실패면 여전히 throw. ※ 실행 검증은 다음 전체 재시작 때.

### 1-7. 관측 인프라 신설 (8/1 밤 ~ 8/2)
- `tools/us_swing_shadow_runner.py`에 **하드필터 5조건 shadow** 기록 추가 → `data/shadow/us_hard_filter_shadow.jsonl` (US 세션마다 자동, 주문 무영향).
- **신설** `tools/kr_fallen_shadow_scan.py` — KR 8조건 일일 스캔 + `--settle` D5 자동 정산. 첫 구현이 pykrx 무타임아웃 행으로 5시간 미완 → **캐시 기반 재설계**(네트워크는 `--update-cache` 한 단계로 격리). 7/31 실검증 완료.
- **신설** `tools/audit_pnl_consistency.py` — 원장 pnl 정합 감사(읽기 전용). 판명: 원장 pnl_pct = **KRW 기준 net**(USD수익+환율−수수료), 검증 가능 138건 중 불일치 3건 = 원장은 정확, 문제는 결손(청산가 16%·fx 41%).
- 원장 동기화 복구: `sync_v2_learning_performance` 21건 반영(백업 후).

### 1-8. API 비용 최적화 (8/2, "데이터 축적 유지·API 축소")
호출을 끄지 않고 **모델만 하향** — 빈도·스키마·케이던스 동일(축적 그대로):
- `SINGLE_SYMBOL_JUDGE_MODEL`: **opus-5 → haiku-4.5** (최대 비용원 — 차단된 진입판정에 opus 사용 중이었음, 3일 76만 in 토큰)
- `R1/BULL/BEAR/NEUTRAL_R1_MODEL`: sonnet-5 → haiku-4.5 (국면 라벨 유지)
- tuner·hold_advisor·매도리뷰는 sonnet 유지. 추정 일 ~$8 → ~$2.
- ⚠️ 8/2 전후 judge/consensus 라벨 분포 변화 — 시계열 비교 시 감안.

---

## 2. 어떻게 반영했나 (적용·검증 절차)

모든 변경은 동일 규율로 반영:
1. **두 소스 동시**: `.env.live` + `config/v2_start_config.json` env_overrides (한쪽만 바꾸면 미반영)
2. **코드 변경 시**: py_compile → 단위 행동테스트(케이스별) → pytest (safety 60 / swing 42 / pathb 622 / 통합 710 통과) → mojibake `--staged`
3. **봇 graceful 재기동** 후 `psutil.Process(pid).environ()`으로 **effective-config 실측** — 로그가 아니라 프로세스 환경을 직접 읽어 반영 확정
4. 스택 8종 단일 인스턴스 확인 + manifest 갱신
5. **커밋**(동작 단위 5건, `.env.live`·data/·런타임 상태 제외):

| 커밋 | 내용 |
|---|---|
| `678604c` | 차단 마스터 스위치 + 월경계 재시작 수정 + start-config |
| `98ae604` | PathB 보유하한·목표거리 게이트 |
| `e9add64` | swing day_losers 전용 + 하드필터 shadow |
| `8b89fc6` | KR 스캐너·pnl 감사기·설계문서 3건 |
| `3349956` | API 모델 하향 |

**차단의 행동 검증**(설정 확인이 아니라 코드 실행): SafetyGate/PathBSafetyGate 매수 평가 → `LEGACY_BUY_DISABLED` 차단, PathB `_market_live_gate_detail` → live=False → 진입 스캔 early return, 코어 materializer → signals 0, 매도 경로 SafetyGate 미경유 확인.

**us_swing 주문 가능 확정**: 원시 authority는 shadow지만 라이브는 `us_swing_order_bridge` 94~118행의 **운영자 micro override**(블로커가 forward 부족 4종일 때 micro 부활, 7/27 AXTI와 동일 경로)로 주문 가능. ※ override 모드 슬롯은 코드에 1/1 하드코딩 — 슬롯 확대는 bridge 2줄 수정 필요.

---

## 3. 어떻게 운영되나 (현재 확정 운영 흐름)

```
[US 세션 — 매일 자동]
 22:20 KST  preopen_scheduler → us_swing_runner (개장 10분 전)
            ├ KIS 랭킹에서 day_losers만 수집 (ALLOWED_SOURCES)
            ├ 알파모델(27피처 GBM×3시드)이 top5 랭킹
            └ 하드필터 5조건 통과 여부 shadow 기록 (자동)
 22:30~     봇 루프 → us_swing handoff → bridge micro override
            └ 게이트: 진입창 5~30분·갭/추격 캡·브로커신뢰·확률 허들
            └ 통과 시 실주문: 일 1건 · 1슬롯 · ≤30만원
 보유 중     TP +12% / SL −25% / 최대 5거래일 (규칙 자동, Claude 개입 없음)
            갭이 TP 넘으면 시가 체결 (최대 수익원 — AXTI 패턴)

[KR 세션 — shadow 전용 (실주문 없음)]
 마감 후     kr_fallen_shadow_scan --update-cache (~10분) → --date 스캔 (즉시)
            8조건 후보를 shadow 원장에 기록, --settle로 D5 결과 자동 정산
            → 6주 후 forward 평균>0이면 KR micro 논의

[차단 상태 — 상시]
 PathA/PathB/전략신호/코어 신규매수 = LEGACY_BUY_DISABLED (3중 방벽)
 보유 코어 3종목(SCHG·275280·275300)은 유지, 매도관리 정상
 judge/analysts는 haiku로 shadow 데이터만 생산 (관측 지속)

[승격 사다리]
 micro(현행 1슬롯) → forward sessions 15/matured 60/mean≥0.25/PF≥1.2
 → probe(3슬롯/일3건) 자동 승격 → standard(5슬롯)
 ※ day_losers 전용 forward가 이제부터 쌓임 (신호 일 ~1건 페이스)
```

**일상 감시 포인트**: ① `LEGACY_BUY_DISABLED` 차단 로그(차단 작동 증거) ② handoff 로그(BLOCKED 사유 추이) ③ swing 포지션 TP/SL 동작 ④ shadow 원장 증분.

---

## 4. 롤백 맵

| 변경 | 롤백 |
|---|---|
| 전면 차단 | `LEGACY_NEW_BUY_DISABLED=false`, `PATHB_*_LIVE_ENABLED=true`, `PROFIT_STRATEGY_ENABLED_IDS` 복원 → 재시작 |
| 조기익절 해제 | FRACTION 0.4 / BREAKEVEN enforce / WEAK_MFE true |
| 보유하한·게이트 | `PATHB_MIN_HOLD_DAYS_*=0`, `PATHB_MIN_REWARD_PCT_US=0`, RR 1.2 |
| swing 소스 제한 | `US_SWING_ALLOWED_SOURCES` 줄 삭제 |
| API 모델 | opus-5/sonnet-5 복원 |
| 관측 도구 | shadow 전용이라 롤백 불요(파일 삭제로 충분) |

## 5. 미해결·운영자 결정 항목

1. **(결정) us_swing 슬롯 1→3**: bridge 코드 2줄 수정 필요. 확대 시 계좌 일일손실 HALT 체크를 handoff에 추가하는 작업 선행(현재 swing은 HALT 우회 — 1슬롯에선 최대 노출 −7.5만이라 허용).
2. (결정) KR 스캔 자동화: 현재 수동, 스케줄러 등록은 코드 1건.
3. (결정) 잔여 PathB 설정 대청소 시점.
4. (검증 대기) 월경계 재시작 수정의 실행 검증 — 다음 전체 재시작 때.
5. (관측 대기) US 하드필터·KR 8조건 shadow forward — 각 4주/6주 후 판정. 두 규칙 모두 피처 선정 누출이 있어 **shadow가 최종 판정**이다.
6. (구조 과제) CLOSED 이벤트에 exit_price·fx 기록 — 결손(청산가 16%·fx 41%)이 계속 쌓이는 것을 막는 근본 수정.

## 6. 이번 개편에서 뒤집힌 내 판정 (기록)

1. "us_swing 무엣지(−239%p)" → shadow가 TP/SL 없는 다른 전략이었음. **shadow 청산규칙이 실거래와 같은지 먼저 확인.**
2. "AXTI는 운" → 모델 선별력 p=0.0000. **하루 수익이 아니라 forward 분포로 판정하되, 하위그룹(소스)로 쪼개서 볼 것.**
3. "멀티데이 pnl 오류" → 원장은 KRW 기준 net, 내 재계산은 USD gross — 정의 차이(fx 상관 −0.99).
4. TP10/SL20 적용 → 진입 규약 불일치 캘리브레이션 오류, 당일 원복.
5. "미실현 −91.6만" → 원장 stale 30건, 실제는 −1.4만. **브로커 스냅샷이 truth.**

---
*설계 문서: design_fallen_rebound_lane_20260801.md · design_candidate_selection_and_exit_20260801.md · monday_readiness_20260801.md*
