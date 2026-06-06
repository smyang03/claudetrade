# 후보군 스크리너 개선 분석 요청 템플릿

이 문서는 후보군 스크리너 개선 방향을 AI에게 요청할 때 쓰는 입력 템플릿입니다.
selection 품질 문제와 execution/risk 문제를 섞지 않고, 실제 개선으로 이어지도록 필요한 정보를 한 번에 전달하는 것을 목표로 합니다.

## 복붙용 요청문

```text
후보군 스크리너 개선 방향을 분석해줘.

코드 수정 전에는 이번 이슈의 직접 수정 범위, 건드리지 않을 보호 영역, 수정 예정 파일, 실행할 검증 명령을 먼저 명시해줘.

목표:
- 대상 시장: KR / US / 둘 다
- 대상 경로: Path A / Path B / 공통
- 개선하고 싶은 지표: trade_ready 정밀도 / watchlist recall / 급등주 조기 포착 / 손실 후보 감소 / 후보 수 안정화
- 기본 적용 방향: enforce/live 전제
- shadow 예외가 필요하다면 예외 사유, 관찰 지표/기간, live 전환 조건을 먼저 제시

최근 변경:
- 관련 커밋 또는 브랜치:
- 변경한 파일:
- 기대했던 효과:
- 실제로 관찰된 효과:

현재 문제:
- 잘못 뽑힌 후보 false positive 예시:
- 놓친 후보 false negative 예시:
- 특정 날짜/시장 상황:
- 후보 수가 너무 많거나 적은 구간:
- conviction 또는 strategy-fit이 어긋난 사례:

분석에 쓸 데이터:
- 기간: 예) 최근 4주, 최근 100거래 후보, 특정 날짜
- 봐야 할 DB/log/report:
- 성과 기준: 예) 다음날 수익률, 진입 후 MFE/MAE, 실제 체결 성과, Claude selection 통과율

제약:
- selection 품질 개선만 다루고 execution/risk 문제와 섞지 말 것
- 주문 수량/금액 계산, hard stop, broker truth, live gate는 건드리지 말 것
- PathB 보호 영역과 수익 핵심 경로는 필요한 근거 없이 수정하지 말 것
- config/env 변경이 필요하면 먼저 영향과 검증 계획을 보고할 것
- state/brain.json 자동 변경 또는 직접 수정 경로는 추가하지 말 것

원하는 결과:
- 현재 후보 생성 흐름 요약
- false positive / false negative 원인 분리
- KR/US 및 전략별 성과 분리
- 즉시 반영 가능한 개선안
- 운영 데이터가 더 필요한 개선안
- 위험해서 보류해야 하는 개선안
- 가능한 범위의 코드 수정과 테스트/QA
```

## 최소 입력

시간이 없을 때는 아래처럼 짧게 줘도 됩니다.

```text
최근 후보군 스크리너 개선 diff를 보고, selection 품질 관점에서 추가 개선점을 찾아줘.
대상은 US PathB 후보 생성이고, 주문/리스크/브로커 truth 경로는 건드리지 마.
최근 4주 후보 중 false positive를 줄이는 게 목표야.
필요하면 로컬 DB/log를 읽고, 개선안 우선순위를 낸 뒤 가능한 것은 코드 수정과 테스트까지 해줘.
코드 수정 전에는 직접 수정 범위, 건드리지 않을 보호 영역, 수정 예정 파일, 검증 명령을 먼저 명시해줘.
```

## 가장 도움이 되는 사례 입력

개선 방향을 정확히 잡으려면 ticker/date 단위 사례가 가장 유용합니다.

```text
false positive:
- 2026-06-03 / US / ABCD: 후보로 나왔지만 이후 급락. volume spike만 강했고 strategy-fit이 약해 보임.
- 2026-06-04 / KR / 123456: trade_ready였지만 실제 체결 후 MAE가 컸음.

false negative:
- 2026-06-03 / US / WXYZ: 후보에서 빠졌지만 이후 강하게 상승. premarket momentum과 relative volume이 있었음.
- 2026-06-04 / KR / 654321: watchlist에는 있었지만 trade_ready로 승격되지 않음.
```

## 분석 시 우선 확인할 축

- 후보 생성 경로: raw universe, screener filter, ranking, watchlist, trade_ready.
- Claude selection 연결: raw response, normalized/applied trade_ready, rejection reason.
- 성과 분리: KR/US, Path A/Path B, strategy별로 따로 비교.
- 품질 분리: selection miss인지, affordability/risk/order/live gate 문제인지 분리.
- 운영 안전: broker truth, hard stop, PathB protected area, config/env 영향 여부.
- 가시성: log, audit DB, dashboard/report에서 개선 효과를 관찰할 수 있는지 확인.

## 최종 보고에 요구할 항목

최종 보고에는 아래 구분을 명시하도록 요청하세요.

- 반영 완료: 이번 작업에서 실제로 수정한 항목.
- 비차단 잔여 리스크: 이번 변경은 허용하지만 운영자가 알아야 할 테스트 공백 또는 미검증 축.
- 범위 밖 후속 개선: 별도 승인, 장기 관찰, 운영 데이터가 필요한 항목.
- 실행한 검증: 단위 테스트, 통합 테스트, py_compile, preflight, dry-run, dashboard/log 확인 등.
- 미검증 축: 테스트가 직접 보장하지 못한 데이터 흐름, 운영 모드, stale/empty/default 케이스.
