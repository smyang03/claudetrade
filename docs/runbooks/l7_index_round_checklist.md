# L7 회차 체크리스트 — KOSPI200·KOSDAQ150 편입 (2026-10-31)

레인 계약은 `docs/operating_model.md` L7, 도구는 `tools/kr_index_inclusion_live.py`. 이 문서는 회차마다 사람이(또는 Claude 세션이) 하는 일만 적는다.
예약 작업 3개(10-31 확인, 전부 Ready·직전 결과 0): `claudetrade_kr_index_incl_buy` 평일 08:41 · `_sell` 15:20:40 · `_reconcile` 16:15. 로그 `logs/index_incl/`.

**특례편입 감시**(10-31): `claudetrade_kr_fast_entry_watch` 평일 18:30 → `tools/kr_fast_entry_watch.py`. 상장 8~20거래일째이고 시총 순위가 KOSPI 50위 / KOSDAQ 30위 안인 신규·이전 상장 종목이 CSV에 없으면 텔레그램을 보낸다(알림만, CSV는 사람이 쓴다). 로그 `logs/index_incl/fast_entry_watch.log`. 순위는 오늘 시총 기준 근사다(규정은 15거래일 평균) — 경계 종목은 KRX 발표로 확인한다.

## D0 — 발표일 저녁 (정기: 5월·11월 하순, 특례편입: 수시)

1. **명단 확인**: KRX 보도자료를 기사 두 곳 이상으로 교차 확인한다(KRX 사이트 자동수집 금지). 편입·편출 종목명, 효력일.
   - 2026-12 정기변경: 효력일 = 12월 선물·옵션 만기(12-10 목) 다음 거래일 **12-11**, 청산 세션 12-10.
2. **CSV 행 추가** `data/reference/kr_index_changes.csv`: `index,announce_date,effective_date,ticker,name,action,source_url`.
   - `announce_date` = 보도자료 날짜(장 마감 후 발표 가정 → 다음 거래일 시가 진입).
   - `ticker` 6자리. 영숫자 코드(예: `0009K0`) 그대로 쓴다. `action`은 `add`/`delete`.
3. **읽힘 확인** — 경고 줄이 없고 편입 수가 명단과 같아야 한다:
   ```
   python -c "import sys;sys.path[:0]=['.','tools'];import kr_index_inclusion_live as L;r=L.load_rounds();a=max(r);print(a,r[a]['effective'],len(r[a]['adds']),[x['ticker'] for x in r[a]['adds']])"
   python tools/kr_index_inclusion_live.py status
   ```
4. **현금**: 시장가 증거금이 전일 종가 × 1.3이라 50만 회차를 다 사려면 KR 주문가능현금이 약 65만 필요하다. 부족하면 도구가 **싼 종목부터 사고 나머지는 "주문가능현금 부족"으로 건너뛴다**(회차 일부만 체결). 보충 여부는 운영자 결정.
   - 같은 KR 현금을 L2(자사주 canary)·L3(패닉 ETF)가 같이 쓴다. 10-31 실측: 주문가능 38.5만, L3 229200 39.3만은 11-02 시가 예약매도로 돌아온다.
5. **봇 격리 조건**: 매수 전까지 `risk_manager.py`·`trading_bot.py`가 수정되면 봇을 재시작해야 한다. 안 하면 매수 작업이 fail-closed로 거부한다("봇 재시작 필요").
6. CSV 변경을 커밋한다(`data/reference/kr_index_changes.csv`만 명시 스테이징).

## D1 — 진입일

- 08:41 매수 후 `logs/index_incl/buy.log`: `회차 … 편입 N · 매수 M · 예산상 제외 K`, 종목별 `→ ok`. `unknown`이면 16:15 reconcile이 정리한다.
- 16:15 reconcile 후: 원장 `data/shadow/kr_index_inclusion_live.jsonl`에 position `OPEN`·`filled_qty` > 0.
- **첫 회차에서만 확인**: 영숫자 코드 종목이 있으면 KIS 주문 접수·체결을 확인한다(시세 조회만 검증됨, 주문은 미검증).

## 청산 — 효력일 전 거래일

- 15:20:40 장 마감 동시호가 시장가 매도 → 16:15 reconcile이 체결·손익(`close`, 수수료·거래세 반영)을 남긴다.
- 매도를 놓치면(PC 꺼짐 등) reconcile이 다음 시가 예약매도를 건다(A1). 다음 날 체결을 확인한다.

## 회차 후

- 회차 평균 초과(규모매칭)를 기록한다 — 폐기·확대 조건은 forward 4회차 기준(`operating_model.md` L7).
- 누적 실현 −10만이면 도구가 신규 회차를 막는다.
