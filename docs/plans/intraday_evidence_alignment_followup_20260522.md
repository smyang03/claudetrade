# Intraday Evidence Alignment Follow-up Plan - 2026-05-22

## Why

`final prompt evidence alignment` 경고의 직접 원인은 데이터 수집 실패가 아니라 구조적 불일치였다.

- US 최종 prompt pool이 최대 35개까지 커질 수 있다.
- mid phase intraday evidence 요청 기본값은 20개였다.
- 따라서 prompt 35개 기준 최대 overlap이 `20 / 35 = 0.5714`로 고정된다.
- 경고 기준은 `FINAL_PROMPT_EVIDENCE_ALIGNMENT_WARN_OVERLAP_MIN=0.80`이므로, 기존 설정 조합에서는 provider가 정상이어도 경고가 반복될 수밖에 없었다.

이 상태에서는 Claude가 보는 후보 35개 중 일부 후보가 장중 실행 근거 없이 판단될 수 있다. 주문/리스크 에러는 아니지만, selection 품질 경고로 보는 것이 맞다.

## What Changed

`trading_bot.py`의 final prompt evidence prefetch 계산을 보정했다.

- alignment가 켜져 있을 때 최종 prompt 개수와 경고 기준으로 필요한 최소 evidence 수를 계산한다.
- 계산식: `ceil(prompt_count * FINAL_PROMPT_EVIDENCE_ALIGNMENT_WARN_OVERLAP_MIN)`
- 예: prompt 35개, 기준 0.80이면 최소 evidence target은 28개다.
- 기존 phase 기본 target보다 이 값이 클 때만 prefetch target을 올린다.
- `INTRADAY_EVIDENCE_MAX_TICKERS` global cap은 그대로 유지한다.
- funnel/meta에 `evidence_alignment_min_target`을 기록한다.

예상 로그 변화:

```text
selection_intraday_evidence_coverage ... target_rule=phase:mid+alignment_min:28 requested=28
```

## Remaining Attention Point

late/close_guard phase에서도 prompt가 35개라면 기존 phase 기본값보다 evidence 요청 수가 늘 수 있다.

- 기존 late 기본값: 12
- 기존 close_guard 기본값: 8
- 개선 후 prompt 35개, 기준 0.80이면 최대 28개까지 올라갈 수 있다.

US yfinance는 부담이 작지만, KR KIS는 장 막판 API 호출 수가 늘 수 있다. 다만 `INTRADAY_EVIDENCE_MAX_TICKERS=30`, timeout, coverage fail-closed가 유지되므로 주문/리스크 안전장치를 직접 우회하는 변경은 아니다.

## Later Review Checklist

재시작 후 다음 항목을 확인한다.

- `selection_intraday_evidence_coverage`에 `target_rule=phase:mid+alignment_min:28` 형태가 찍히는지 확인한다.
- `final prompt evidence alignment` 경고가 사라지는지 확인한다.
- 경고가 남는다면 구조적 target 부족이 아니라 provider 실패, coverage 부족, cache/session 문제인지 분리한다.
- KR 장중, 특히 late/close_guard phase에서 KIS timeout 또는 prefetch timeout 증가 여부를 본다.
- KR timeout이 늘면 `KR_INTRADAY_EVIDENCE_MAX_TICKERS_LATE`, `KR_INTRADAY_EVIDENCE_MAX_TICKERS_CLOSE_GUARD` 같은 phase override를 검토한다.

## Decision

현재 변경은 적용 가능한 상태다. 운영 재시작 후 위 로그와 KR KIS timeout만 관찰한다.
