# 토큰 사용량 실측 리뷰 — 규모 판정 + 감축 레버 (2026-07-02)

분석만. 코드·config·env 무변경. 모델/thinking/env는 운영자 확인 필수 파라미터라 이 문서는 **개선 후보 랭킹**까지만 낸다.

소스: `state/live_api_usage.json`(집계·모델별·일별), `data/audit/agent_call_events.db`(경로별 실측, read-only 조회).

---

## 1. 규모 판정 — "많은지 적은지"

| 축 | 실측 |
|---|---|
| 최근 14일(6/16~7/2) | 3,214 calls · in 12.9M / out 2.1M tok · **$83.16 (약 11.2만원)** |
| 하루 평균 | 약 **$5.94** (~250 calls) |
| 월 환산(활동일 ~22) | **약 $178 / 24만원** |
| 누적(lifetime) | $257 (sonnet-4-6 $205=80% · opus $27.6 · haiku $5.4) |
| in/out 비율 | **6.2x** (input이 압도 — 캐싱이 유효한 구조) |

**판정: 절대 규모는 크지 않다(월 ~24만원).** 실거래 시스템 기준 과다 아님. 단 아래 2가지가 다음 청구서를 키운다:
- **⚠️ 현재 데이터는 sonnet-5 업그레이드 재시작 *이전* 기준**(라벨이 전부 `sonnet-4-6`). 5.0 업그레이드가 analyst/hold/selection/오프라인에 **thinking ON(medium)**을 켰다 → 재시작 후 output 토큰(현재 out 2.1M/14d)이 지배 경로에서 2~3배로 뛸 가능성. **이게 진짜 증가 요인이고 품질과 맞물린다.**
- 재시작 전/후가 깨끗한 before/after 측정 기회다.

---

## 2. 경로별 분해 (agent_call_events, 최근 14일, in+out)

| 경로 | 점유율 | calls | in tok | out tok | thinking(5.0후) |
|---|---:|---:|---:|---:|---|
| **select_tickers** | **41.6%** | 718 | 4.05M | 1.02M | ON |
| **analyst_debate** (bull/bear/neutral × r1/r2 = 6call) | **39.7%** | 967 | 4.46M | 0.36M | ON(r1) |
| single_symbol_judge (진입) | 7.7% | 275 | 0.85M | 0.08M | OFF |
| hold_advisor | 5.5% | 347 | 0.56M | 0.11M | ON |
| hold_tuner (tune_*) | 3.9% | 257 | 0.40M | 0.08M | 오프라인 |
| postmortem / param_tuner | 1.6% | 59 | 0.13M | 0.06M | 오프라인 |

- **상위 2개(select_tickers + analyst_debate)가 토큰의 81%.** 나머지는 꼬리.
- 시장: US 7.5M tok / KR 4.66M tok (US가 1.6배).
- analyst r1 3인(bull/bear/neutral) input이 거의 동일(1.01~1.06M) → **동일 debate 내 공유 컨텍스트가 큼**.
- (DB는 raw_call_logger 경유 호출만 인덱싱 → JSON 총량 대비 ~75% 커버. 상대 점유율 판정엔 충분, 절대치는 JSON이 truth.)

---

## 3. 캐시 현황 — 이미 켜져 있으나 부분적으로만 landing

- config/.env.live 둘 다 `SELECTION_PROMPT_CACHE_ENABLED=true`, `HOLD_ADVISOR_PROMPT_CACHE_ENABLED=true`.
- 최근 캐시 landing = input의 **약 7~13%** (lifetime 4%는 캐싱 도입 전 구데이터에 희석된 값 — 함정).
- select_tickers: 정적 계약/스키마/규칙 블록만 1h TTL 캐시(`analysts.py:3044`). 나머지 대부분(후보 풀 텍스트·digest)은 매 호출 변동 → 본질적으로 캐시 불가. **여기서 더 짜낼 여지 작음.**
- **analyst_debate: 캐싱 아예 없음(cache_control 미적용).** 게다가 프롬프트가 `persona`(analyst별 상이)를 **맨 앞**에, 큰 공유 본문(계약+brain+digest)을 뒤에 둔다(`analysts.py:2052`, `2180`) → 3 analyst가 **공통 캐시 프리픽스를 못 만든다**. 구조적 미스.

---

## 4. 감축 레버 랭킹 (품질리스크 표시)

### L1. thinking 범위 재검토 — 최대 레버, 품질 민감 (운영자 판단)
- 재시작 후 output 폭증의 진원. `analyst_r1`은 **stance/confidence/1문장**만 반환(max_tokens 700)하는 빠른 판단인데 thinking medium이 붙는다 → 짧은 구조화 출력에 reasoning 비용이 과할 수 있음.
- 이미 경로별 토글 존재: `CLAUDE_THINKING_<SCOPE>`, `CLAUDE_EFFORT_<SCOPE>`. 코드 변경 없이 env만으로 조절 가능.
- **권고: 재시작 후 thinking ON/OFF before/after를 실측**하고, r1(과 selection)에 대해 thinking OFF 또는 effort=low가 품질을 안 깎는지 확인 후 결정. 지금 맹목적으로 끄지 말 것(품질 목적으로 켠 것).

### L2. analyst_debate 프리픽스 캐싱 — 품질 중립, 코드 리팩터 필요
- 프롬프트 재구성: 공유 본문(계약+market_guide+brain+correction+digest)을 캐시된 system 블록 프리픽스로, `persona`+본인 feedback을 suffix로. → debate 내 2·3번째 analyst가 공유 본문을 캐시에서 읽음(~90% off).
- 규모: r1 input 3.1M/14d 중 공유분 상당 → 대략 input 1.5~2M/14d 절감 ≈ **$5~10/월**. 모델 출력·품질 무변경(동일 텍스트, 순서만).
- 비용: 실제 코드 수정 + 테스트. r2도 동형 적용 가능.

### L3. analyst r2 라운드 필요성 — 품질 질문 (운영자)
- debate 6콜 중 r2×3이 40% 중 상당. r2가 결정 품질에 실질 기여하는지(합의/반전 유발 빈도) 측정 후, 조건부 실행(r1 합의 강하면 r2 스킵)이면 큰 절감. **순수 품질 판단 → 운영자.**

### L4. select_tickers 페이로드 슬림 — 품질 민감, 효과 제한
- 후보 풀 크기·digest limit이 input의 대부분. 캐시 불가분. 후보 수/digest 길이 축소는 selection 품질 직결이라 신중. 지금 건드리지 말 것(스크리너 리랭킹 backfire 전력과 별개지만 입력 축소도 품질축).

---

## 5. 하지 말 것 / 제약

- 모델 다운그레이드(sonnet-5→haiku 재강등 등)·thinking 전역 OFF는 **품질 목적 업그레이드를 되돌리는 것** → 운영자 승인 없이 금지, 측정 없이 금지.
- 캐시 이미 ON인 걸 "껐다 켜서" 개선하는 착각 금지(select_tickers는 이미 최대치 근처).
- 비용 환산 단가는 `credit_tracker.py` 실측 단가 사용(sonnet $3/$15, opus $5/$25, haiku $1/$5). 기억으로 추정 금지.

---

## 6. 운영자 결정 대기 항목

1. **L1(재시작 후 thinking before/after 측정)** — 착수 승인? (측정은 매매 무변경, 코드 무변경)
2. **L2(analyst_debate 프리픽스 캐싱 리팩터)** — 품질 중립·$5~10/월. 구현 지시?
3. **L3(r2 기여도 측정)** — 측정 착수?

우선순위 권고: **재시작 → L1 실측(진짜 비용 그림 확정) → L2 구현 → L3 측정** 순.

---

## 7. 재시작 후 실측 (2026-07-02 09:03~, sonnet-5 + thinking 라이브)

봇 재시작 08:59:23 KST. config/env 모두 sonnet-5 해석 확인, `CLAUDE_THINKING_ENABLED=true`. KR 개장 첫 세션 표본으로 before/after 시장 매칭 비교.

**⚠️ 표본 얇음(첫 세션): select_tickers steady n=3, analyst 각 n=1. 예비 결과 — 수 세션 누적 후 확정.**

| label | 지표 | before (KR sonnet-4-6) | after (KR sonnet-5+thinking) | Δ |
|---|---|---:|---:|---|
| **select_tickers** | output tok | 1,089 | **2,600** | **+139%** |
| | duration | 18.5s | 23.8s | +29% |
| | input tok | 5,103 | 7,543 | +47% |
| analyst_bull_r1 | output tok | 278 | 231 | **-17%** |
| | duration | 8.1s | 5.8s | -29% |
| analyst r2 (3인) | output tok | 414~441 | 325~674 | 대체로 평탄(neutral만 outlier) |

(09:03 첫 select 1건은 in=17,206/개장 대형풀 아웃라이어 → steady 3건만 사용.)

### 확정 발견
1. **thinking 비용은 select_tickers에 집중.** output이 2배(+139%). select는 토큰 1위 경로(41.6%)라 영향 큼.
2. **analyst debate는 thinking으로 안 커짐.** r1은 오히려 output↓·duration↓(sonnet-5가 더 lean). r2도 평탄. → analyst thinking은 비용 무해(효과는 별개 질문).
3. select thinking scope override 없음 → 전역 기본(thinking ON, effort=medium) 사용.
4. select **input +47%는 thinking과 무관**(input은 thinking 영향 없음) — 개장 풀 크기인지 프롬프트 변경인지 별도 확인 필요(input은 $3/M로 싼 쪽).

### 비용 추산 (select thinking)
- select ~51콜/일(14일 718콜). thinking 순수기여(output +1,511/콜) ≈ **+$1.16/일, 월 ~$26**. input 증가분 포함 시 ~+$1.54/일.
- 현 하루 base ~$6 대비 select thinking만으로 ~+20%.

### 새 레버 (L1 실측 결과 → 운영자 판단)
- **`CLAUDE_THINKING_SELECTION=off` 또는 `CLAUDE_EFFORT_SELECTION=low`** — env만, 코드 무변경. select output 약 절반으로 되돌림(월 ~$26 절감).
- **단 selection thinking의 품질 효과는 미측정.** selection은 히스토리상 무알파(예측 안 됨) → thinking 2배 비용 대비 품질 이득 불확실 → **AB/shadow 강력 후보**. 맹목 OFF 금지, 맹목 유지도 근거 없음.
- analyst thinking은 비용 무해라 손댈 이유 없음(끄면 품질만 손해 가능).

### 미확정/제약
- 표본 첫 세션. 3~5 세션(KR+US) 누적 후 재측정 필요.
- 비용 단가는 tracker의 sonnet $3/$15 사용 — **sonnet-5 실단가 별도 확인 필요**(claude-api 스킬).
- select input +47%의 정체(풀 vs 프롬프트) 미확인.

---

## 8. ⚠️ 회귀 발견 — thinking이 select 50% 절단, 매수 기계적 억제 (2026-07-02)

AB 설계 중 발견. **비용 문제가 아니라 correctness 문제.**

### 사실
- 프로덕션 select max_tokens = `SELECTION_OUTPUT_COMPRESSION_ENABLED=true` → `CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS` = **2600**(config env_overrides 우선; .env.live엔 2200으로 불일치).
- thinking이 출력 ~2176토큰을 먹어 JSON 예산 잠식 → `stop_reason=max_tokens` → `selection_truncated` fallback → **tr(매수) 강제 []**.
- 오늘 KR select **14건 중 6~7건(43~50%)이 절단**, 절단 콜 out은 전부 2600(캡).
- **before/after 회귀**: select output 중앙 1690→2598, 절단율 **1% → 50%**.

### 함의
- 절단된 사이클의 매수는 **모델 판단이 아니라 예산 소진으로 죽는다.** §7의 "thinking = 매수 삭감(4→0)"의 상당분이 실제로는 **절단 아티팩트**(reasoning 보수화가 아님).
- 재시작 전엔 없던 문제 → **thinking 업그레이드가 유발한 회귀.**
- 무알파 관문에 2600토큰 다 쓰고 결과물(JSON)조차 절반은 못 뱉음 = 최악 조합.

### 수정안 (운영자 확인 파라미터 — 임의 변경 안 함)
- **A. `CLAUDE_THINKING_SELECTION=off`** — 가장 깨끗. thinking 오버헤드 제거 → 출력 ~500토큰, 절단 0, 매수 억제 해소, 비용↓. 리플레이서 sonnet-5 무thinking drop-in 안전 확인됨. 측정된 품질 손실 없음.
- B. `CLAUDE_SELECTION_COMPRESSED_MAX_TOKENS` 상향(예 4500~6000) — thinking 유지하되 절단 해소. 단 비용↑·thinking 이득은 여전히 미측정.
- C. `SELECTION_OUTPUT_COMPRESSION_ENABLED=false` → 6000 캡 사용.
- +부수: config 2600 vs .env.live 2200 불일치 정정 필요.

### AB에 대한 재판정
- 원래 AB(thinking on/off 매수부호 확정)는 **현 절단 때문에 confound.** on 그룹이 절단으로 매수를 잃으므로 "thinking이 보수적"이 아니라 "thinking이 절단"을 보게 됨.
- → **절단부터 해소(A 또는 B) 후** net AB가 의미. A로 가면 애초에 thinking-on 비교 대상이 사라짐(무이득 확인 시).
