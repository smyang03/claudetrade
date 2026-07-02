# 보류 항목 #6b·#7 — 전후 리포트 (2026-06-26)

> 위생 감사 보류 2건(라이브 위험). 운영자 "진행해" → 조사·수정.
> 검증: py_compile PASS / mojibake PASS / preflight ok=True·FAIL 0 / candidate_action pytest 167 passed. 커밋 안 함.

---

## #6b — 후보 액션 만료/grace TZ 정합 → ✅ 완료 (안전하게)

### 검토
- 시각 파서들이 `.replace(tzinfo=None)`로 tzinfo를 strip → UTC("Z") 시각이 KST 벽시계와 비교돼 **±9h 왜곡**. 액션이 9h 일찍/늦게 만료, grace 윈도우 오판 → 진입 허용/차단 오류.
- **콜러 전수 감사(보류 사유였던 것):** `_parse_candidate_route_time` 콜러는 trading_bot.py 3곳(grace 6131·6136, stale 6161)뿐이고 **메서드 출력은 bool/float**(datetime 미반환) → 외부 naive-의존 콜러 없음. candidate_actions.py `_parse_dt`는 모듈 내부 전용. → **self-contained로 안전 확인.**

### 개선방향
aware 통일 — "Z"=UTC 유지, naive=시스템 로컬(KST) 가정 후 aware-aware 비교. candidate_actions.py(전 모듈 naive)는 `_parse_dt`가 aware를 **KST로 정규화 후 strip**(naive 입력 불변, aware만 변환).

### 구현 (5곳)
| 파일:위치 | 전 | 후 |
|---|---|---|
| `trading_bot.py` `_parse_candidate_route_time` | `.replace(tzinfo=None)` (UTC-naive) | aware 반환(Z=UTC, naive→KST) |
| `trading_bot.py` grace `current`(6122) | `(now\|now(KST)).replace(tzinfo=None)` | aware(naive면 KST) |
| `trading_bot.py` stale `current`(6164) | 동상 | aware |
| `runtime/candidate_actions.py` `_parse_dt` | strip | `_to_kst_naive`(aware→KST 정규화) |
| `runtime/candidate_actions.py` `_min_expiry` raw_expiry | strip | `_to_kst_naive` |

### 전→후 (스모크)
UTC "Z" 14:00 / KST+09 23:00 / naive 23:00:
- **전:** "Z" → 14:00(KST 벽시계로 오인, 9h 빠름)
- **후:** 셋 다 **KST 23:00로 일치** ✓

→ 액션 만료·grace가 정확한 시각에. **라이브 진입 타이밍 변화 → A1처럼 운영테스트로 확인.**

---

## #7 — dead STALE_MARKET_DATA 게이트 → ⏸ 안전 수정 불가 (전용 인프라 필요)

### 검토 (조사 결과)
- `pathb_runtime.py:4312`가 `last_market_data_at=datetime.now(KST)` **항상** 세팅 → `safety_gate.py:129` `_is_stale` 영구 False → 시세 신선도 게이트 no-op.
- **수정하려면 진입 가격의 실제 fetch/quote 시각이 필요한데 — 존재하지 않음:**
  - `get_price`(kis_api) 반환에 타임스탬프 없음.
  - 진입 가격은 signal.limit_price/price에서 오는데 "언제 fetch됐나"를 안 들고 옴.
  - 유일한 price+시각은 `_exit_price_cache[key]=(price, time.time())`(11103) — **청산 경로 전용**, 진입 무관.
- **나이브 수정 위험(보류 사유 확인):** now()를 다른 값으로 바꾸려면 fetch 시각을 fetch→signal→SafetyContext로 threading하는 전용 인프라가 필요하고, 잘못 잡으면 **전 진입 차단**.

### 완화 (이미 부분 커버)
- freshness 우려는 `broker_trust_level`(degraded/untrusted→quarantine, `safety_gate.py:104-119`)가 **부분 커버** 중. 브로커 데이터 불신 시 별도 차단됨.

### 결정
**강제 수정 안 함.** 진입 가격 fetch-타임스탬프 인프라 구축 + 운영테스트가 필요한 전용 작업이고, 마라톤 세션 끝 나이브 수정은 전 진입 차단 위험(CLAUDE.md "조용히 깨짐" 금지). broker_trust가 부분 커버하므로 긴급도도 낮음.

### 권고 (별도 작업)
진입 시 사용한 시세의 실제 quote/fetch 시각을 캡처(KIS quote ts or fetch 직전 시각) → SafetyContext.last_market_data_at에 전파 → shadow로 "frozen feed 오탐 0" 확인 후 enforce. 우선순위 낮음(broker_trust 부분커버).

---

## 검증
| 단계 | 결과 |
|---|---|
| py_compile (trading_bot·candidate_actions) | ✅ PASS |
| mojibake | ✅ PASS |
| live preflight | ✅ ok=True, FAIL 0 |
| pytest (candidate_action/expir/grace/no_signal) | ✅ **167 passed** |
| TZ 정규화 스모크 | ✅ Z↔KST↔naive 모두 동일 |

## 한 줄 결론
#6b(액션 만료/grace TZ)는 콜러가 self-contained임을 확인해 **5곳 aware/KST 정합으로 안전 수정**(±9h 왜곡 제거, 167 테스트 통과) — 라이브 진입 타이밍 변화라 운영테스트 대기. #7(dead STALE 게이트)는 **진입가 fetch-타임스탬프 인프라가 없어 안전 빠른수정 불가** + broker_trust 부분커버이므로, 강제 수정 대신 전용 작업으로 분리(나이브 수정시 전 진입 차단 위험).
