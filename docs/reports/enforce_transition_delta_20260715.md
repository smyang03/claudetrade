# 수익전략 enforce 전환 차이 보고서 — 2026-07-15

## 0. 결론

최종 목적은 shadow 유지가 아니라 검증을 통과한 정책의 운영자 승인 enforce다. 다만 신규 정책을 곧바로
주문 경로에 삽입하지 않는다. 현재 라이브를 기준군으로 보존한 채 동일 가격 경로에서 paired shadow로
실행 가능 성과를 측정하고, 사전등록 게이트를 통과한 정책만 시장별 단일 플래그로 전환한다.

이번 변경으로 달라지는 핵심은 다음 네 가지다.

1. KR PathB 출구가 `early-tier 전량청산` 단일 정책에서 `현재 정책 A vs Split-Runner B` 경쟁 구조가 된다.
2. B의 관측기는 라이브 분봉 캐시의 read-only 소비자이며 라이브 출구 판단에 어떤 분기도 추가하지 않는다.
3. 저회전 코어는 중복 트래커를 없애고 시장별 하나의 통합 트래커·원장·heartbeat로 운영한다.
4. `/monitor`가 수익·프로세스 생존뿐 아니라 전략 데이터의 최종 갱신 시각과 stale 상태를 검사한다.

## 1. 기존과 변경 후 비교

| 영역 | 기존 | 변경 후 enforce 목표 | 수익성 영향 |
|---|---|---|---|
| KR PathB 출구 | 목표거리 40%에서 early-tier 무장, 고점 대비 0.6% 반납 시 전량청산 | A는 현 정책 유지, B는 +3.6%에서 정수수량 50% 부분익절 후 잔량 러너 | A의 조기보호와 B의 꼬리수익을 같은 포지션에서 직접 비교 |
| +3.6% 이전 소유권 | early/tier1~3가 전량청산 가능 | B에서는 split 대기 상태가 profit-side 소유. hard stop·loss cap 등 손실보호만 유지 | B는 +1.6~3.6% 구간 반납 위험이 커지지만 큰 러너 보존 가능 |
| +3.6% 이후 소유권 | 단일 ladder가 전량 관리 | B가 50% 가상체결, 잔량은 runner ladder·target·pre-close·기존 손절에 인계 | 이익 일부 확정과 무제한 잔량을 분리 |
| 검증 데이터 | 역사적 종가·기록 MFE 반사실 | 실시간 분봉 순서, 정수수량, 가상 체결, 비용·슬리피지 반영 | `+38.84%p` 낙관 상한을 실행 가능한 net으로 교체 |
| 코어 전략 | tsmom 사망, index trend 1회성, SCHG/BIL 연구, GQMT 봉인 등 중복 | 현재 자본은 US SCHG/BIL·KR 팩터만 통합 tracker에서 primary shadow | PathB 가뭄 때 자본의 시장수익 참여 가능 |
| 트래커 감시 | 봇 중심 생존 검사, 전략 트래커 stale 미검출 | heartbeat와 `/monitor`에 최종 성공·데이터 기준일·다음 예정·stale 사유 표시 | 직접 알파가 아니라 조용한 전략 정지로 인한 수익 기회 0화를 방지 |
| 승격 | 연구 판정과 라이브 적용 사이 표준 전환 계약 부족 | SHADOW→PROBE→MICRO→ENFORCE, 단계마다 운영자 승인 | 잘못된 자동승격·동시정책 충돌 방지 |

## 2. KR paired A/B의 정확한 정책 계약

### Arm A — 현재 라이브 기준군

- 현재 `PATHB_EARLY_TIER_ENABLED=true` 정책을 그대로 복제한다.
- 계획 목표거리의 40%에서 early-tier를 무장한다.
- peak 대비 0.6% 반납 시 `CLOSED_PROFIT_LADDER` 전량청산을 기록한다.
- MFE 3% 이상에서는 현재 B 수정대로 tier3/tier4의 느슨한 peak trail이 우선한다.
- 현재 라이브 주문과 실제 손익은 계속 A가 소유한다.

### Arm B — Split-Runner challenger

상태는 `PRE_SPLIT → SPLIT_FILLED → RUNNER → CLOSED`로 고정한다.

1. `PRE_SPLIT`
   - +3.6% 도달 전 early-tier 및 profit-side tier1~3는 관찰만 하고 전량청산 권한을 갖지 않는다.
   - hard stop, loss cap, 무효화, 브로커 안전청산은 그대로 유지한다.
   - 이 구간의 이익 반납은 B가 감수하는 명시적 trade-off다.
2. `SPLIT_FILLED`
   - 최초 +3.6% 도달 시 `floor(original_qty * 0.50)`를 한 번만 부분청산한다.
   - 1주 포지션처럼 부분체결 수량이 0이면 `A_FALLBACK_QTY1`로 귀속해 Arm A 정책을 그대로 적용한다.
   - 이 행은 `NOT_INTEGER_EXECUTABLE`로도 표시하고 B 실행 가능 paired 표본에는 포함하지 않는다.
3. `RUNNER`
   - 잔량만 tier3/tier4, 원 목표, pre-close 및 기존 손실보호에 인계한다.
   - early-target은 잔량의 전량청산 소유자로 되돌아오지 않는다.
4. `CLOSED`
   - 부분익절과 잔량청산을 합산한 비용 후 net, capture, 보유시간을 확정한다.

각 상태 전환과 가상 체결 행은 다음을 반드시 기록한다.

- `position_id`, `path_run_id`, market, ticker, qty
- `arm=A|B`, `policy_version`, `exit_owner`
- source event timestamp, source cache watermark, 관찰 가격
- trigger, requested qty, executable qty, virtual fill price
- fee, slippage, realized net, remaining qty
- MFE/MAE, close reason, data-quality/freshness flag

역사 원장의 정확한 분모는 다음과 같다.

- 전체 KR 51건 중 1주 포지션은 4건(7.8%)이며 모두 +3.6%에 도달하지 않았다.
- +3.6% 도달은 34건(66.7%)이고 34건 모두 정수 부분청산이 가능했다.
- 나머지 17건은 `1주라서 분할 불가`가 아니라 `트리거 미도달`이다.

따라서 역사 반사실의 효과를 다시 2/3로 곱해 할인하지 않는다. 기존 51건 합계는 미도달 17건을 이미
변경 없음으로 포함한 전체 코호트 수치다. 더구나 forward B는 트리거 미도달 거래에서도 A의 조기청산을
보류하므로 A/B 손익이 달라질 수 있다. 판정기는 항상 `전체 신규 KR 포지션`, `qty>=2 분할 가능 포지션`,
`+3.6% 도달·가상체결 포지션` 세 분모를 따로 보고한다.

## 3. Arm B 가상체결 데이터 접근 계약

가상체결 관측기는 `runtime/intraday_minute_cache`의 read-only 소비자다.

- 허용: 확정된 분봉 또는 immutable snapshot 읽기, 관측기 전용 state/ledger 쓰기
- 금지: 캐시 수정, 라이브 plan/position 객체 수정, 주문 API 호출, live config 변경
- 금지: 라이브 exit 함수에 A/B 조건문·콜백·예외경로 추가
- 금지: observer 실패를 라이브 HOLD/SELL 결정으로 전달
- 장애 격리: observer 예외는 shadow heartbeat에만 기록하고 라이브 봇 판단 결과를 바꾸지 않는다.
- 재현성: 읽은 cache watermark와 bar timestamp를 모든 행에 저장한다.

구현 경계는 라이브 출구 함수 내부가 아니라 이미 생성된 가격 이벤트를 별도 subscriber가 소비하는 구조다.
paired shadow 기간 동안 A와 B는 같은 event sequence를 받아야 하며 한 arm의 오류가 다른 arm의 상태를
변경해서는 안 된다.

## 4. historical ceiling에서 enforce 근거로 바뀌는 점

기존 `actual -11.10%p → counterfactual +38.84%p`는 기록 MFE에 도달하면 주문도 체결됐다고 보는
낙관 상한이다. 변경 후 의사결정에는 이 숫자를 기대수익으로 사용하지 않는다.

Enforce 판단 원장은 다음 차이를 반영한다.

- 분봉 도달 순서와 cache freshness
- 정수 부분수량과 미체결
- 지정가/보수적 가상체결 가격
- 추가 슬리피지와 수수료
- A와 B의 동일 포지션 paired net delta
- 상위 소수 거래 제거와 시기별 블록 성과

모든 문서에서 역사 수치는 `historical MFE-based optimistic ceiling`로 표기한다.

## 5. enforce 게이트와 전환 방법

Arm B는 다음 조건을 모두 만족할 때만 운영자 승인 대상으로 올라간다.

1. 실행 가능한 paired 표본 `n>=15`
2. 비용·슬리피지 후 `mean(B_net - A_net) > 0`
3. 거래일/주 단위 paired block 5% 하한 > 0
4. 상위 기여 3건 제거 후 누적 delta > 0
5. B 정수수량 실행 가능률과 가상체결 완결률 사전 기준 충족
6. 캐시 stale·observer gap 표본을 제외해도 부호 유지
7. hard stop/loss cap 등 기존 안전경로 회귀 테스트 통과
8. 운영자 명시 승인

### 5.1 paired 판정 시계와 스루풋

`n>=15`는 달력 시간이 아니라 신규 KR 체결에 의해 움직이는 event clock이다. 2026-07 역사 원장에는 KR
표본이 1건뿐이므로 플랜·체결 생산 복구가 되지 않으면 시험은 완료되지 않는다. `/monitor`와 판정 리포트에
다음을 함께 표시한다.

- 최근 7일 신규 KR PathB 포지션 수
- 최근 7일 `qty>=2` paired eligible 수
- 최근 7일 +3.6% trigger 및 B 가상체결 수
- 누적 전체/eligible/trigger 표본 수
- 최근 표본 발생시각, 4주 이동평균 신규 n/주
- 현재 페이스 기준 `n=15` 예상 도달일 또는 `ETA_UNAVAILABLE`
- `RUNNING | STARVED | COMPLETE` 판정 시계 상태와 starvation reason

두 개의 연속 KR 거래주 동안 신규 paired eligible이 0이면 `STARVED`로 경고하고 플랜 생산·진입 퍼널
재분해를 P0로 올린다. 이는 표본을 만들기 위해 게이트를 자동 완화한다는 뜻이 아니다. 후보→judge→plan→
entry-window→order→fill의 기존 의도 안에서 파이프라인 누락·과도한 비의도 차단을 찾는 작업이다.

### 5.2 설정 이중 소스와 enforce/rollback 절차

live 봇은 `.env.live`를 먼저 읽은 뒤 `config/v2_start_config.json`의 `env_overrides`로 같은 키를 덮어쓴다.
따라서 `PATHB_KR_EXIT_POLICY`는 두 파일에 같은 값이 있어야 한다. config 값이 실제 우선값이라는 사실에
기대 한쪽만 바꾸는 절차는 금지한다.

Enforce와 rollback은 모두 다음 순서를 원자적 운영 절차로 사용한다.

1. `.env.live`와 `config/v2_start_config.json.env_overrides`의 값을 같은 변경 단위로 수정한다.
2. 두 소스 값의 완전일치, JSON 유효성, dotenv 중복키 부재를 검사한다.
3. `PATHB_KR_EXIT_POLICY`를 live preflight의 중요 runtime drift key와 effective-config 출력 대상에 포함한다.
4. 봇 스택을 재시작하고 새 PID를 확인한다.
5. 새 PID 시작시각 이후 생성된 runtime effective-config snapshot에서 기대값을 실측한다.
6. preflight의 `config.runtime_snapshot_drift` PASS와 PathB 회귀 smoke를 확인한다.
7. `/monitor`에 파일 두 소스, effective 값, 현재 PID의 policy version을 함께 표시한다.

파일 변경만으로는 전환 완료로 보지 않는다. `두 소스 일치 + 재시작 + 새 PID snapshot 실측`이 모두 끝나야
enforce 또는 rollback이 완료된 것이다.

정책은 포지션 진입 시 `exit_policy_version`으로 고정해 원장에 저장한다. 일반 전환·롤백은 신규 포지션부터
적용하며, 보유 중 포지션의 출구 소유권을 조용히 바꾸지 않는다. 보유 중 정책 강제변경은 별도 비상 절차와
감사 이벤트를 요구한다.

전환 시 전역 early-tier 플래그를 임의로 끄지 않는다. 시장별 단일 정책 선택값을 둔다.

```text
PATHB_KR_EXIT_POLICY=EARLY_FULL_V1      # 현재/즉시 롤백값
PATHB_KR_EXIT_POLICY=SPLIT_RUNNER_V1    # 승인 후 KR만 enforce
```

전환 후에도 paired observer는 일정 기간 유지해 실제 정책과 counterfactual A의 성과를 비교한다. 안전 이벤트,
원장 불일치 또는 체결 상태 불일치가 발생하면 단일 플래그로 `EARLY_FULL_V1`에 롤백한다.

## 6. 통합 코어 트래커 및 사망 감지

현재 목적이 겹치는 독립 작업을 하나의 `core_shadow_tracker`로 합친다.

- US primary: `US_SCHG_BIL_TREND_V1`
- KR primary: 저회전 팩터 코어
- benchmark arm: 기존 index-trend 규칙
- archived/deprecated: `tsmom_sleeve`
- sealed future replacement: `GQMT_CORE_V1`

월간 신호 생성과 일일 MTM을 분리하고 heartbeat에는 다음을 기록한다.

- `last_started_at`, `last_success_at`, `last_error_at`, `last_error`
- `signal_month`, `effective_month`, `price_data_as_of`
- `last_mtm_at`, `next_expected_at`, `stale`, `stale_reason`
- arm별 ledger 마지막 행과 SHA/manifest

`/monitor`에는 최소 다음 행을 항상 표시한다.

| 검사 | 정상 기준 |
|---|---|
| 코어 프로세스 최종 성공 | 예약 실행 이후 grace 안에 success |
| 코어 가격 기준일 | 해당 시장 최신 완료 세션과 일치 |
| 월간 신호 | effective month와 현재 월 일치 |
| 일일 MTM | 최신 완료 세션 이후 갱신 |
| US swing materializer | 일일 예정시각 이후 갱신 |
| paired exit observer | KR 포지션 보유 중 event watermark가 stale 아님 |
| paired 표본 페이스 | 최근 7일 신규/eligible/trigger n과 4주 이동평균 표시 |
| paired 판정 시계 | `RUNNING/STARVED/COMPLETE`, n=15 ETA 및 starvation reason 표시 |
| KR exit policy 설정 | `.env.live`·start config·runtime snapshot 세 값 일치 |

stale 판단은 단순 경과시간이 아니라 시장 달력과 `next_expected_at` 기준으로 한다. 이상은 live 주문을 자동
변경하지 않고 경고와 승격 차단으로만 작동한다.

## 7. 수익 구조가 실제로 달라지는 부분

### 직접 수익 레버

- Arm A는 작은 봉우리의 이익을 빨리 잠그지만 큰 러너를 일찍 끝낼 수 있다.
- Arm B는 +3.6%까지 기다리는 동안 이익을 반납할 수 있지만, 도달 시 절반을 확정하고 잔량의 꼬리수익을
  보존한다.
- paired 결과가 B를 지지하면 KR PathB는 `전량 조기보호형`에서 `부분확정+볼록 러너형`으로 바뀐다.
- SCHG/BIL·KR 팩터 코어는 PathB 신호가 없는 기간에도 자본을 시장 추세에 참여시킨다.

### 직접 알파가 아닌 운영 레버

- heartbeat와 `/monitor`는 전략 자체의 기대수익을 높이지 않는다.
- 대신 tsmom·us_swing처럼 트래커가 조용히 죽어 수익 기회와 검증 표본이 0이 되는 운영 누수를 막는다.
- 중앙 자본배분기는 신규 알파가 아니라 코어와 PathB의 중복 베타·현금경합·과다노출을 제한한다.

## 8. 구현 순서

1. 본 계약과 통합 보고서의 출구 소유권·낙관 상한·반증 근거를 동기화한다.
2. KR 신규 체결 스루풋과 paired 표본 페이스를 `/monitor`에서 먼저 관측하고 starvation을 P0로 연결한다.
3. paired observer의 read-only adapter, 독립 state machine, append-only ledger를 구현한다.
4. 실시간 주문 없이 A/B 가상체결과 `exit_owner`·`A_FALLBACK_QTY1` 귀속 테스트를 배선한다.
5. 통합 코어 tracker, scheduler, heartbeat를 구현한다.
6. `/monitor`에 코어·US swing·paired observer freshness와 설정 3중 일치 검사를 추가한다.
7. forward 게이트 판정기를 구현하고 세 분모의 결과를 고정 포맷으로 출력한다.
8. 게이트 통과 후 별도 변경 단위로 KR enforce 플래그·부분매도 실행 경로와 이중 소스 runbook을 구현한다.
9. MICRO 운영자 승인 후 실제 체결 truth와 counterfactual A를 계속 비교한다.

중앙 자본배분기는 1~6과 분리된 설계 승인 단위다. shadow 노출 시뮬레이션까지는 진행할 수 있지만 실제
주문가능금·슬롯 경로 연결은 별도 승인을 요구한다.
