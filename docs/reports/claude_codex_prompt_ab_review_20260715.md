# Claude vs Codex 동일 프롬프트 소규모 A/B — 2026-07-15

## 결론

동일한 라이브 `single_symbol_judge_v1` 사용자 프롬프트를 넣어도 Claude와 Codex의 결과는 같지 않았다.
4건 중 action 일치는 2건(50%)이었다. 두 모델 모두 4/4 strict JSON을 반환했지만, 플랜 생성 여부와
confidence가 크게 달랐다. 따라서 provider를 무통보 교체하거나 두 결과를 같은 의미로 취급하면 안 된다.

## 방법

- Claude 기준: 저장된 실제 라이브 raw-call의 원래 응답
- Claude 모델: `claude-opus-4-8`, single-symbol thinking off
- Codex: raw-call의 `prompt` 문자열을 바이트 내용 그대로 stdin 재생
- Codex 실행: CLI 0.144.3, ephemeral, read-only, ignore-rules
- 관측 Codex 모델: `gpt-5.6-sol`, reasoning `xhigh`
- 프롬프트 SHA256을 결과 JSON에 저장
- 외부 주문·설정·DB 변경 없음

동일한 것은 사용자 프롬프트다. provider의 숨은 system prompt, reasoning 설정, API surface는 다르므로
순수 기초모델만의 통제실험은 아니다. 현재 실제 제품을 사용했을 때의 운영 비교다.

## 결과

| 시장·종목 | Claude | Codex | 일치 |
|---|---|---|---:|
| US AAPL | PULLBACK_WAIT, 312.5~313.1 | WAIT_RECHECK | 아니오 |
| KR 263800 | WAIT_RECHECK | PULLBACK_WAIT, 5900~5980 | 아니오 |
| US SKHY | WAIT_RECHECK | WAIT_RECHECK | 예 |
| KR 001210 | REJECT | REJECT | 예 |

- action agreement: 2/4 = 50%
- strict JSON: Claude 4/4, Codex 4/4
- 평균 confidence: Claude 0.533, Codex 0.840
- 평균 지연: Claude API 4.76초, Codex CLI 22.93초

### AAPL

현재가 314.82에서 Claude의 buy_zone_high 313.1은 0.546% 아래라 문면상 추격금지 규칙을 통과한다.
Claude는 VWAP/open-anchor 아래의 대기존을 허용했다. Codex는 실제 VWAP/open-anchor 자체가 현재가에서
0.5% 미만 떨어져 있다는 점과 OR breakout 미확인을 더 크게 보아 WAIT했다. 둘 다 규칙 위반이라기보다
지지구간을 얼마나 확장해서 인정하는지의 차이다.

### KR 263800

Claude는 fade·VWAP 하단·OR breakout 실패를 이유로 WAIT했다. Codex는 5920 open anchor와 5880 OR low를
구조적 군집으로 인정해 5900~5980 대기존을 만들었다. AAPL과 반대 방향이므로 Codex가 일관되게 더
보수적이거나 더 공격적이라고 말할 수 없다.

### SKHY / 001210

stale 가격 충돌과 심한 fade 같은 명백한 위험에서는 action이 일치했다. 그러나 SKHY confidence는
Claude 0.40, Codex 0.96으로 크게 달라 confidence 임계값을 provider 공통으로 쓰면 안 된다.

## 시스템 적용 판정

1. **즉시 교체 금지:** 현재 Claude를 Codex로 바꾸면 플랜 생산량과 종목 구성이 달라진다.
2. **confidence 공통 임계값 금지:** 모델별 별도 calibration이 필요하다.
3. **Codex는 shadow second opinion부터:** 두 판단과 후속 성과를 독립 저장한다.
4. **초기 fail-closed 합의:** 한 모델이 WAIT/REJECT이고 다른 모델만 PULLBACK_WAIT이면 주문하지 않고 WAIT한다.
5. **가격 평균 금지:** 둘 다 플랜을 만들더라도 buy zone·target·stop을 기계적으로 평균내지 않는다.
6. **생산 연결은 CLI가 아닌 API:** 현재 Codex CLI는 평균 약 23초이고 agent system prompt가 추가된다.
   라이브 연결 검토 시 고정 OpenAI API 모델·structured output·reasoning 설정을 별도 계약해야 한다.

## 다음 정식 벤치마크

최소 60건을 US/KR과 Claude action별로 층화한다. 측정값은 단순 일치율이 아니라 다음이다.

- action agreement와 Cohen's kappa
- strict-schema 성공률·재시도율·지연·비용
- 둘 다 PULL인 경우 buy-zone overlap, target/stop 거리 차이
- stale-data·추격금지·RR validator 위반률
- 각 provider 플랜을 동일 체결 엔진으로 재생한 비용 후 net과 missed-opportunity
- 상위 3건 제거·시장별·국면별 안정성

승격은 “Claude와 더 비슷함”이 아니라 동일 데이터에서 비용 후 net·안전 위반·지연을 함께 개선했을 때만 한다.

재현 원장: `reports/claude_codex_prompt_ab_20260715.json`
