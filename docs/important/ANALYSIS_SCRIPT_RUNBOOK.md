# 분석 스크립트 실행 런북 (멈춤·실패 방지)

> 2026-06-27 작성. 분석 쿼리/스크립트가 "멈춘 것처럼" 보이거나 깨지는 문제 재발 방지.
> 분석 돌리기 전 이 체크리스트대로 한다.

## 왜 멈춰 보였나 (원인 2개)
1. **도구호출 형식 깨짐** — tool-call XML이 잘못 나가면 *실행 자체가 안 됨* → 결과 없이 멈춘 것처럼 보임. (가장 흔한 원인)
2. **라이브 DB 락** — 가동 중인 봇(PID)이 `v2_event_store.db`·`candidate_audit.db`·`decisions.db`에 쓰는 중이면, 읽기 쿼리가 락을 무한 대기 → 타이머가 한 값에서 멈춤.

## 실행 수칙 (분석 쿼리/스크립트)

### A. 인라인 `python -c` 금지 — 스크립트 파일로
- 중첩 따옴표(bash " 안의 python " 안의 SQL ")가 깨짐의 주범.
- **반드시 scratchpad에 `.py` 파일로 Write → `python <파일>` 실행.**
- scratchpad: `C:\Users\Unknown\AppData\Local\Temp\claude\E--code-claudetrade\<session>\scratchpad\`

### B. DB 연결 — 항상 read-only + 락 보호 + 절대경로
```python
import sqlite3
con = sqlite3.connect("file:E:/code/claudetrade/data/ml/decisions.db?mode=ro", uri=True, timeout=3)
con.execute("PRAGMA busy_timeout=3000")   # 락이면 3초 후 에러(무한대기 X)
con.row_factory = sqlite3.Row
```
- `uri=True` **필수** (없으면 `file:...?mode=ro`를 파일명으로 오인 → "unable to open").
- 절대경로 (상대 `file:data/...`는 Windows에서 cwd 의존·실패).

### C. 실행 — 하드 타임아웃 래핑
```
timeout 25 python "C:\...\scratchpad\xxx.py"; echo "[exit:$?]"
```
- `timeout 25` = 락/행이어도 25초 후 강제종료. exit 124=타임아웃/락, 0=정상.
- 출력에 `print(..., flush=True)` 또는 섹션 헤더로 진행 표시.

### D. 코드 검색 — bash `grep` 금지, Grep 도구 사용
- `trading_bot.py`(3.8만 줄)+runtime 전체 bash grep = 40초+ → 멈춤처럼 보임.
- **Grep 도구(ripgrep)**는 빠르고 결과가 바로 옴. 정규식 이스케이프도 안전.

### E. 도구호출 형식 — 한 번에 하나, 형식 확인
- tool-call XML이 깨지면 실행 안 됨. 복잡한 다중 호출보다 **단순·검증된 호출 1개**씩.
- Write로 파일 만든 직후 "created" 확인 → 그 다음 Bash 실행 (파일 존재 보장).

## 실행 전 체크리스트 (매번)
- [ ] 인라인 -c 아니라 `.py` 파일인가
- [ ] DB는 `?mode=ro` + `uri=True` + `busy_timeout` + 절대경로인가
- [ ] `timeout 25`로 감쌌나
- [ ] 코드검색이면 bash grep 아니라 Grep 도구인가
- [ ] Write 성공("created") 확인 후 Bash 실행인가

## 비용 모델 (분석 공통)
- net = gross − 수수료/FX. **US 0.7%**(0.5+FX0.2), **KR 0.5%**.
- v2_learning_performance는 net 우선: `pnl_pct_net` 있고 `net_basis in (measured/backfilled_exact/backfilled_fee_only)`면 사용, `backfilled_fee_only`+US면 −0.2% 추가. 아니면 `pnl_pct − 비용`.
- `runtime_mode='live'`만.

## 참고
- 분석 자세·원칙: `ALWAYS_ANALYZE.md`
- 이 런북은 "어떻게 안 멈추고 돌리나"(실행 기계)만 다룬다.
