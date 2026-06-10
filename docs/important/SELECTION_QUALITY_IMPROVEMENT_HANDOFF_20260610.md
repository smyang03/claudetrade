# Selection 품질 개선 — 작업 핸드오프 (2026-06-10)

새 세션에서 이 문서를 읽고 이어서 진행한다. 완료 후 이 파일은 삭제하거나 ACTIVE_WORK.md로 이관한다.

> **세션 상태 (2026-06-10 마무리)**: #1~#6 완료·푸시(아래 표). **#7도 완료** — part(1) ATR%/blocked_reason 기록 + part(3) CLAUDE_PRICE_INVALID reason_detail 노출 구현·테스트 통과. **part(2) US vol_ratio 실값은 "실행 영향 항목"으로 재분류해 후속 과제로 분리**(아래 #7 (2) 참조). 리스트업 7항목 모두 처리 완료.
> 남은 것은 part(2)의 후속(별도 producer로 US 거래량 입력 품질) + 섹션 4 보류 항목들뿐. 이 문서는 운영자 확인 후 삭제/ACTIVE_WORK.md 이관 가능.
> 작업 규칙(섹션 6)·검증 명령(섹션 5)은 동일하게 적용.

## 0. 전체 진단 결론 (배경)

- 손실 원인은 **전략 수식 변경이 아니라 selection/execution 계약 변화**다. `strategy/*.py`는 6월 핵심 커밋 없음.
- 현재 main 구조: PlanA BUY_READY는 강하게 차단(6/8~9 BUY_READY→WATCH 111건, 최종 0건) + 약한 후보를 PlanB(PULLBACK_WAIT→single_symbol_judge→PathB)로 살림.
- 게이트가 막은 후보 forward도 -6.96% → **게이트 완화는 데이터가 반대.** 지금은 방어 국면(출혈 차단 + 입력 품질 회복).
- 적용 방침: **enforce/live 직행** (백데이터 충분, shadow 관찰 생략). KR은 "축소 방향" enforce만 (CLAUDE.md KR live 확대 금지 — guard/감점은 진입을 늘리는 게 아니라 거르는 방향이라 충돌 없음).

## 1. 운영자 확정 결정 (재확인 불필요)

- mean_reversion guard from_high 임계: **min=-2, max=1** (테스트 검증값, 6/3 이후 trade_ready의 신고가 추격 37%를 타격하는 데이터 근거).
- MILD_BEAR PathB cap: **audit-only → enforce 전환 승인됨**.
- 트랙 B(mean_reversion guard, 뉴스 감점) 범위: **US/KR 모두 enforce**.

## 2. 완료 (이 세션)

| 항목 | 커밋 | 내용 |
|---|---|---|
| #5 mean_reversion guard 코드 | `75f79da` | `trading_bot.py` guard 함수 3종 + ready_candidates 필터 연결 |
| #5 enforce 활성값 등록 | `cd180ec` | `SELECTION_MEAN_REVERSION_*` 7종 → `.env.live` + `config/v2_start_config.json` 양쪽 (min=-2/max=1) |
| #1 counterfactual 매칭 인프라 | `41365b9` | `audit/candidate_counterfactual_link.py` + `tools/report_candidate_counterfactual_link.py` + 테스트. 매칭률 0%→KR 62.8%/US 54.7% |
| #1 actual 병기 보강 | `737362c` | 이론 runup에 actual 진입/outcome/수집률 병기. missed_runup 대부분 미진입 확인 → 게이트 완화 근거 아님 |
| #2 completeness 버그픽스 | `7b356e0` | hold_advisor `_input_completeness` 대칭화: minutes_to_close 인자 + pathb_plan fallback. 보호영역 D 불변 |
| #3 soft-block floor enforce | `42e97f0`,`6eab66c` | repeated_failed_ready(>=2)/late_mover PULLBACK_WAIT 승격 차단. action_routing(2종)+single_symbol_judge(late_mover→WAIT_RECHECK). 운영자 확정 신호 2종/US·KR, enforce |
| #4 MILD_BEAR cap enforce | `2df49e3`,`f1c815d` | risk-off cap audit→enforce. `PATHB_RISK_OFF_CAP_ENFORCE=true`, caps{MILD_BEAR:2,CAUTIOUS_BEAR:1} 유지. 등록(footprint)+제출(committed) 양단 enforce, mean_reversion 예외 미적용 |
| #6 뉴스 감점 enforce | `8f92a65` | broad_weak/weak 뉴스 preopen_score -0.05 차감(`PREOPEN_WEAK_NEWS_PENALTY`). enforce-conservative, US/KR. **enforce 도달=preopen 뉴스 핀 채널 한정**(score_ok≥0.50 임계로 HARD→SOFT 강등 + preopen rank). 일반 프롬프트 풀은 trainer_score 기반이라 미적용(의도적 분리, Claude는 weak_news 태그를 프롬프트에서 봄). 검토 결론: 추가 수정 불필요 |

## 3. 남은 리스트업 (우선순위 순)

### #7 ATR / vol_ratio 스키마 [다음 세션, 계측·저위험]

느슨하게 묶인 계측 3개 묶음. 독립적이라 하나씩 끊어 커밋 가능. (이번 세션에서 read-only 스코프만 함, 미착수.)

**(1) ATR 축 저장 + ATR-blocked 사유** — *2026-06-10 코드 대조로 계획 수정*
- ⚠️ 핸드오프 원안(`insert_batch`에서 `c.get("atr")` 매핑)은 **selection 시점 candidate에 `atr`가 없어** 거의 NULL만 저장됨. ATR은 OHLC 14기간으로 신호 빌드 시점(`trading_bot.py:33353`, `sig_df.iloc[i].get("atr")`)에 계산됨.
- **수정 계획**: 실제 값이 존재하는 지점에 저장 — `atr_pct`(=atr/risk_price, `trading_bot.py:33355`)를 신호 빌드 시 `update_signal` 경로로 기록. 스키마는 `atr_pct REAL` 컬럼 + 마이그레이션 블록(`if "atr_pct" not in existing: ALTER TABLE ... ADD COLUMN atr_pct REAL`).
- **잠재 버그 동시 수정**: `momentum_atr_too_high` 차단은 `trading_bot.py:33410`에서 `tsdb.update_signal` 호출 없이 `continue`로 빠짐 → ops review의 `atr_blocked_rows`(`trading_bot.py:2161`이 `blocked_reason='momentum_atr_too_high'` 집계)가 **항상 0인 공백**. 차단 직전 `update_signal(blocked_reason="momentum_atr_too_high", atr_pct=...)` 기록 추가.
- **검증**: 값 없음/기본값/기존 DB 마이그레이션 케이스 확인(CLAUDE.md 데이터 흐름 원칙). `tests/test_candidate_audit.py` 등 selection log 테스트 회귀. ops review snapshot 테스트(`test_trading_improvements.py` `atr_blocked_rows`).

**(2) US vol_ratio 실값 — 후속 과제로 분리 (2026-06-10 운영자 승인, 이번 미진행)**

핸드오프 원안은 이 항목을 "계측·저위험"으로 분류했으나, **코드 대조 결과 계측이 아니라 실행(execution) 영향 항목**으로 재분류함. 이번 #7 작업에서 실값 연결하지 않고 후속으로 분리한다.

- **재분류 근거 (실행 영향)**: US 후보 vol_ratio는 Yahoo/FMP 스크리너에서 `1.0` 고정 주입(`kis_api.py:4862, 4923, 5042` 등). 그러나 vol_ratio는 계측만 흐르지 않고 **US live 전략 4곳이 진입 판정에 직접 소비**한다:
  - `bot/bucket_classifier.py:110` — US `vol_ratio >= 1.1`
  - `strategy/continuation.py` — `vol_ratio >= cont_vol_mult`(기본 1.5)
  - `strategy/mean_reversion.py:13` — vol_ratio 사용
  - `strategy/volatility_breakout.py:24` — vol_ratio 사용
  - 현재 1.0 고정이라 이 게이트들이 사실상 "통과/중립"으로 동작 중. 실값 교체 시 **US PathB 진입 종목 구성이 바뀜 → 보호영역(US PathB 누적 수익경로) 직접 영향**. CLAUDE.md "selection과 execution 문제를 섞지 않는다" / US PathB 보존 원칙과 충돌.
- **데이터 품질 함정 (naive 실값 = placeholder보다 나쁨)**: 장중 `regularMarketVolume`은 부분 거래량. 일평균과 단순 비교하면 장 초반 0.2~0.5로 나와 "거래량 급감"으로 오독됨. 세션 진행률 보정 없는 실값은 계측 전용 별도 필드로 저장해도 **신뢰 불가 노이즈**라 입력 품질을 오히려 떨어뜨림.
- **실현 가능성 미확인**: Yahoo 스크리너 응답에 비교 가능한 일평균 거래량 필드 존재 여부 미검증. FMP stable은 아예 volume 미포함(`volume_missing=True`).
- **제대로 하려면 (후속 과제 범위)**: 별도 producer가 US 일평균 거래량을 정식 수집 → 세션 진행률(elapsed/total)로 보정한 vol_ratio 계산 → producer→writer→소비처(전략 게이트) 흐름 일괄 검증. 빠른 계측 패치가 아니라 **"US 거래량 입력 품질" 독립 프로젝트**로 다룸. live 전략에 연결하기 전 US PathB 성과 데이터 확인 + shadow 관찰 선행.
- **이번 처리**: 실값 연결 보류. 코드 주석(`kis_api.py` US 스크리너 vol_ratio 고정 지점)에 "1.0은 placeholder, 실값은 후속 과제, 장중 부분거래량 함정 주의" 메모만 남김. 별도 측정 필드(Option 1)도 노이즈만 늘어 미채택.

**(3) CLAUDE_PRICE_INVALID 상세 + confidence_below_minimum 일반 로그 노출**
- `execution/safety_gate.py`: CLAUDE_PRICE_INVALID 차단 시 상세 사유 노출, `confidence_below_minimum` 등 현재 가려진 일반 차단 로그를 운영자 가시 로그로.

**주의**: DB 스키마 변경이라 CLAUDE.md "DB schema 변경 시 focused test 먼저" 적용. 계측 항목이므로 주문/리스크/브로커 truth 경로는 건드리지 않음. **part(2)는 위 재분류대로 후속 분리.**

## 4. 범위 밖 후속 (보류)

- #1 비차단 #3·4: counterfactual tolerance(20분) 튜닝 + ticker_day 폴백 정밀도. 저가치 — actual 병기로 과대평가는 이미 차단됨. delta 분포 측정 후 미세조정.
- #1 none 비율 37~45%: watch_only 후보는 cf 미생성이라 매칭 불가. cf producer 확대는 비용 큼 → 보류. watch_only missed runup은 별도 ops review KPI로 본다.
- cf `max_runup_60m_pct` outcome 27%만 수집 → forward outcome 수집 작업과 연결.
- #6 감점폭 튜닝: `PREOPEN_WEAK_NEWS_PENALTY=0.05`는 보수 시작값. weak_news 후보 forward 성과(candidate_audit `news_quality_tags` 조인) N거래일 관찰 후 데이터 기반 조정. 관측성은 이미 확보(preopen_reason `weak_news_penalty` 기록).
- #6 프롬프트 표기 감점: 현재 감점은 preopen 핀 채널만. 프롬프트 `news_score` 숫자까지 낮추는 건 Claude가 weak_news 태그를 이미 봐서 가치 낮음 → 필요 시에만.

## 5. 검증 명령 (각 항목 공통)

```
python -m py_compile <수정파일>
python -m pytest <관련테스트> -q
python tools/check_mojibake.py --staged
python tools/live_preflight.py --mode live --skip-dashboard --json   # config 변경 시
```

## 6. 작업 규칙 (이 세션에서 지킨 것)

- 커밋 대상만 stage. `state/brain.json`, `*.pid.stale`, runtime 산출물 제외.
- env 변경은 `.env.live`(gitignore, 로컬) + `config/v2_start_config.json`(커밋) 양쪽.
- 커밋 메시지 heredoc 사용(`git commit -F -`), PowerShell here-string 문법 금지.
- 보호영역 변경 시 `MD 위반 사항` 섹션 필수.
