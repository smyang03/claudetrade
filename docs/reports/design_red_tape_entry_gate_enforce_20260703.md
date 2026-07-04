# 설계: 반응형 red-tape 진입 게이트 enforce (US 전용)

작성 2026-07-03. 상태: **설계 스펙(미구현) — 운영자 승인 대기.** read-only 검증만 완료, 코드/config 무변경.

## 1. 결정 요약

진입 순간 지수가 **세션개장 대비 하락(red-tape)** 인 US Path B 신규 진입을 차단한다. 예측이 아니라 **사실 기반 방어**(무알파 벽 밖). 이전 DB로 낼 수 있는 모든 confound 검증을 통과했고, 유일 미지수는 국면 일반화(6월 편중)라 forward로만 확증 가능 → **점진 enforce**로 시작.

- **범위: US 전용.** KR은 shadow만(근거 §3).
- **임계: −0.3%부터 enforce**, −0.1~−0.3 밴드는 shadow(전환조건 = forward net).
- **가역**: 토글 off 즉시 복귀. 기존 도구로 관측 지속.

## 2. 증거 (이전 DB, US n=135, since 2026-05-04)

핵심: red-tape 진입 net −1.16 vs 비-red +0.26, **격차 +1.41%p**(n=53). 아래 stress-test 전부 통과:

| 각도 | 결과 | 판정 |
|---|---|---|
| 임계 민감도 | −0.1~−0.75 어디든 격차 +1.41~+1.70(깊을수록↑) | 아티팩트 아님 |
| 용량반응 | 개장대비 낙폭 깊을수록 net 단조악화, ≤−0.5 승률 0% | 기계적 인과 |
| 양날 | 빨강 차단 시 승자 +17.4pp 포기·패자 −78.7pp 회피 = 순 +61.3pp | 순이득 견고 |
| sharp_reversal 겹침 | −0.1서 53건 중 49건 고유(중복 4). 상보적 | 중복 아님 |
| **staleness(30분 캐시)** | stale 분류로도 격차 +1.43(일치율 80%) | **라이브 게이트 실현가능** |
| 시간대 confound | 모든 elapsed 버킷서 빨강 나쁨(late-gate로 설명 안 됨) | confound 아님 |
| QQQ 교차 | QQQ로도 격차 +1.18 | SPY 특정 아님 |
| 종목편중 | 빨강 53건 = 고유종목 36개 | idiosyncratic 아님 |
| 출구사유 | 빨강엔 PRICE_TARGET 거의 없고 스톱 다수 | 메커니즘 실재 |
| 보유일수 | 양쪽 중앙 2일, intraday 0 | 홀드 confound 아님 |

**유일 미지수 = 6월 표본 편중**(빨강 53건 중 50건 6월, 5월 빨강 n=3). 신호 결함이 아니라 표본 가용성일 가능성 큼(용량반응·staleness·상보성은 국면 무관 기계논리). forward만 확증 가능.

## 3. 왜 US 전용인가 (KR 각도, n 얇음)

KR CLOSED n=17(빨강 n=5, KOSPI ^KS11 기준): 격차 +1.43%p로 방향은 US와 동일. **그러나 KR red-tape net = +0.17(양수)**. US(−1.16 출혈)와 달리 KR은 "덜 좋은" 것뿐이고, KR은 net 흑자 시장이라 red-tape 차단 = **소폭 이익 포기**. 용량반응도 KR선 재현 안 됨(n=2~3 노이즈). → KR enforce 부적합. KR은 shadow로 forward 누적만.

## 4. 설계 상세

### 4-1. 라이브 소스 (이미 커밋 54b9720)
`_red_tape_at_entry_shadow`가 `_session_open_index_change`(세션 첫 튜닝샘플 1회 고정) 대비 현재 지수로 **세션개장→진입** 계산. 캐시 기반(API 무호출), staleness 통과(§2). 이 필드가 게이트 입력.

### 4-2. 차단 로직
`runtime/pathb_runtime.py::_submit_buy`에서 red-tape shadow 계산 직후:
- `mode = env(PATHB_RED_TAPE_GATE_MODE_US, "off")` ∈ {off, shadow, enforce}
- `thr = float(env(PATHB_RED_TAPE_GATE_THRESHOLD_US, "-0.3"))`
- `idx`(개장→진입) `< thr` AND `market == "US"` AND `mode == "enforce"` → 진입 차단(`_record_blocked(..., "RED_TAPE_ENTRY_GATE")`), decision_id 기록.
- `shadow` 모드 또는 `thr ≤ idx < -0.1` 밴드: 차단 안 하고 로그/plan_json 기록만(현행 shadow 유지).
- **sharp_reversal 가드는 손대지 않음**(상보적, 별도 유지).

### 4-3. 토글 (기본 무변경)
```
PATHB_RED_TAPE_GATE_MODE_US   = off      # 승인 후 enforce
PATHB_RED_TAPE_GATE_MODE_KR   = shadow   # KR은 관측만(enforce 금지)
PATHB_RED_TAPE_GATE_THRESHOLD_US = -0.3
PATHB_RED_TAPE_SHADOW = true             # 현행(밴드/KR 관측)
```
`.env.live` + `config/v2_start_config.json` 양쪽 일치 필요(운영자 파라미터 규약).

### 4-4. 전환조건 (점진)
- **활성화**: 운영자 승인 시 `MODE_US=enforce, THRESHOLD=-0.3`.
- **넓히기(−0.3→−0.1)**: forward US에서 −0.1~−0.3 밴드 red-tape net이 지속 음성(비-red 대비 격차 유지, n≥20) 확인 후.
- **되돌리기**: forward red-tape net이 양전(격차 소멸)하면 → `MODE_US=shadow` 복귀(국면 반전 신호).

### 4-5. Blast radius / 주의
- −0.3 enforce = in-sample US 진입의 ~17% 차단(stale 기준 더 적음). 고유 bleeder ~19건(net ~−1.5).
- **매수 얇아짐 주의**: US는 이미 reward_risk로 fill ~12%. red-tape가 추가로 red-tape 날 진입 차단 → 총 US 매매량 추가 감소. 단 자르는 건 bleeder(net 음성)이고 US는 net 적자 시장이라 노출 축소가 손해 아님. **총 US 진입량 0 근접 여부는 재시작 후 모니터링**(매도 엔진 굶김 방지).

### 4-6. 가드레일 (enforce해도 계속 측정)
- −0.1~−0.3 밴드 + KR + QQQ 기준은 shadow 유지 → 안 자르는 것도 forward 관측.
- 판독 도구 `tools/reactive_tape_gate_review.py`(SPY 소급) 세션마다 재실행 → enforce 후에도 격차 추적.

## 5. 구현 상태 (2026-07-04 완료 — 기본 off, 매매 무변경)

**코드 구현됨** (`runtime/pathb_runtime.py`):
- `_entry_tape_idx(plan)` — 세션개장→진입 idx 계산(캐시), shadow·게이트 공통 입력.
- `_red_tape_at_entry_shadow(plan)` — idx 반환하도록 리팩터(기록은 PATHB_RED_TAPE_SHADOW on일 때만, 기존 동작 보존).
- `_red_tape_entry_gate_block(market, idx)` + `_red_tape_gate_threshold(market)` — enforce 판정(기본 off).
- `_submit_buy` — sharp_reversal 블록 직후 게이트 삽입, `RED_TAPE_ENTRY_GATE` reason 기록.
- 테스트 `tests/test_red_tape_entry_gate.py` 10케이스(off/enforce/band/green/none/KR-scope/threshold-override/idx계산·폴백·빈history).

**기본값 off = 매매 무변경.** `PATHB_RED_TAPE_GATE_MODE_{market}` 미설정 시 getenv 기본 "off"라 차단 안 함. shadow 기록은 기존 `PATHB_RED_TAPE_SHADOW`(기본 true)로 US·KR 둘 다 계속.

### ★활성화됨 (2026-07-04 운영자 승인 "인포스로 가자")
`.env.live` + `config/v2_start_config.json` 양쪽에 `MODE_US=enforce`, `THRESHOLD_US=-0.3` 추가(디스크만, 미커밋 — repo 관례). 게이트 시뮬 검증: US idx −0.31→차단, −0.3→통과(경계 `< thr`), KR −1.5→미차단(off). **다음 재시작부터 발동.** 현재 봇(22:03 시작)은 미반영, US 장중(00:56, 05:00 마감)이라 **장중 재시작 비권장 → 마감 후/다음 세션 전 재시작.**

### 운영자 활성화 절차 (참고)
`.env.live` + `config/v2_start_config.json`(env_overrides) **양쪽**에 추가:
```
PATHB_RED_TAPE_GATE_MODE_US = enforce
PATHB_RED_TAPE_GATE_THRESHOLD_US = -0.3
# KR은 추가하지 않음(미설정=off=미차단, net 흑자시장 보호)
```
→ 다음 재시작부터 US red-tape(개장대비 <−0.3%) 진입 차단. revert = MODE_US 삭제 또는 off.

### 미구현 (선택, 후속)
- 시간조건 refine(0~60분 red-tape 격차 작음 −0.42 vs +0.25) — `RED_TAPE_GATE_MIN_ELAPSED_MIN` 후보.

**config/.env는 건드리지 않았다(기본 off). enforce 활성화는 운영자가 위 절차로.**
