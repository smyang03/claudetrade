# 실행 설계 리스트업 — KR/US 전체 + 오늘 밤 US 트라이 (2026-07-21)

원칙(오늘 확정): **손절은 짧게(−2% 유지) · 익절은 길게(러너) · 선별↑ · 공격 매수 · 관리로 흑자.**
"관리는 손실→본전(−2.4%→0), 선별이 본전→흑자(+1.94% 실체결), 공격매수가 규모."

## 전체 설계 리스트 (4축)

### 축 1 — 매수 살리기 ("매수도 Claude 판단")
| 항목 | 변경 지점 | KR/US | 위험 | 오늘밤 |
|---|---|---|---|---|
| BUY_READY 판단권 복원 | `single_symbol_judge.py:116` 프롬프트 | US 강세 한정, KR 눌림유지 | 중(코드) | 후보 |
| 눌림존 모순 창 완화 | env `MAX_ZONE_ABOVE_CURRENT_PCT` | US 강세 | 중 | 후보 |
| 호출 희소성 완화 | `EARLY_JUDGE_MIN_MARKET_ELAPSED_MIN`·`MAX_CALLS_PER_TICKER` | 공통 | 낮음 | ✅ |
| PATHB_MIN_CONFIDENCE 0.5 | config | 공통 | 중 | 유지 |

### 축 2 — 이긴 놈 오래 안기 (러너) ★오늘 데이터 핵심
| 항목 | 현재 | 바꿀 것 | 위험 | 오늘밤 |
|---|---|---|---|---|
| **mfe_breakeven** | `true`(러너 죽임) | **off**(이익 봉우리 반납 컷 제거) | 낮음(손실 안 늘림) | ✅ |
| TAIL_CAPTURE_CARRY 조건 | strength 3%·risk_on만 | 완화(strength↓·regime 확대) | 낮음 | ✅ |
| weak_mfe_cut | 이미 `false` | 유지 | — | — |
| PRE_CLOSE 강제청산 | 시간컷 | 멀티데이 carry 허용 | 중 | 후보 |
| **손절 −2%(loss_cap)** | −2% | **유지**(완화 금지, 데이터 확정) | — | 유지 |

### 축 3 — 선별 (본전→흑자)
| 항목 | 상태 | 오늘밤 |
|---|---|---|
| KR 랭킹 재정렬 | 배포됨(shadow) | 관측 |
| dip gate·anti-chase | enforce | 유지 |
| 변동성 밴드 후보질 | 미착수 | — |

### 축 4 — hold_advisor 경량화 (매수 폭증 대비)
| 항목 | 변경 | 위험 | 오늘밤 |
|---|---|---|---|
| INTRADAY/PRE_CLOSE 소프트캐시 확장 | 코드 | 낮음 | 후보 |
| 3-analyst→triage 이관 | `TRIAGE_STAGE_ALLOWLIST` 확대 | 낮음 | 후보 |
| challenge JSON 절단 버그 수정 | 코드 | 낮음 | 후보 |

## ★오늘 밤 US 트라이 — 두 옵션 (운영자 선택)

핵심 딜레마: **러너·이익컷 제거(안전)만 켜면 매수가 없어(BUY_READY 금지) 효과 0.
BUY_READY까지 켜야 진짜 트라이가 되지만 코드 변경 + 실자금 리스크.**

### 옵션 A — config만 (안전·가역, 매수 효과 제한적)
- mfe_breakeven off (러너 살리기)
- TAIL_CAPTURE_CARRY strength 3→1.5, regimes 확대
- EARLY_JUDGE 호출 완화(warmup 5→2·per-ticker 2→3)
- 손절 유지
→ **오늘 밤 매수가 생기면** 러너로 관리. 단 BUY_READY 없어 눌림 대기라 매수 자체는 여전히 제한적. 코드 무변경·즉시 롤백.

### 옵션 B — A + BUY_READY 제한적 복원 (진짜 트라이, 코드 변경)
- A 전부 +
- `single_symbol_judge.py` BUY_READY 허용(US 강세 국면 한정, 소량)
- 눌림존 완화(US 강세)
→ **매수가 실제로 생김.** 단 코드 변경·테스트 필요, 실자금 공격 매수 노출. 되돌리기는 env 토글로 가역 설계.

## 트레이너 조언 (직언)

- **"전체 한 번에 enforce"는 권하지 않는다** — 여러 변경 동시면 뭐가 효과/문제인지 구분 불가(교란), BUY_READY는 검증 없는 실자금 공격이라 소량 시작이 맞다.
- 오늘 밤은 **옵션 B라도 "제한적·가역"으로**: BUY_READY를 US 강세 국면 + 소수 종목 + 기존 포지션 캡 안에서만. 손절 −2%는 절대 유지(방어선).
- 각 변경은 **운영자 확인 필수 파라미터**(BUY_READY 프롬프트·loss_cap·PATHB confidence). 두 소스(config+.env.live) 동시 + 재시작 후 effective-config 실측.

## 관측 지표 (오늘 밤 US 세션)
- 매수 발생 건수(BUY_READY vs PULLBACK_WAIT), 진입 후 러너 carry 발동, mfe_breakeven 제거 효과, 손절 −2% 발동, 당일청산 vs 멀티데이 비율, 실에러.

## 롤백
- 전부 config/env 토글 → 즉시 원복. BUY_READY는 프롬프트 env 게이트로 off 가능하게 구현.
