# Claude 모델 5.0 업데이트 — 상세 설계 (2026-07-02)

작성: 트레이너/개발자 관점 설계 문서. **코드 수정 없음 — 검토·설계·점검 계획서.**
근거: `claude-api` 스킬(authoritative 모델 레퍼런스) + 저장소 전수 grep.
범위: Claude를 쓰는 **모든 기능별 호출부**의 모델을 4.x → 5.0으로 올릴 때의 문제점·개선점·단계별 방안·항목별 성능 점검 방법.

---

## 0. 요약 (TL;DR)

- **모델 매핑**: `claude-sonnet-4-6` → **`claude-sonnet-5`**. `claude-opus-4-8`은 이미 최신이라 **유지**. **⚠️ `Haiku 5`는 존재하지 않는다** — 최신 Haiku는 `claude-haiku-4-5`. R1/BEAR/NEUTRAL(하이쿠 3자리)은 "5로 못 올린다" → 운영자 결정 필요(§8).
- **가장 큰 블로커는 모델ID가 아니라 thinking 도입이다.** 현재 거의 모든 프로덕션 호출부가 `resp.content[0].text`로 응답을 읽는데, **thinking을 켜면 `content[0]`이 thinking 블록이 되어 전 호출부가 깨진다(AttributeError/빈 텍스트).**
- Sonnet 5는 **`thinking` 미지정 시 adaptive thinking이 기본 ON**이다(4.6은 미지정=off). 즉 **모델ID만 바꾸면 우리가 원치 않아도 thinking이 켜지고**, 위 파싱이 깨지며, 작은 `max_tokens`(예: `quick_exit_check`=220)는 thinking이 다 먹어 잘림/빈응답이 된다.
- Sonnet 5는 **`temperature`/`top_p`/`top_k` 비기본값이 400 에러**다. 저장소에 `temperature=0`이 3곳(shadow/도구)에 있어 그대로 두면 400.
- Sonnet 5는 **새 토크나이저(같은 텍스트 ~+30% 토큰)** — 비용·`max_tokens`·`count_tokens` 기준 재산정 필요. 스티커 가격은 4.6과 동일($3/$15)이나 **도입가 $2/$10(2026-08-31까지)**.
- **결론**: 단계적으로 간다. (1)무해 준비(응답추출 헬퍼·temperature 제거·max_tokens 헤드룸·가격) → (2)shadow/도구부터 교체 → (3)국면 분석 R1 교체+thinking → (4)live 진입/청산 게이트는 **thinking 선택적**(latency·timeout 제약). "단타 아니라 느려도 OK"는 대부분 참이지만 **`buy_time_confirm`(2.5초 하드 타임아웃)·`quick_exit`(8초)는 예외**로 다뤄야 한다.

---

## 1. 현재 Claude 사용 전수 (기능별 리스트)

모델 소스는 두 곳이 일치해야 반영: `.env.live` + `config/v2_start_config.json`(env_overrides). 코드 기본값은 fallback.

### 1-A. env로 모델이 결정되는 항목 (config/v2_start_config.json 현재값)

| env 키 | 현재 모델 | 사용처(역할) | 5.0 목표 |
|---|---|---|---|
| `SINGLE_SYMBOL_JUDGE_MODEL` | `claude-opus-4-8` | 단일종목 진입 판단(live) | **유지**(최신) |
| `R1_MODEL` | `claude-haiku-4-5-20251001` | 애널리스트 R1 기본 | Haiku 5 없음 → 결정필요 |
| `BULL_R1_MODEL` | `claude-sonnet-4-6` | 강세 국면 R1 | → `claude-sonnet-5` |
| `BEAR_R1_MODEL` | `claude-haiku-4-5-20251001` | 약세 국면 R1 | Haiku 5 없음 → 결정필요 |
| `NEUTRAL_R1_MODEL` | `claude-haiku-4-5-20251001` | 중립 국면 R1 | Haiku 5 없음 → 결정필요 |
| `ANTHROPIC_MODEL`(전역 기본) | `claude-sonnet-4-6` | 아래 다수 모듈의 기본값 | → `claude-sonnet-5` |

> 참고(메모리): BULL_R1을 sonnet5로 바꾸려던 핸드오프가 있었고 "thinking=disabled 선행"이 조건으로 적혔다. 본 설계가 그 "선행 조건"을 구체화한 것이다.

### 1-B. `ANTHROPIC_MODEL`(기본 sonnet-4-6)을 쓰는 코드 호출부

| 파일:라인 | 라벨/역할 | live? | max_tokens | 지연민감 | 응답추출 |
|---|---|---|---|---|---|
| `minority_report/analysts.py` R1 2077 | 애널리스트 R1(국면별 override) | live | 700 | 중 | `content[0].text` |
| `minority_report/analysts.py` R2 2214 | 애널리스트 R2(MODEL=sonnet) | live | 900 | 중 | `content[0].text` |
| `minority_report/analysts.py` selection 3304 | selection 승격 | live | 동적 | 중 | `content[0].text` |
| `minority_report/analysts.py` retry 3471 | selection 재시도 | live | 1800 | 중 | `content[0].text` |
| `minority_report/hold_advisor.py` challenge 929 | 청산 챌린지 투표 | live | 동적 | **고** | `content[0].text` |
| `minority_report/hold_advisor.py` triage 1186 | 청산 트리아지 | live | 동적 | **고** | `content[0].text` |
| `minority_report/hold_advisor.py` 1769 | (보조) | live | 700 | 고 | `content[0].text` |
| `execution/single_symbol_judge.py` 380 | 진입 판단(opus) | live | 900 | **고** | `content[0].text` |
| `execution/buy_time_confirm_judge.py` 173 | 매수 타이밍 확인 | live | 700 | **고(2.5s 타임아웃)** | `content[0].text` |
| `minority_report/quick_exit_check.py` 112 | 소프트 청산 체크 | live | **220** | **고(8s)** | `content[0].text` |
| `minority_report/postmortem.py` 717 | 사후분석(비실시간) | batch | 2500 | 저 | `content[0].text` |
| `minority_report/tuner.py` 236 | 파라미터 튜너 | batch | 500 | 저 | `content[0].text` |
| `strategy/param_tuner.py` 470 | 파라미터 튜너 | batch | 1200 | 저 | `content[0].text` |
| `preopen/continuation_shadow.py` 1847 | 연속성 shadow | shadow | 1200 | 저 | **for block(정상)** + `temperature=0` |
| `phase1_trainer/historical_sim.py` 143/214 | 과거 시뮬 | offline | 512 | 저 | `content[0].text` |
| `phase1_trainer/sector_play.py` 238/389 | 섹터 플레이 | offline | 200 | 저 | `content[0].text` |
| `dashboard/dashboard_server.py` 9816 | 환율/숫자 파싱(haiku 하드코딩) | 대시보드 | **20** | 저 | `content[0].text` |
| `tools/*`(fasttrack, tier1_*, prompt_eval, hold_advisor_ab, preopen_replay, simulate_*) | 분석 도구 | 오프라인 | 다양 | 저 | 대부분 `content[0].text`, 일부 `for block` |

**핵심 관찰**
- **응답추출**: 프로덕션 live 경로는 전부 `resp.content[0].text`. 정상 `for block in resp.content` 패턴은 `continuation_shadow.py:1854`, `tools/preopen_candidate_replay.py:664` 두 shadow/도구뿐.
- **temperature=0**: `preopen/continuation_shadow.py:1850`, `tools/preopen_candidate_replay.py:659`, `tools/simulate_hold_advisor_decision_modes.py:450`.
- **budget_tokens/thinking**: 저장소에 **하나도 없음**(현재 thinking 미사용 확정). `hold_advisor`의 `"token_budget"`는 API 파라미터가 아니라 raw-call 로깅 메타데이터임(무관).
- **지연 하드제약**: `buy_time_confirm` 2.5초(ThreadPool 하드 타임아웃→`unavailable`), `quick_exit_check` 8초 클라이언트 timeout.
- **비용추적**: `credit_tracker.py`가 모델명 substring("sonnet"/"haiku"/"opus")로 단가 매핑. `claude-sonnet-5`는 "sonnet" 포함 → 라우팅은 되지만 단가는 4.6 기준($3/$15) 그대로.

---

## 2. 교체 매핑 (4.x → 5.0)

| 현재 | 목표 | 근거 |
|---|---|---|
| `claude-sonnet-4-6` | `claude-sonnet-5` | Sonnet 5가 코딩/에이전틱에서 4.6 상회, 도입가 저렴 |
| `claude-haiku-4-5-20251001` | **(Haiku 5 부재)** | 최신 Haiku가 4.5 — §8 결정 |
| `claude-opus-4-8` | 유지 | 이미 최신 Opus |

> **모델ID 규칙**: 5.0은 날짜 접미사 없음. 정확히 `claude-sonnet-5`. `claude-sonnet-5-xxxxxx`로 쓰면 404.

---

## 3. 교체 시 문제점 (블로커 B / 리스크 R)

### ★B1. 응답추출 `content[0].text` — thinking 도입 시 전 호출부 붕괴 (최우선)
- Sonnet 5는 `thinking` 미지정 시 **adaptive thinking 기본 ON**. thinking이 켜지면 `resp.content`의 **첫 블록이 thinking 블록**이고 `content[0].text`는 존재하지 않아 **AttributeError**(또는 `display:"omitted"` 기본이라 빈 문자열) → 우리 `extract_json`이 "JSON 파싱 실패"로 던짐 → 모든 live 판단이 안전 폴백(HOLD/unavailable)로 떨어짐.
- **즉, 모델ID만 sonnet-5로 바꿔도(=thinking 미지정) 파싱이 조용히 전멸한다.** thinking을 원하든 안 원하든 이 지점은 반드시 다뤄야 한다.
- **해결(둘 중 택1)**:
  - (a) **응답추출을 `for block in resp.content: if block.type=="text"` 로 리팩터**(권장, thinking 병행 가능). 공통 헬퍼 1개로 전 호출부 교체.
  - (b) thinking을 **명시적으로 `{"type":"disabled"}`**로 꺼서 `content[0]`이 다시 text가 되게 유지(응답추출 무변경). 단 이러면 "thinking 도입" 목표와 상충.

### B2. `temperature=0` → 400
- Sonnet 5/Opus 4.8은 비기본 sampling 파라미터를 거부(400). `temperature=0`은 기본값(1.0)이 아니므로 **거부**. 해당 3파일에서 `temperature` **제거** 필요(프롬프트로 결정성 유도). opus-4-8을 쓰는 `single_symbol_judge`는 현재 temperature 미설정이라 무관.

### ★B3. `max_tokens` 헤드룸 — thinking이 잡아먹어 잘림
- thinking 토큰은 `max_tokens` 안에서 소비된다(응답과 공유). 작은 값이 위험:
  - `dashboard 20`, `sector_play 200`, `quick_exit_check 220`, `tuner 500`, `historical_sim 512`, `buy_time_confirm 700`, `analyst R1 700` …
- thinking ON에서 `quick_exit=220`은 거의 전부 thinking에 먹혀 **빈/잘린 JSON** → `claude_utils.extract_json`이 "max_tokens 부족" ValueError. **thinking을 켜는 호출부는 max_tokens를 대폭 상향**하거나 그 호출부는 thinking off 유지.

### ★B4. Latency / 하드 타임아웃
- thinking은 호출당 지연을 크게 늘린다. "단타 아님"이라 대부분 허용되나 **하드 제약 있는 곳은 예외**:
  - `buy_time_confirm` 2.5초 → thinking이면 초과 확실 → 매수 타이밍 게이트가 **상시 timeout→unavailable**(매수 흐름 손상).
  - `quick_exit_check` 8초 → thinking이면 초과 위험.
  - `single_symbol_judge`/`hold_advisor`는 하드 타임아웃은 없으나 live 진입/청산이라 체감 지연↑.
- **처방**: 이 호출부들은 thinking off 유지하거나, thinking 켤 경우 timeout을 현실값으로 대폭 상향(운영자 승인 파라미터 — CLAUDE.md 확인필수 목록의 "슬리피지/최소거리"와 같은 등급으로 취급).

### B5. Haiku 5 부재
- R1/BEAR/NEUTRAL 3자리가 Haiku 4.5. "다 5로"를 문자 그대로는 불가. §8 결정.

### B6. 새 토크나이저 (~+30% 토큰)
- 같은 프롬프트가 sonnet-5에서 토큰 ~30%↑ → (i) `max_tokens` 재기준, (ii) 비용 재산정, (iii) 프롬프트 캐시 최소토큰 문턱 재확인(sonnet-5 캐시 최소 2048토큰), (iv) `count_tokens`는 모델별로 다시 재야 함(과거 4.6 수치 재사용 금지).

### B7. thinking.display / silent 기본 변경
- Sonnet 5 `thinking.display` 기본 `"omitted"`(4.6은 "summarized"). thinking 블록 텍스트가 빈 문자열로 옴. 우리는 thinking 텍스트를 UI/로그에 안 쓰므로 무해하나, raw_call 로깅에 thinking을 남기려면 `display:"summarized"` 명시 필요(선택).

### B8. credit_tracker 단가
- sonnet-5 도입가 $2/$10(2026-08-31까지) 미반영(현재 $3/$15로 계상 → 도입기 **과다 계상**, 보수적이라 위험은 아님). opus 단가는 코드가 $15/$75인데 실제 opus-4-8은 $5/$25 → **opus 과다 계상**(별개 기존 오기). 비용 대시보드 재기준 시 함께 정정 권장.

### B9. 프롬프트 리튜닝 (기존 위생 이슈와 겹침)
- Sonnet 5/Opus 계열은 지시를 **더 문자 그대로** 따른다. 강한 "CRITICAL/MUST" 문구는 과발동. 우리 프롬프트엔 이미 메모리에 적힌 위생 결함(reward_risk 앵커, 워크드 예시 RR위반, 목표가 과대)이 있다 → 모델 교체는 이 문자적 해석을 **증폭**할 수 있다. 교체와 프롬프트 위생수정을 **한 패치에 섞지 말 것**(selection/execution 분리 원칙과 동일). 교체 후 동일 프롬프트로 먼저 재기준을 잡고, 위생수정은 별도 트랙.

---

## 4. thinking 도입 설계 (핵심 결정)

운영자 방침: "thinking 쓰면 느리지만 단타 아니니 OK." → **원칙적으로 도입하되, 하드 지연제약 호출부는 예외**로 티어링한다.

### 4-A. 파라미터 형태 (5.0 계열 공통)
- thinking ON: `thinking={"type":"adaptive"}` (+선택 `"display":"summarized"`). **budget_tokens는 400** — 쓰지 않는다.
- 깊이 제어: `output_config={"effort": "low|medium|high|xhigh|max"}` (기본 high). thinking과 조합.
- thinking OFF(명시): `thinking={"type":"disabled"}` (Sonnet 5/Opus 4.8 허용).

### 4-B. 호출부별 thinking 티어 (권장 초안)

| 티어 | 호출부 | thinking | effort | 이유 |
|---|---|---|---|---|
| T1 도입 | `postmortem`, `tuner`, `param_tuner`, `historical_sim`, `sector_play`, 분석 `tools/*` | adaptive | medium~high | 비실시간·오프라인, 지연 무관, 품질 이득 |
| T2 도입(관찰) | 애널리스트 R1/R2/selection, `continuation_shadow` | adaptive | medium | live지만 하드 타임아웃 없음. max_tokens 상향 선행 |
| T3 신중 | `hold_advisor`(triage/challenge), `single_symbol_judge` | **초기 off** | — | live 청산/진입. 먼저 sonnet-5 무thinking로 재기준, shadow 검증 후 T2 승격 |
| T4 off 유지 | `buy_time_confirm`(2.5s), `quick_exit_check`(8s/220토큰), `dashboard`(20토큰 숫자파싱) | **disabled** | — | 하드 타임아웃/극소 max_tokens — thinking 부적합 |

> **응답추출 리팩터(B1 해결 a)는 티어와 무관하게 전 호출부 선행 필수** — thinking을 켜는 순간을 대비. off 유지 호출부도 헬퍼로 통일해두면 미래에 안전.

### 4-C. effort 스윕
- 오프라인 T1에서 low/medium/high를 자체 평가셋으로 스윕 → 계약충족·방향일치·토큰·지연 트레이드오프 확인 후 티어별 고정.

---

## 5. 최종 개선 방안 (단계별, "설계문서대로 하면 문제없게")

각 단계는 `py_compile` + 관련 pytest + `tools/check_mojibake.py --staged` + live preflight 통과를 전제(CLAUDE.md 커밋계약). 모델/토글 변경은 **`.env.live` + `config/v2_start_config.json` 양쪽** 동시.

### Phase 0 — 무해 준비 (매매 무변경, 코드/설정 정지작업)
목표: 5.0 전환의 **필요조건**을 미리 깔되 동작은 그대로 유지.
1. **공통 응답추출 헬퍼** 신설(예: `claude_utils.extract_text_blocks(resp)`): `resp.content`를 순회해 `type=="text"` 텍스트만 concat. 전 호출부의 `resp.content[0].text` → 헬퍼로 교체. (thinking off 상태에서도 결과 동일 = 무해)
2. **temperature 제거**(3파일). 현재 모델(4.6/opus)에서도 무해(0은 결정성 의도였을 뿐).
3. **max_tokens 헤드룸 상향**을 env로 조정 가능하게(대부분 이미 env). thinking 켤 호출부 후보의 하한을 문서화(예: thinking시 최소 1500~2000).
4. **credit_tracker** 단가 env로 재정의 가능(이미 env override 지원). 도입가 $2/$10·opus $5/$25 반영은 env로.
5. `py_compile`+pytest 전체 green 확인. **커밋 1(무해 준비)**.

### Phase 1 — 오프라인/도구 교체 (T1)
1. 오프라인·도구 호출부(`postmortem`/`tuner`/`param_tuner`/`historical_sim`/`sector_play`/`tools/*`)를 `sonnet-5`로. thinking adaptive+effort medium.
2. 각 도구를 과거 데이터로 재실행 → §6 A/B로 4.6 대비 출력 차이·유효율 확인. **커밋 2**.

### Phase 2 — 국면 분석 R1/R2 교체 (T2, shadow 관찰)
1. `BULL_R1_MODEL` → `sonnet-5`(운영자 핸드오프의 원래 의도). **NEUTRAL/BEAR는 §8 결정 전까지 haiku-4-5 유지**.
2. R2/selection의 `ANTHROPIC_MODEL` → `sonnet-5`. max_tokens 헤드룸 반영. thinking adaptive medium.
3. 재시작 후 **raw_call 로그로 계약충족율·방향일치·토큰·지연 실측 누적**(1~2 국면). shadow와 병행.

### Phase 3 — live 진입/청산 게이트 (T3/T4, 신중)
1. T3(`hold_advisor`, `single_symbol_judge`는 이미 opus-4-8 유지): **먼저 thinking off로 모델만 안정화**(single_symbol은 무변경, hold_advisor는 sonnet-5 무thinking). 재기준 후에만 thinking 승격 검토.
2. T4(`buy_time_confirm`/`quick_exit`/`dashboard`): 모델은 올리되 **thinking disabled 명시**. buy_time timeout(2.5s)·quick_exit(8s)는 무thinking sonnet-5의 실측 지연으로 재검증(초과 시 운영자 승인하 상향).
3. 각 단계 커밋 분리, 롤백 지점 유지.

### 설정 변경 요약(config/v2_start_config.json + .env.live 동시)
- `ANTHROPIC_MODEL`: `claude-sonnet-4-6` → `claude-sonnet-5`
- `BULL_R1_MODEL`: `claude-sonnet-4-6` → `claude-sonnet-5`
- `R1_MODEL`/`BEAR_R1_MODEL`/`NEUTRAL_R1_MODEL`: §8 결정
- `SINGLE_SYMBOL_JUDGE_MODEL`: 유지
- 신규 토글(제안): `CLAUDE_THINKING_ENABLED`(전역 킬스위치), 호출부별 `*_THINKING`·`*_EFFORT`·`*_MAX_TOKENS`·`*_TIMEOUT_MS`.

---

## 6. 항목별 성능 점검 방법 (요청: "업데이트 시 성능차이 점검 + 변화가 좋은지 확인")

### 6-A. 오프라인 A/B (재기준, live 무영향) — 1순위
- 동일 프롬프트 N건(국면/시장 분리, 평균 뒤 분포까지 — CLAUDE.md "평균의 오류" 준수)을 **4-way**로 호출:
  - (1) 4.6 무thinking(현행 기준선) / (2) sonnet-5 무thinking / (3) sonnet-5 thinking-adaptive / (4) sonnet-5 thinking+effort 스윕.
- **지표(항목별)**: ① JSON 유효율(파싱 성공%) ② 스키마/결정계약 충족율 ③ 방향 일치율(같은 입력에 같은 액션?) ④ output/thinking 토큰 ⑤ latency(p50/p95) ⑥ 비용/호출. 
- 산출: `docs/reports/`에 항목별 표. **"변화가 좋은가"의 1차 판정은 net이 아니라 계약충족·파싱·지연·일관성**(net은 6-C).

### 6-B. 라이브 shadow 병행 (Phase 2~3)
- 교체 호출부는 raw_call 로그(`logs/raw_calls/`, `data/audit/agent_call_events.db`)에 모델·토큰·지연·파싱에러·request_id가 이미 남는다 → **국면별 누적**으로 4.6 기간 대비.
- 기존 shadow 배관(net_profitability_review, capture 등)과 결합해 **동일 결정에서 old/new 신호 차이**를 측정.

### 6-C. net 판정 (최후·신중)
- 메모리 반복 교훈: **forward≠net, MFE-enforce 금지, 분봉 replay/실거래로만 net 판정.** 모델 교체의 net 효과는 **selection 무알파** 전제상 크지 않을 가능성 — 교체는 "예측 향상"이 아니라 **위생/일관성/파싱 안정성** 관점 이득으로 평가하는 게 정직하다. net은 실거래 누적으로만, 국면 분리해 확인.
- **판정 기준(제안)**: 계약충족·파싱 유효율이 기준선 이상 + 지연이 하드제약 내 + shadow 방향 일관성 유지 → 유지. 하나라도 악화면 해당 호출부 롤백.

---

## 7. 롤백
- 모든 변경은 env 토글/모델ID로 되돌림 가능(`.env.live`+config 양쪽 원복). thinking은 `*_THINKING=disabled`/전역 킬스위치로 즉시 off. 응답추출 헬퍼는 thinking off에서도 동일 동작이라 잔존 무해.

---

## 8. 운영자 결정 필요 항목 (CLAUDE.md "애매하면 운영자가 고른다")

1. **Haiku 3자리(R1/BEAR/NEUTRAL) 처리**: (a)Haiku 4.5 유지(5 없음, 비용/속도 유지) / (b)Sonnet 5로 승격(품질↑, 비용 $1/$5→$2~3/$10~15, 지연↑) / (c)일부만(예: NEUTRAL만 sonnet-5).
2. **thinking 범위**: 본 설계의 T1~T4 티어 채택 여부. 특히 live 진입/청산(T3)·하드타임아웃(T4)에 thinking을 어디까지 허용할지.
3. **오프라인 A/B(6-A) 실행 승인**: 소량 live API 호출(비용 수 센트)로 4-way 비교를 지금 돌릴지 — 돌리면 항목별 실측 표를 바로 제시.

> 위 결정 전까지는 **어떤 모델/토글도 무단 변경하지 않는다**(운영자 확인 필수 파라미터 규정 준수).

---

## 9. 운영자 결정 반영 + 실측 A/B 결과 (2026-07-02)

### 9-A. 운영자 결정
1. **Haiku 3자리 → 전부 `claude-sonnet-5` 승격** (R1/BEAR/NEUTRAL 애널리스트 경로에서 Haiku 제거).
2. **thinking 최대 도입** (오프라인 포함 넓게). 단 물리 제약 호출부는 실측(9-B)으로 판정.
3. **4-way 오프라인 A/B 지금 실행** → 완료(아래).

### 9-B. 실측 (실제 raw_call 프롬프트 3건 재생, raw HTTP, n=1/라벨)
결과 원본: `docs/reports/model5_ab_20260702.json`. **주의: 라벨당 n=1 — 지연·토큰·파싱·잘림 등 "기계적 사실"은 신뢰, "판단이 더 좋은가"는 미결(표본 부족·평균의오류).**

| 샘플(원모델) | 변형 | 지연 | out_tok | thinking | JSON | 방향 |
|---|---|---|---|---|---|---|
| neutral_r1 (haiku) prod_max=700 | A 원본 무think | 4.9s | 313 | - | ✅ | MILD_BULL |
| | B sonnet5 무think | 6.8s | 267 | - | ✅ | MILD_BULL |
| | C sonnet5 think-high | **21.5s** | **1639** | Y | ✅ | MILD_BULL **(700토큰서 잘림)** |
| | D sonnet5 think-med | 6.2s | 206 | - | ✅ | MILD_BULL |
| bull_r2 (sonnet) prod_max=900 | A 원본 무think | 7.5s | 402 | - | ✅ | MILD_BULL |
| | B sonnet5 무think | 6.4s | 324 | - | ✅ | MILD_BULL |
| | C sonnet5 think-high | 9.9s | 698 | Y | ✅ | MILD_BULL |
| | D sonnet5 think-med | 5.3s | 287 | - | ✅ | MILD_BULL |
| single_symbol (opus4.8) prod_max=900 | A 원본 무think | 4.4s | 195 | - | ✅ | WAIT_RECHECK |
| | B sonnet5 무think | 5.6s | 291 | - | ✅ | WAIT_RECHECK |
| | C sonnet5 think-high | 12.2s | 855 | Y | ✅ | WAIT_RECHECK |
| | D sonnet5 think-med | 5.4s | 333 | Y | ✅ | WAIT_RECHECK |
| | **E opus4.8 think-high** | 11.5s | 835 | Y | ✅ | **PULLBACK_WAIT(≠)** |

### 9-C. 실측이 확정한 사실
1. **★응답추출 리팩터는 필수이자 충분**: A/B 하네스는 `type=="text"` 블록만 모으는 방식이라 thinking 변형(C/D/E)도 **JSON 100% 유효**. 반대로 현행 프로덕션 `content[0].text`였다면 C/D/E는 전부 깨졌을 것. → **헬퍼 리팩터만 하면 thinking이 실제로 동작**함이 실증됨.
2. **Sonnet 5 무thinking(B) = 안전한 drop-in**: 3샘플 모두 방향 동일, 지연·토큰 유사(때로 더 적음). 저위험 1차 이행 경로.
3. **effort=medium(D) ≈ 무thinking 지연**(5~6s), out_tok 206~333 → **작은 max_tokens에도 안 잘림**. thinking 켤 live 티어의 **기본 effort는 medium**이 정답.
4. **effort=high(C)는 지연 12~21s + 작은 max_tokens서 잘림**(R1 1639>700). → **오프라인/큰 max_tokens 티어 한정**.
5. **토크나이저**: 입력토큰 +12~13%(haiku·sonnet4.6→sonnet5). ~+30%는 상한, 실측은 더 낮으나 재기준 필요.
6. **★opus 진입판단 + thinking은 결정을 바꿈**(E: WAIT_RECHECK→PULLBACK_WAIT). live 진입 게이트에 thinking을 켜면 **행동이 달라짐** → n=1이라 좋다/나쁘다 판정 불가. **shadow로 방향분포 검증 전 blind-on 금지.**

### 9-D. 최종 권장 설정 (실측 반영)
- **응답추출 헬퍼 리팩터 = Phase 0 필수 선행**(이게 없으면 thinking 도입 자체가 불가).
- **thinking effort 기본 = medium**(live 티어). high는 오프라인(T1)·postmortem 등 큰 max_tokens 전용.
- **thinking 켜는 호출부 max_tokens 하한 ≥ 1500**(high는 ≥2500). 현행 700/900은 medium이면 OK, high면 상향 필수.
- **하드타임아웃 게이트**:
  - `quick_exit_check`(8s): thinking-medium(5~6s) **가능하나 여유 적음** → timeout 12s 상향 권장 후 medium.
  - `buy_time_confirm`(2.5s): medium도 5~6s로 **초과** → thinking 도입 시 **timeout을 8~10s로 상향(운영자 승인 파라미터)** 필수, 아니면 off 유지.
  - `dashboard`(20토큰 숫자파싱): thinking 불필요 → **off 유지**.
- **`single_symbol_judge`(opus 진입)**: thinking-on이 결정을 바꿈 → 먼저 **shadow(thinking-off vs on 병행 로깅)로 방향분포·net 확인** 후에만 승격. 초기 off.
- **비용**: sonnet-5 도입가 $2/$10(8/31까지)·opus-4-8 $5/$25를 credit_tracker env로 반영.

### 9-E. "변화가 좋은가" 정직한 판정
- **좋다(확정)**: 파싱 안정성(리팩터 시 100%), sonnet-5 무thinking 무해 이행, effort-medium의 지연/토큰 경제성.
- **미결(표본부족)**: thinking이 *판단 품질*을 올리는가 — 방향은 대부분 보존되나 opus 진입에서 1건 변화. **selection 무알파 전제상 thinking의 net 이득은 작을 가능성**(메모리 반복 교훈). thinking의 진짜 값은 "예측 향상"보다 **일관성·파싱 안정·근거 품질**로 보는 게 정직. net은 shadow/실거래 국면분리 누적으로만.
- **판정 기준**: 계약충족·파싱 유효율 ≥ 기준선 + 지연 하드제약 내 + shadow 방향 일관 → 채택. 하나라도 악화 시 해당 호출부 롤백.
