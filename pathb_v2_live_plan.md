# Path B Claude Price Live Reference

상태 업데이트: 2026-05-20

이 문서는 더 이상 active phase checklist가 아니다. Path B live 구현의 상세 완료 단계는 Git history와 `docs/DEVELOPED_WORK.md`에 보존하고, 지금 해야 할 일은 `docs/TODO_ROADMAP.md`만 기준으로 본다.

## 현재 운영 정책

- Path A: 기존 `trading_bot.py` timing/strategy/order 흐름.
- Path B: `runtime/pathb_runtime.py`의 Claude price plan 기반 조건부 진입/청산 흐름.
- 두 경로는 `runtime/action_routing.py`의 route/audit 메타를 통해 같은 safety/order truth로 합류한다.
- 현재 live 정책은 KR PathB live off, US PathB live on이다.
  - `PATHB_KR_LIVE_ENABLED=false`
  - `KR_CLAUDE_PRICE_LIVE_ENABLED=false`
  - `PATHB_ENABLED=true`
  - Telegram control on
- live config 기준 PathB fixed budget은 `PATHB_FIXED_ORDER_KRW=300000`이다. KIS fill truth와 sell pending reconciliation이 더 검증되기 전에는 size를 키우지 않는다.
- `PULLBACK_WAIT`는 PathA trade_ready가 아니라 PathB wait-only price plan으로 등록될 수 있다. plan/order/position audit의 `origin_action=PULLBACK_WAIT`, `origin_route=pathb_wait_only` 보존이 필수다.

## 완료된 구현 범위

- selection prompt의 `price_targets` 계약과 PathA legacy 호환.
- `v2_path_runs`, PathB lifecycle status, path run lookup/recovery.
- `PricePlan` parser/validator, KR/US native price 처리, reward/risk validation.
- PathB safety gate, buy-zone touch, tick rounding, shared order executor 연결.
- hard stop/Claude stop/target/pre-close exit signal과 final policy exit bypass.
- startup recovery, waiting plan scan, session close expire/cancel.
- Telegram `/pathb_status`, `/pathb_on`, `/pathb_off`, `/pathb_kill`, `/pathb_closeall`.
- dashboard/live status/daily review의 Path A/B 최소 비교.
- `PULLBACK_WAIT` origin audit 보존과 관련 regression tests.

## 지금 남은 Path B 관련 작업

Path B 전용 세부 TODO도 `docs/TODO_ROADMAP.md`에만 둔다.

| TODO | 이유 |
| --- | --- |
| KIS fill truth 실수신 검증 | full/partial/cancel payload와 재기동 fill cache가 실제 브로커 truth와 맞아야 한다. |
| sell pending remainder reconciliation | 현재 live sell execution은 기존 `_execute_sell()`을 재사용하므로, partial fill TTL 이후 잔량 재주문/확인을 별도로 강화해야 한다. |
| KR EXPIRED quote resampling | KR PathB miss quality에서 quote sample 부족과 zone reentry 가능성이 관찰됐다. |
| stale waiting plan/zone hit 운영 모니터링 | `PULLBACK_WAIT -> live order` 전이가 operator에게 PathB wait-only origin으로 명확히 보여야 한다. |
| size 확대 금지 gate | 위 검증 전에는 fixed budget 또는 max position을 키우지 않는다. |

## 검증 기준

다음 항목은 live 시작 전 운영 runbook에서 반복 확인한다.

```powershell
python tools/live_preflight.py --mode live --skip-dashboard --json
python tools/live_guardian.py --mode live --skip-dashboard --json
```

- hard fail 0.
- unresolved ORDER_UNKNOWN 0 또는 remediation 완료.
- 기존 broker open buy order 0.
- KR PathB live disabled가 의도된 WARN으로만 표시.
- US PathB live/on 상태와 Telegram control 상태 확인.
