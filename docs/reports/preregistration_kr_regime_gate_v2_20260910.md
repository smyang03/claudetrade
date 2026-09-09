# 사전등록 — family C_REGIME_V2: KR 이벤트·급락 arm × KOSPI MA20 아래 국면 + 내부자 K1 삼성·하이닉스 제외 (2026-09-10 00:50 KST)

운영자 지시 흐름(09-09~10): "수익 나는 애들만 잡고 손실 내는 애들 걸러낼 방법을 검증하면서 찾아라" → 걸러내기 규칙 워크포워드 6종·OOS 3종 → "그래서 뭐 어떻게 하자는 거야" → 등록.
등록 시점 = 2026-09-10 00:50 KST(09-10 KR 세션 전). forward 표본은 **2026-09-10 세션부터**. 그 전은 백필(후보 생성용).

## 1. 근거 (판정 아님)

워크포워드(신규 c_* 19 arm, 월 M은 M 이전 데이터로만 규칙 결정, 2025-12~2026-08):
- 진입 특성 18종 3분위 필터: 사전 선택 AND 결합 시 리프트 양수월 2/6 → 기각. KR 가격 필터도 부모 풀에서 재현 안 됨(+0.07pp).
- arm 성적 선택(t≥2): 리프트 8/9월이나 유지 arm이 삼성 착시·데이터 끊긴 US 내부자·OOS 0인 US 패닉 → 실용성 없음.
- **KOSPI MA20 국면(진입 시점 상태)**: 인샘플 KR 10 arm 중 9개 아래>위.

| KR arm (백필 2025-06~2026-09, 세션평균) | MA20 위 | MA20 아래 |
|---|---:|---:|
| c_kr_fallen5_nobio | −1.22 (221세션) | +1.09 (86) |
| c_kr_fallen_nomajor | −1.45 (169) | +1.06 (73) |
| c_kr_fallen_buyback30 | +0.56 (48) | +3.86 (35) |
| c_kr_fallen_buyback_active | −0.81 (67) | +2.94 (39) |
| c_kr_buyback_start | +0.45 (49) | +1.93 (25) |
| c_kr_insider_cluster | +0.13 (221) | +0.78 (86) |
| c_kr_plan_buy | −2.54 (52) | +0.42 (31) |
| c_kr_exright | −0.76 (21) | −0.04 (12) |
| c_kr_insider_k1 (삼성 착시) | +3.24 (218) | +1.31 (83) |

OOS(인샘플 이전, 급락 ≤−5% & 거래대금≥20억, 다음 시가 TP12/SL25/D7 KR 비용, 저장소 `contract_exit_v2`):

| 패널 | MA20 아래 | MA20 위 |
|---|---:|---:|
| 자사주 종목 250개 yfinance 2023-01~2025-05 (세션평균) | −0.54 (224세션) | −1.28 (284) |
| 〃 연도별 거래평균 2023 / 2024 / 2025 | +0.31 / +1.16 / +1.19 | −1.21 / −1.26 / +0.20 |
| 634종목 캐시 2024-12~2025-05 | +0.16 (43세션, 승 64%) | −1.14 (75, 승 45%) |
| 자사주 공시 0~30일 창 2023~2025-08 | +0.66 (n 75) | −0.64 (n 42) |
| US SPY MA20 (Alpaca 2023-01~2025-04) | −0.29 (2023은 역전) | −0.43 |

해석: KR은 부호 6/6 유지. "위 국면 급락 매수"는 전 패널 음수(확실한 손실원). "아래 국면" 자체는 OOS 세션평균 ≈0~+1 → **손실 회피 필터로 등록, 수익원 증명 아님.** US는 게이트 없음(등록 안 함).
스크립트: `tools/research/research_walkforward_*.py`, `research_wf_*.py`, `research_kr_regime_gate_oos.py`, `research_kr_buyback_regime_oos.py`, `research_us_regime_gate_oos.py`.

## 2. 등록 arm

부모 arm은 그대로(forward 계속). 같은 풀·같은 계약(TP/SL/hold 부모와 동일)에 `idx_above_ma20: False`만 얹은 변형을 새 id(`<부모>_rg`)로 등록. "위" 부분집합 = 부모 − 변형.

| id | 부모 | 필터 |
|---|---|---|
| c_kr_fallen_buyback30_rg | c_kr_fallen_buyback30 | 부모 + KOSPI MA20 아래 |
| c_kr_fallen_buyback_active_rg | c_kr_fallen_buyback_active | 〃 |
| c_kr_fallen_nomajor_rg | c_kr_fallen_nomajor | 〃 |
| c_kr_fallen5_nobio_rg | c_kr_fallen5_nobio | 〃 (c_kr_fallen_regime과의 차이 = 바이오 제외) |
| c_kr_insider_cluster_rg | c_kr_insider_cluster | 〃 |
| c_kr_plan_buy_rg | c_kr_plan_buy | 〃 |
| c_kr_exright_rg | c_kr_exright | 〃 |
| c_kr_buyback_start_rg | c_kr_buyback_start | 〃 |
| c_kr_insider_k1_ex | c_kr_insider_k1 | 삼성전자(005930)·SK하이닉스(000660) 제외 (국면 게이트 없음) |

국면 결측이면 진입하지 않는다(fail-closed, 기존 필터 규약). 실주문 경로 없음.

## 3. 판정 규칙 (사전 고정)

- 표본 단위 = 정산 세션. 1차 판정 = forward **30 정산 세션** 도달 시(KR 아래 국면은 월 5~6세션이라 4~6개월 예상).
- 통과 = 세션 t ≥ 2.0 & 평균 net > 0 & 상위 2세션 제거 후 > 0 & **부모 arm 대비 증분 > 0**(부모 같은 기간) & 위 부분집합(부모 − 변형)이 변형보다 낮음.
- c_kr_insider_k1_ex 통과 = 30건 세션 t ≥ 2 & 평균 > 0. 부모 c_kr_insider_k1의 판정은 이 arm 결과로 대체한다(삼성·하이닉스 포함 수치로는 승격 논의 금지).
- 반증(표시만): 변형이 부모보다 낮음, 또는 위/아래 부호 차이 소멸.
- 승격은 운영자만. 통과해도 canary 5만원부터.

## 4. 함께 정리한 것

- c_us_insider_cluster / c_us_insider_k1: SEC 분기 데이터셋이 2026Q1까지라 2026-04-21 이후 표본 0 → note에 "판정 불가" 표기. 수집기 전까지 forward 판정 대상 아님.
- 회피 필터 후보(동전주·급등 중 내부자·개별 악재형·IBS)는 워크포워드에서 재현되지 않아 등록하지 않음.
