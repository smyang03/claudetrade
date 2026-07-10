# Profit Evidence Gate 구현·운영 계약 (2026-07-10)

상태: 구현 완료. 기본 모드는 `shadow`; `enforce` 전환 가능.

검증 결과: [Profit Evidence 1·2단계 DB 검증](./validation_profit_evidence_replay_walkforward_20260710.md)

Trainer 개선: [Profit Trainer v2 경로 라벨 검증](./validation_profit_trainer_v2_path_20260710.md)

## 핵심 변화

기존 진입 질문은 `후보인가 / READY인가 / 신뢰도가 높은가`였다. 새 공통 신규매수 gate의 질문은 다음과 같다.

> 보정된 목표 선도달 확률, 실제 비용 p75, 비용 후 기대수익, 불확실성, OOD, drift, 독립 검증 하한이 모두 유효한가?

하나라도 충족하지 못하면 `shadow`에서는 `would_block`을 기록하고 주문은 허용한다. `enforce`에서는 `PROFIT_EVIDENCE_ABSTAIN`으로 신규매수를 차단한다. 기존 포지션의 손절·목표·청산은 차단하지 않는다.

## 공통 적용 경로

- Path A 일반 진입
- Path A micro probe
- US/KR sector play
- Path B `claude_price`

모든 경로가 최종 broker precheck 전에 동일한 신규매수 gate를 통과한다.

## 모드 설정

기본:

```text
PROFIT_EVIDENCE_GATE_MODE=off|shadow|enforce
```

더 구체적인 설정이 우선한다.

```text
PROFIT_EVIDENCE_GATE_MODE_US_PATH_B
PROFIT_EVIDENCE_GATE_MODE_PATH_B
PROFIT_EVIDENCE_GATE_MODE_US
PROFIT_EVIDENCE_GATE_MODE
```

경로 이름은 `PATH_A`, `PATH_B`; 시장은 `KR`, `US`다. 현재 `config/v2_start_config.json`의 기본값은 `shadow`다.
시장×경로 override 네 개는 빈 문자열이면 global mode를 상속하고, `shadow` 또는 `enforce`를 넣으면 해당 경로만 독립 전환된다.

## 증거 입력

우선순위:

1. 주문 후보/PricePlan의 `profit_evidence`
2. selection meta의 `profit_evidence_by_ticker`
3. `state/profit_evidence_{market}.json`

snapshot 예시:

```json
{
  "schema_version": "profit_evidence_v1",
  "model_version": "meta_us_claude_price_v1",
  "generated_at": "2026-07-10T02:30:00+00:00",
  "evidence_by_ticker": {
    "NVDA": {
      "model_state": "PROBE",
      "decision_ts": "2026-07-10T02:50:00+00:00",
      "p_target_before_stop_calibrated": 0.64,
      "expected_gross_pct": 1.20,
      "expected_cost_pct_p75": 0.55,
      "expected_net_pct": 0.60,
      "uncertainty": 0.18,
      "ood": false,
      "drift_state": "healthy",
      "validation_sample_n": 120,
      "validation_net_lcb_pct": 0.08,
      "calibration_ece": 0.06
    }
  }
}
```

## Enforce 통과 조건

- schema `profit_evidence_v1`
- model version 존재
- model state: `PROBE`, `STANDARD`, `PRESS`
- 기본 180분 이내의 point-in-time evidence
- 보정 확률 기본 0.55 이상
- 비용 후 기대수익 기본 +0.25% 이상
- 비용 p75 최소 KR 0.21%, US 0.50%
- `expected_net <= expected_gross - expected_cost + tolerance`
- uncertainty 기본 0.25 이하
- `ood=false`
- drift state `healthy|stable`
- 검증 표본 기본 60 이상
- 검증 순수익 95% 하한 양수
- calibration ECE 기본 0.10 이하

문턱은 시장·경로별 환경변수로 override할 수 있다.

## 기존 점수 권한 변경

현재 설정:

```text
CANDIDATE_PROMPT_POOL_REORDER_ENABLED=false
CANDIDATE_QUALITY_TRAINER_PROMPT_HINT_ENABLED=false
ENABLE_KR_CANDIDATE_QUALITY_PROMPT=true
CANDIDATE_QUALITY_COMPOSITE_SCORE_PROMPT_ENABLED=false
SUB_SCREENER_TRIAGE_SCORE_MODE=raw_order
TRADE_READY_PRIORITY_SORT_ENABLED=false
PATHB_READY_BOOST_MULT=1.0
```

trainer와 quality scorer는 삭제하지 않고 shadow 학습·비교에 남긴다. KR의 RS·turnover·flow 같은 원시 feature는 prompt에 유지하되 역분별된 종합 `q` 숫자만 숨긴다. 검증되지 않은 숫자는 Claude에게 보일 후보, sub-screener 추가 후보, READY slot, 주문금액을 바꾸지 못한다.

## 전환 전 검사

```powershell
python tools/profit_evidence_preflight.py
```

`enforce` 경로에 통과 evidence가 하나도 없으면 exit code 2를 반환한다. 이 경우 라이브에서도 해당 경로의 신규매수는 전부 `ABSTAIN`한다.

## 기존 DB replay

```powershell
python tools/profit_evidence_db_replay.py --mode shadow
python tools/profit_evidence_db_replay.py --mode enforce
```

도구는 `candidate_audit.db`를 변경하지 않고, 종목-일 최초 prompt 후보와 1일 forward outcome을 결합해 동일 gate 계약을 재생한다. 현재 과거 행에는 `profit_evidence`가 0건이므로 결과는 다음과 같다.

- shadow: KR 2,070건, US 2,430건 모두 허용하지만 전부 `would_block`
- enforce: KR/US 모두 허용 0건
- 기존 후보 1일 평균: KR -1.706%, US -0.420% — broker 실현 net이 아닌 candidate close-return proxy

정확히 재생 가능한 것은 저장된 계약의 허용/기권 판단이다. 과거에 저장되지 않은 보정확률을 사후 생성하여 “당시 예측”으로 취급할 수는 없다. `candidate_counterfactual_paths`에는 immediate/wait 30m/wait 60m의 entry·60m·close·MFE·MAE가 다수 존재해 새 모델의 walk-forward 연구에는 쓸 수 있지만, 모든 표본의 target/stop 선도달 순서와 broker 비용까지 완전 재현하지는 못한다.

## 운영 원칙

- `shadow` 결과로 충분한 forward net·calibration·coverage를 확보한다.
- 통과한 시장×전략만 `PROBE enforce`로 전환한다.
- drift 또는 validation LCB 실패 시 snapshot producer가 model state를 `SHADOW/BLOCK`으로 내려 자동 기권시킨다.
- Claude confidence는 calibrated probability를 대신할 수 없다.
- profit gate 오류 자체도 enforce 상태에서는 fail-closed다.
