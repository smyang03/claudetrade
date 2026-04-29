# Historical Candidate / Execution Review (2026-04-20 ~ 2026-04-29)

작성일: 2026-04-30  
범위: 2026-04-20 ~ 2026-04-29 KR/US 실거래 로그 및 후보 기록  
주요 소스:
- `data/ticker_selection_log.db`
- `logs/daily_judgment/live_20260420_*` ~ `live_20260429_*`
- `logs/system/live_trading_20260420.log` ~ `live_trading_20260429.log`
- `logs/screener_quality/20260428_US_candidates.jsonl`
- `logs/screener_quality/20260429_KR_candidates.jsonl`
- `logs/screener_quality/20260429_US_candidates.jsonl`

## 데이터 한계

- `ticker_selection_log.db`는 2026-04-07부터 존재하지만, 최신 `trade_ready` 체계와 직접 비교 가능한 구간은 주로 2026-04-20 이후다.
- `screener_quality` 상세 후보 품질 로그는 2026-04-28 US, 2026-04-29 KR/US, 2026-04-30 US만 남아 있다.
- 2026-04-29 US 세션은 `daily_judgment`의 최종 `actual_result`가 아직 완결되지 않은 상태로 보인다.
- 일부 `daily_judgment.trades`에는 fallback/중복 기록이 있어, 손실 캡 시뮬레이션은 중복 제거 기준으로 별도 계산했다.

## 1. 후보 선정 성능

### KR

| 구분 | 건수 | signal 전환 | 실제 거래 전환 | 평균 forward_3d | 평균 max_runup_3d | 평균 max_drawdown_3d | 실현 pnl 평균 |
|---|---:|---:|---:|---:|---:|---:|---:|
| trade_ready | 61 | 14.8% | 9.8% | +6.02% | +15.95% | -8.10% | -1.81% |
| watch_only | 378 | 2.4% | 1.3% | -5.38% | +9.36% | -11.42% | -0.58% |

판정:
- KR `trade_ready`는 watch 대비 forward_3d가 +11.40%p 높다.
- max_runup_3d도 +6.59%p 높다.
- 후보 선정 자체에는 의미 있는 알파가 있다.
- 하지만 `trade_ready` 실현 pnl 평균은 -1.81%다. 문제는 후보 선정이 아니라 실행/청산/주문상태 쪽이다.

주의:
- KR `trade_ready`의 median forward_3d는 -1.91%로, 평균 +6.02%는 일부 큰 승자에 의해 올라간다.
- 따라서 "후보는 좋다"와 "아무 ready나 사면 된다"는 다른 말이다.

### US

| 구분 | 건수 | signal 전환 | 실제 거래 전환 | 평균 forward_3d | 평균 max_runup_3d | 평균 max_drawdown_3d | 실현 pnl 평균 |
|---|---:|---:|---:|---:|---:|---:|---:|
| trade_ready | 94 | 9.6% | 4.3% | +0.98% | +8.39% | -4.83% | +0.01% |
| watch_only | 386 | 1.0% | 0.3% | -0.04% | +6.81% | -5.57% | -3.75% |

판정:
- US도 `trade_ready`가 watch보다 낫지만, KR만큼 강하지 않다.
- US는 `trade_ready` 임계치를 KR보다 높이거나, opening trade_ready 보호와 동시에 약한 ready의 즉시 실행을 제한해야 한다.

## 2. 일별 실제 성과와 시장 대비

| 날짜 | 시장 | 모드 | 시장 변화 | 봇 pnl | 거래 수 | 오염 여부 |
|---|---|---|---:|---:|---:|---|
| 2026-04-20 | KR | DEFENSIVE | +1.02% | +0.00% | 0 | False |
| 2026-04-20 | US | MILD_BULL | -0.49% | +0.00% | 0 | False |
| 2026-04-21 | KR | CAUTIOUS | +2.72% | -0.06% | 3 | True |
| 2026-04-21 | US | MILD_BULL | -0.63% | +0.01% | 0 | False |
| 2026-04-22 | KR | MILD_BULL | +0.46% | -0.37% | 0 | True |
| 2026-04-22 | US | CAUTIOUS | +1.03% | +0.11% | 1 | True |
| 2026-04-23 | KR | MILD_BULL | +0.90% | +0.01% | 1 | False |
| 2026-04-23 | US | MILD_BULL | -0.41% | -0.85% | 0 | False |
| 2026-04-24 | KR | MILD_BULL | -0.00% | +0.00% | 0 | False |
| 2026-04-24 | US | NEUTRAL | +0.78% | +0.00% | 0 | False |
| 2026-04-27 | KR | MILD_BULL | +2.15% | -0.26% | 1 | False |
| 2026-04-27 | US | MODERATE_BULL | +0.12% | -0.66% | 10 | True |
| 2026-04-28 | KR | MILD_BULL | +0.39% | -1.81% | 11 | True |
| 2026-04-28 | US | MILD_BULL | -0.48% | -0.13% | 1 | False |
| 2026-04-29 | KR | MODERATE_BULL | +0.75% | -1.14% | 4 | True |

판정:
- KR은 2026-04-27 ~ 2026-04-29에 시장이 모두 상승했는데 봇은 계속 음수였다.
- 이 구간은 후보 선정 실패라기보다, 진입 실행/청산/주문 정합성 문제로 보는 게 맞다.
- 특히 2026-04-28, 2026-04-29는 실행 오염이 명시되어 있다.

## 3. 장초 후보 교체 패턴

### 90분 이내 초기 trade_ready 교체

| 날짜 | 시장 | 경과 | 기존 ready | 새 ready | overlap |
|---|---|---:|---|---|---:|
| 2026-04-22 | KR | 25.1m | 010170, 093370, 281740, 462010 | 027360, 051980, 131400, 209640, 222080 | 0 |
| 2026-04-24 | KR | 7.8m | 010140, 032820 | 209640, 332570 | 0 |
| 2026-04-24 | KR | 55.5m | 010140, 032820 | 006340, 027360, 332570 | 0 |
| 2026-04-27 | KR | 65.5m | 336370 | 006340, 010170 | 0 |
| 2026-04-29 | KR | 10.7m | 001780, 138360 | 006340, 047040, 058430, 178320 | 0 |
| 2026-04-29 | KR | 21.9m | 001780, 138360 | 001440, 006340, 098460 | 0 |
| 2026-04-29 | US | 31.7m | CNC, NOK, SANM | BE, NXPI, STX, TEVA | 0 |
| 2026-04-29 | US | 61.5m | CNC, NOK, SANM | BE, NXPI, STX | 0 |

판정:
- 초기 후보가 장초 10~30분에 전부 갈리는 패턴은 하루짜리 문제가 아니다.
- KR은 US보다 더 빠르게 바뀐다. 2026-04-24 KR은 7.8분 만에 ready가 전부 교체됐다.
- US는 2026-04-29에 restart/session_reuse_rescreen 경로가 opening basket을 31.7분 만에 갈아버렸다.

트레이너 관점:
- 장초 후보 교체 자체는 필요하다.
- 하지만 초기 trade_ready의 thesis를 바로 지우는 것은 문제다.
- 특히 ORP/gap_pullback은 10~45분의 셋업 시간이 필요하므로, 후보 보호 없이 교체하면 전략과 운영 타이밍이 충돌한다.

## 4. 막힌 좋은 기회

| 날짜 | 시장 | 종목 | 차단 사유 | ready | f1 | f3 | max_runup_3d |
|---|---|---|---|---:|---:|---:|---:|
| 2026-04-24 | KR | 006340 | order_size_too_small | 1 | +12.75% | +45.12% | +45.12% |
| 2026-04-24 | KR | 209640 | order_size_too_small | 0 | +20.35% | +14.10% | +29.92% |
| 2026-04-23 | US | ARM | qty_zero | 1 | +14.76% | -2.91% | +16.16% |
| 2026-04-23 | US | AMD | qty_zero | 0 | +13.91% | N/A | N/A |
| 2026-04-28 | KR | 002780 | PATHB_ORDER_UNKNOWN_SAME_TICKER | 1 | +7.60% | N/A | N/A |
| 2026-04-28 | KR | 138360 | DAILY_LOSS_LIMIT | 0 | +4.99% | N/A | N/A |
| 2026-04-20 | US | DK | order_rejected | 0 | +2.71% | +4.79% | +6.00% |

판정:
- 놓친 기회의 상당수는 후보 선정 문제가 아니라 주문/사이즈/ORDER_UNKNOWN/false block 문제다.
- 특히 2026-04-24 KR `006340`은 forward_3d +45.12%로, 작은 주문/사이즈 제약 하나가 큰 기회를 막은 대표 케이스다.
- 2026-04-28 KR `002780`은 ORDER_UNKNOWN 계열이 다음 기회를 막은 케이스다.

## 5. 손실 캡 시뮬레이션

중복 제거한 `daily_judgment.trades` 기준:
- 고유 거래 48건
- 합산 pnl_pct: -26.97%p

| 손실 캡 | 영향 거래 수 | 기존 합산 | 적용 후 합산 | 개선폭 |
|---|---:|---:|---:|---:|
| -3.0% | 5 | -26.97%p | -10.61%p | +16.35%p |
| -2.0% | 6 | -26.97%p | -5.61%p | +21.36%p |
| -1.5% | 10 | -26.97%p | -1.53%p | +25.44%p |

`-3%` 캡에 걸리는 대표 거래:
- 2026-04-28 KR `002780` -9.82%
- 2026-04-29 KR `058430` -7.38%
- 2026-04-28 KR `452190` -5.62%
- 2026-04-28 KR `452260` -5.01%
- 2026-04-27 US `QCOM` -3.53%

판정:
- 손실 캡은 실험이 아니라 구조적 방어 장치다.
- 과거 데이터 기준 `-3%`만 걸어도 대형 손실 대부분을 줄인다.
- `-1.5%`는 개선폭은 크지만 정상 변동을 너무 빨리 자를 위험이 있다.
- 현재 방향처럼 `loss_cap`을 hard overlay로 두는 설계가 맞다.

## 6. Claude SELL / Intraday Review 이슈

소폭 손실에서 Claude/intraday review가 매도를 낸 케이스:
- 2026-04-21 KR `047040`: -0.78%, 이후 max_runup_3d +10.20%
- 2026-04-27 KR `006340`: -0.54%, 이후 데이터 제한
- 2026-04-29 KR `006345`: -0.85%, 당일 강한 반등 케이스

판정:
- 방향은 맞지만 아직 hard rule로 잠글 정도의 표본은 부족하다.
- 먼저 청산 감사 로그가 필요하다.
- `exit_owner`, `last_claude_vote`, `hard_rule_triggered`, `profit_floor`, `peak_pnl_pct`, `entry_conviction` 기록이 우선이다.

## 7. 개선 우선순위

### P0. 주문 정합성 / ORDER_UNKNOWN 시장 차단

해야 할 일:
- ORDER_UNKNOWN이 시장 단위로 걸리면 PathA, PathB, Tier2 신규 매수 모두 차단.
- broker-only open order 발견 시 즉시 `market_block`.
- duplicate open order는 ticker 단위가 아니라 order_no 단위로 추적.

근거:
- 2026-04-28 KR `002780`: ORDER_UNKNOWN same ticker로 +7.60% f1 기회 차단.
- 2026-04-29 KR `006340`, `047040`: broker/local open order mismatch가 장중 지속.

### P1. Loss Cap 유지 및 검증

해야 할 일:
- 현재 구현한 `loss_cap`을 유지.
- exit 기록에 `CLOSED_LOSS_CAP`이 정확히 남는지 2~3세션 확인.
- PathA/PathB 모두 동일한 hard overlay로 적용.

근거:
- -3% cap만으로 과거 고유 거래 합산 손익이 +16.35%p 개선되는 시뮬레이션 결과.

### P1. Opening Candidate Protection

KR:
- 초기 trade_ready 보호 시간: 20~30분.
- fresh 후보는 허용하되 기존 ready를 즉시 밀어내지 않음.
- fresh 후보는 2사이클 확인 후 실행.

US:
- 초기 trade_ready 보호 시간: 45분.
- restart/session_reuse_rescreen은 opening lock 중 기존 selection 복구 우선.

공통:
- 보호 대상은 전체 watchlist가 아니라 `initial trade_ready` + `active PathB waiting plan`.
- 가격이 first_ready_price 대비 KR -2.5%, US -2.0% 이상 약화되면 보호 해제.

### P1. Candidate Health 기반 약화 감지

사용할 raw 값:
- `first_ready_price`
- `last_price`
- `ready_count`
- `mfe_pct`
- `mae_pct`
- `recovered_first_ready`

초기 규칙:
- `ready_count >= 3`
- `mae_pct <= -2.0%`
- `recovered_first_ready == false`
- 위 조건이면 `WEAKENING_READY`

적용 방식:
- 1단계: 로그만.
- 2단계: size/priority penalty.
- 3단계: hard block 검토.

### P2. Claude SELL Soft Guard

초기 규칙 제안:
- 진입 후 30분 미만이고 손실이 -1.5%보다 작으면 Claude SELL은 즉시 실행하지 않고 1회 재확인.
- 단, hard stop/loss_cap/profit_floor는 Claude보다 먼저 실행.
- Claude SELL 거부 시 반드시 audit log 기록.

## 8. 최종 판단

1. 후보 선정 시스템은 버릴 게 아니다.
   - KR trade_ready는 watch 대비 forward_3d +11.40%p로 명확한 알파가 있다.
   - US도 약하지만 trade_ready가 watch보다 낫다.

2. 실제 손실의 주 원인은 후보 선정이 아니라 실행/청산/주문 상태다.
   - ORDER_UNKNOWN, order_size_too_small, qty_zero, DAILY_LOSS_LIMIT false block이 좋은 후보를 막았다.
   - 큰 손실은 loss_cap 부재/미적용에서 반복됐다.

3. 장초 후보 교체 문제는 KR/US 모두 재현된다.
   - KR은 7~25분 이내 전면 교체가 이미 여러 번 있었다.
   - US는 restart/session_reuse_rescreen 때문에 30분대 전면 교체가 발생했다.

4. 개선 방향은 공격성을 낮추는 게 아니라 구조를 분리하는 것이다.
   - 초기 후보는 일정 시간 thesis를 보존한다.
   - fresh 후보는 계속 발굴한다.
   - 하지만 fresh 후보는 즉시 실행하지 않고 2사이클 검증한다.
   - 손실 캡은 무조건 hard rule로 둔다.

## 실행 결론

다음 개발 우선순위:
1. ORDER_UNKNOWN market-level 신규 매수 차단 검증
2. loss_cap 실거래 로그 검증
3. opening candidate protection 설계/구현
4. candidate health log-only 2~3세션 축적
5. Claude SELL soft guard는 audit 데이터 확인 후 적용

