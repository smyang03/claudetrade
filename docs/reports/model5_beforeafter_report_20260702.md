# Claude 5.0 신/구 모델 코드경로 리플레이 비교 리포트 (2026-07-02)

방식: 실제 `logs/raw_calls`의 진짜 프롬프트를 **프로덕션 추출(`response_text`)+파싱(`extract_json`) 경로**로 재생.
구 모델=로그된 결과, 신 모델=`claude-sonnet-5`(프로덕션 thinking 설정 그대로). "API 말고 code로" = 별도 raw-HTTP가 아닌 프로덕션 추출/파싱 경로 사용. 병렬(동시성 6). 원본 `docs/reports/model5_beforeafter_20260702.json`.

지표: dir_match=신/구 방향(action/stance/trade_ready) 일치율, json=신 모델 JSON 유효, out/ms=출력토큰/지연 중앙값, in=입력토큰 증감(토크나이저).

| 항목 | n | 구 모델 | 신 thinking | dir_match | json | out 구→신 | ms 구→신 | in |
|---|---|---|---|---|---|---|---|---|
| **select_tickers** | 20 | sonnet-4-6 | adaptive/med | **90%** | **12/20** | 1672→2362 | ~0→**22156** | +24% |
| **hold_advisor_challenge** | 10 | sonnet-4-6 | adaptive/med | **100%** | 10/10 | 292→404 | 5998→**5159** | +21% |
| **hold_advisor_triage** | 10 | sonnet-4-6 | adaptive/med | **100%** | 10/10 | 364→414 | 7801→**5006** | +16% |
| **analyst_bear_r1** | 10 | **haiku**/sonnet | adaptive/med | 80% | 10/10 | 353→318 | 4796→6857 | +12% |
| **analyst_neutral_r1** | 10 | **haiku**/sonnet | adaptive/med | 80% | 10/10 | 399→497 | 4053→8730 | +12% |
| **analyst_bull_r1** | 10 | sonnet-4-6 | adaptive/med | 80% | 10/10 | 241→468 | 6665→8030 | +11% |
| single_symbol_judge* | 10 | opus/sonnet | disabled | 70% | 10/10 | 319→210 | 5994→3729 | +0% |

\* single_symbol은 **프로덕션에서 opus-4-8 유지·thinking OFF = 무변경**. 위 행은 sonnet-5로 바꿨을 때의 가정치(배포 아님).

## 항목별 판정

- **hold_advisor (challenge/triage) — 완승**: 신/구 결정 100% 동일, JSON 100%, **지연 오히려 감소**(thinking 켜도 sonnet-5가 4.6보다 빠름). thinking ON 안전.
- **selection — 안정하나 비용 주의**: 방향 90% 동일(thinking이 선택을 거의 안 바꿈). BUT **thinking시 지연 22s + 출력 2362토큰**, 하네스 max_tokens=4000에서 **JSON 8/20 실패**(대부분 thinking이 예산 잠식→절단). **프로덕션은 CLAUDE_SELECTION_MAX_TOKENS=6000 + compact schema + 3회 retry + fallback**이라 절단·실패율이 크게 낮아짐(중앙 출력 2362<6000). 그래도 **선택은 진입 퍼널의 관문**이므로 22s 지연·JSON 실패율을 내일 실측 모니터링 권장. 악화 시 `CLAUDE_THINKING_SELECTION=off`(1-flip, sonnet-5 무thinking drop-in=A/B 안전확인) 즉시 회수.
- **analyst r1 (하이쿠 포함) — 80% 동일, 변화는 극단→온건**: 하이쿠→sonnet5 변화 예: `MODERATE_BULL→MILD_BULL`, `NEUTRAL→CAUTIOUS_BEAR`; bull은 `AGGRESSIVE→MODERATE_BULL`, `NEUTRAL→MILD_BULL`. sonnet-5가 극단 스탠스를 온건/신중으로 당김. 메모리 교훈(과신·AGGRESSIVE=추격 -EV 역신호)과 방향 일치 → **개선 가능성**(단 스탠스만으로 net 판정 불가). JSON 100%. 지연 소폭↑(thinking).
- **single_symbol — 무변경**: 프로덕션 opus-4-8·thinking off 유지. 하네스의 sonnet-5 리플레이는 참고용(opus→sonnet5시 70% 동일, 대체로 WAIT_RECHECK로 수렴).

## 종합
- **응답추출 리팩터 실증**: thinking ON인 hold_advisor/analyst에서 **JSON 100%** — `response_text` 리팩터가 thinking 블록을 정확히 건너뜀을 실데이터로 확인(구 `content[0].text`였다면 전멸).
- **토크나이저 +11~24%**: 비용·max_tokens 재기준 필요(반영됨).
- **thinking 순효과**: 방향은 대부분 보존(selection 90%, analyst 80%). 변화분은 "예측 향상"이라기보다 **극단→온건 보정**. 메모리의 selection 무알파 전제와 부합 → thinking의 값은 net alpha보다 **파싱 안정·일관성·과신 완화**로 보는 게 정직. net은 재시작 후 shadow/실거래 국면분리로만.
- **하이쿠 승격 효과**: bear/neutral R1이 haiku→sonnet5로 80% 유지, 20%는 온건화. 품질 상향이나 지연 4~5s→7~9s(thinking).

## 배포 상태 & 권장
- 배포: sonnet-5 전환 + thinking(analyst/hold/selection ON medium, 진입/타임아웃 게이트 OFF). 코드 커밋 `1a5782e`, config/.env.live 디스크 반영. **재시작=내일 아침 운영자**.
- 내일 모니터링 1순위: **selection JSON 유효율·지연**(악화 시 `CLAUDE_THINKING_SELECTION=off`). 2순위: hold_advisor/analyst 방향분포 shift. 3순위: 비용(토큰 +% 반영 확인).
- 후속: shadow 누적 후 single_symbol thinking-on 여부 판정(A/B서 결정변경 관측).
