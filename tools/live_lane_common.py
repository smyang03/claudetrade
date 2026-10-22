# -*- coding: utf-8 -*-
"""격리 실주문 레인 공통 안전장치 (2026-10-05 리뷰 docs/reports/live_tools_review_20261005.md 반영).

- safe_cash_krw: KR orderable_cash는 예수금(dnca_tot_amt)이라 미결제 매수가 빠지지 않는다(H2) → D+1·D+2 중 최소.
- place_safely: place_order 예외(HTTP 500 뒤 복구 실패 등)는 '접수됐는지 모름'이다(H3) → 예외 대신 success=None으로
  돌려주고, 호출 측은 OPEN 포지션 행을 남겨 봇이 그 종목을 일반 포지션으로 청산하지 못하게 한다.
- kr_order_window: PC가 꺼져 있다 켜지면 예약 작업이 StartWhenAvailable로 장외에 몰아 돈다(10-12 20:11 캐너리
  매도 거부 "장운영시간이 아닙니다"). 휴장일·주문 창 밖이면 주문을 내지 않고 다음 정상 실행에 맡긴다.

2026-10-21 A1 — 매도의 PC 의존 제거 (10-20 패닉 ETF 매도 실패: PC 꺼짐 → 17:12 실행 → 보류, 10-21 08:42 DNS 실패 → unknown):
- kr_resv_window / resv_target_session: KIS 국내 예약주문(CTSC0008U) 접수 창 15:40~익일 07:30(23:40~00:10 제외)과
  그 예약이 전송될 영업일.
- reserve_exit_rows: 보유 확인 직후(16:12 reconcile) 다음 영업일 시가 시장가 매도를 예약한다. 원장 행 kind=resv_order.
  이후 08:42 매도 작업은 예약주문 처리결과(odno 전송 / rjct_rson2 거부 / cncl_ord_dt 취소 / 미처리)를 먼저 보고,
  전송됐으면 일반 매도를 내지 않는다(이중 매도 방지). 조회 자체가 실패하면 매도를 내지 않는다(예약이 살아 있을 수 있다).
- resolve_unknown_order_rows: 접수 응답을 못 받은 주문(state unknown, 주문번호 없음)을 당일 주문체결조회로 되짚어
  주문번호를 복구하거나 '브로커에 없음'으로 종결한다 — 10-21 reconcile이 "확인할 주문 없음"으로 지나친 구멍.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Any

KST = timezone(timedelta(hours=9))
KR_ORDER_WINDOWS = {
    "open_auction": (time(8, 30), time(9, 0)),     # 장전 동시호가 — 시가 체결 계약(진입)
    "close_auction": (time(15, 20), time(15, 30)),  # 장 마감 동시호가 — 종가 체결 계약(진입)
    "exit": (time(8, 30), time(15, 30)),            # 만기 청산 — 시가를 놓쳤어도 장중에 팔아 보유가 하루 더 늘지 않게
}
# 예약주문 접수 창(공식 예제 docstring): 15:40 ~ 다음 영업일 07:30, 서버 초기화 23:40~00:10 제외.
KR_RESV_WINDOW = ((time(15, 40), time(23, 40)), (time(0, 10), time(7, 30)))
RESV_TRANSMIT_GRACE = time(9, 5)   # 이 시각 전엔 '미처리'를 전송 대기로 본다(처리일 장 시작 전 일괄 전송)


def _now_kst(now: datetime | None = None) -> datetime:
    return (now or datetime.now(KST)).astimezone(KST)


def kr_order_window(kind: str, now: datetime | None = None) -> tuple[bool, str]:
    """KR 주문을 지금 내도 되는가 — (허용, 사유). 휴장 달력은 buyback_shadow.KR_HOLIDAYS."""
    from buyback_shadow import kr_is_session
    now = _now_kst(now)
    if not kr_is_session(now.date()):
        return False, f"{now.date()} KRX 휴장일"
    lo, hi = KR_ORDER_WINDOWS[kind]
    if not lo <= now.time() < hi:
        return False, f"주문 창 밖({now:%H:%M} — {kind} {lo:%H:%M}~{hi:%H:%M})"
    return True, ""


def kr_resv_window(now: datetime | None = None) -> tuple[bool, str]:
    """예약주문 접수 가능 시각인가. 휴장일 저녁에도 접수는 된다(최초 도래 영업일에 전송)."""
    now = _now_kst(now)
    if any(lo <= now.time() < hi for lo, hi in KR_RESV_WINDOW):
        return True, ""
    return False, f"예약주문 창 밖({now:%H:%M} — 15:40~23:40 · 00:10~07:30)"


def resv_target_session(now: datetime | None = None) -> date:
    """지금 접수한 예약주문이 전송될 영업일(최초 도래 영업일)."""
    from buyback_shadow import kr_is_session, kr_next_session
    now = _now_kst(now)
    if now.time() >= time(15, 40):
        return kr_next_session(now.date())
    return now.date() if kr_is_session(now.date()) else kr_next_session(now.date())


def safe_cash_krw(bal: dict) -> float:
    vals = [float(bal.get(k) or 0) for k in ("orderable_cash", "d1_settlement_krw", "d2_settlement_krw")]
    vals = [v for v in vals if v > 0]
    return min(vals) if vals else 0.0


def place_safely(kis: Any, ticker: str, qty: int, price: float, side: str, token: Any, **kw) -> dict:
    try:
        res = kis.place_order(ticker, qty, price, side, token, **kw)
        return res if isinstance(res, dict) else {"success": None, "msg": f"unexpected:{type(res).__name__}"}
    except Exception as exc:  # noqa: BLE001 — 접수 여부 불명
        return {"success": None, "order_no": "", "msg": f"unknown:{type(exc).__name__}:{str(exc)[:160]}"}


def reserve_safely(kis: Any, ticker: str, qty: int, price: float, side: str, token: Any) -> dict:
    """예약주문 접수 — place_safely와 같은 계약(예외 → success None)."""
    try:
        res = kis.place_reserved_order_kr(ticker, qty, price, side, token)
        return res if isinstance(res, dict) else {"success": None, "msg": f"unexpected:{type(res).__name__}"}
    except Exception as exc:  # noqa: BLE001
        return {"success": None, "rsvn_seq": "", "msg": f"unknown:{type(exc).__name__}:{str(exc)[:160]}"}


def order_state(res: dict) -> str:
    """ok / rejected / unknown — unknown은 포지션을 OPEN으로 남겨 보호하고 운영자 확인 대상으로 둔다."""
    if res.get("success") is True:
        return "ok"
    if res.get("success") is None:
        return "unknown"
    return "rejected"


# ────────────────────────────────────────────── 예약주문 원장 (kind=resv_order)
def active_resv(rows: list[dict], ticker: str, target_session: str) -> dict | None:
    """해당 종목·전송일의 살아 있는 예약 행(접수 ok 또는 unknown — unknown도 브로커에 있을 수 있어 살아 있는 것으로 본다)."""
    hit = None
    for r in rows:
        if r.get("kind") == "resv_order" and r.get("ticker") == ticker and r.get("target_session") == target_session \
                and r.get("state") in ("ok", "unknown"):
            hit = r
    return hit


def reserve_exit_rows(kis: Any, token: Any, rows: list[dict], ticker: str, qty: int, *, now: datetime | None = None,
                      dry: bool = False) -> tuple[list[dict], str]:
    """다음 영업일 시가 시장가 매도 예약. (추가할 원장 행, 사유). 창 밖·이미 예약·수량 0이면 빈 리스트."""
    now = _now_kst(now)
    if int(qty or 0) < 1:
        return [], "수량 0"
    ok, why = kr_resv_window(now)
    if not ok:
        return [], why
    target = resv_target_session(now).isoformat()
    if active_resv(rows, ticker, target):
        return [], f"{target} 예약 이미 있음"
    if dry:
        return [], f"(dry) {ticker} x{qty} {target} 시가 시장가 매도 예약"
    res = reserve_safely(kis, ticker, int(qty), 0, "sell", token)
    state = order_state(res)
    row = {"kind": "resv_order", "ticker": ticker, "side": "sell", "qty": int(qty), "rsvn_seq": str(res.get("rsvn_seq") or ""),
           "success": res.get("success"), "state": state, "msg": str(res.get("msg") or "")[:200],
           "placed_session": now.date().isoformat(), "target_session": target,
           "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    return [row], f"{ticker} x{qty} {target} 시가 매도 예약 {state} seq={row['rsvn_seq'] or '-'} {row['msg']}"


def classify_resv_row(row: dict | None) -> tuple[str, str]:
    """예약주문 조회 행 → (transmitted/rejected/cancelled/pending/missing, 상세).
    필드는 KIS 포털 스펙(CTSC0004R output): odno 주문번호 · rjct_rson2 거부사유 · cncl_ord_dt 취소일 · prcs_rslt 처리결과."""
    if not row:
        return "missing", "조회 결과에 예약 행 없음"
    odno = str(row.get("odno") or "").strip().lstrip("0")
    if odno:
        return "transmitted", str(row.get("odno")).strip()
    if str(row.get("cncl_ord_dt") or "").strip():
        return "cancelled", str(row.get("cncl_ord_dt")).strip()
    rj = str(row.get("rjct_rson2") or "").strip()
    pr = str(row.get("prcs_rslt") or "").strip()
    if rj or "거부" in pr or "오류" in pr:
        return "rejected", rj or pr
    return "pending", pr or "미처리"


def check_resv(kis: Any, token: Any, resv: dict) -> tuple[str, str]:
    """예약 행의 브로커 처리결과. 조회 실패는 예외 그대로(호출 측이 매도를 내지 않게)."""
    # 조회 날짜 범위는 **예약주문주문일자(rsvn_ord_ord_dt = 전송 영업일)** 기준이다 — 10-21 실측: 접수일(10-21)로 조회하면 0행,
    # 전송일(10-22)로 조회해야 행이 나온다. 접수일~전송일 범위로 묶어 양쪽을 덮는다.
    placed = str(resv.get("placed_session") or "").replace("-", "")
    target = str(resv.get("target_session") or "").replace("-", "") or placed
    # ⚠️ RSVN_ORD_SEQ는 조회 필터로 넘기지 않는다 — 10-23 실측: 넘기면 KIS가 `ERROR INVALID INPUT_FILED_SIZE [RSVN_ORD_SEQ]`로
    # 거부(0패딩 9·10·11자리 전부)해 첫 실전(229200 63981)에서 09:10·17:59 조회가 RuntimeError로 끝나 체결을 못 읽었다.
    # 종목·방향·날짜로 조회한 뒤 예약번호는 아래서 로컬 매칭한다.
    rows = kis.inquire_reserved_orders_kr(token, min(placed, target), max(placed, target), ticker=resv.get("ticker", ""),
                                          side="sell")
    seq = str(resv.get("rsvn_seq") or "").strip().lstrip("0")
    cand = [r for r in rows if not seq or str(r.get("rsvn_ord_seq") or "").strip().lstrip("0") == seq]
    if not cand and not seq:
        cand = [r for r in rows if str(r.get("pdno") or "").strip() == str(resv.get("ticker") or "").strip()
                and int(float(r.get("ord_rsvn_qty") or 0)) == int(resv.get("qty") or 0)]
    return classify_resv_row(cand[-1] if cand else None)


def resv_exit_decision(status: str, now: datetime | None = None) -> str:
    """예약 처리결과 → 08:42/09:10 매도 작업의 행동: skip(전송됨) / wait(전송 대기) / fallback(일반 매도)."""
    if status == "transmitted":
        return "skip"
    if status in ("rejected", "cancelled"):
        return "fallback"
    return "wait" if _now_kst(now).time() < RESV_TRANSMIT_GRACE else "fallback"


def resv_rows_to_absorb(rows: list[dict], today: str) -> list[dict]:
    """전송일이 지났거나 오늘인데 그 세션의 매도 order 행이 없는 살아 있는 예약 — 08:42/09:10 작업이 안 돈 날(PC 꺼짐)의 흔적."""
    have = {(r.get("ticker"), r.get("session")) for r in rows
            if r.get("kind") == "order" and r.get("side") == "sell" and r.get("state") != "rejected"}
    out = []
    for r in rows:
        if r.get("kind") != "resv_order" or r.get("state") not in ("ok", "unknown"):
            continue
        tgt = str(r.get("target_session") or "")
        if not tgt or tgt > today or (r.get("ticker"), tgt) in have:
            continue
        if active_resv(rows, r["ticker"], tgt) is r:
            out.append(r)
    return out


def absorb_transmitted_resv_rows(kis: Any, token: Any, rows: list[dict], today: str) -> tuple[list[dict], list[str]]:
    """흡수 대상 예약의 브로커 처리결과를 보고, 전송됐으면 08:42 skip 분기와 같은 매도 order 행을 만든다(체결은 fill 루프가 잡는다)."""
    out, notes = [], []
    for r in resv_rows_to_absorb(rows, today):
        try:
            status, detail = check_resv(kis, token, r)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{r['ticker']} {r['target_session']} 예약매도 처리결과 조회 실패: {type(exc).__name__}")
            continue
        if status == "transmitted":
            out.append({"kind": "order", "session": r["target_session"], "ticker": r["ticker"], "side": "sell",
                        "qty": int(r.get("qty") or 0), "order_no": detail, "success": True, "state": "ok",
                        "msg": "resv_transmitted", "via": "resv", "rsvn_seq": r.get("rsvn_seq"),
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            notes.append(f"{r['ticker']} {r['target_session']} 예약매도 전송 확인 → 주문 {detail} (매도 작업 미실행분 흡수)")
        else:
            notes.append(f"{r['ticker']} {r['target_session']} 예약매도 {status} {detail} — 전송 안 됨")
    return out, notes


# ────────────────────────────────────────────── unknown 주문 되짚기
def unresolved_unknown_orders(rows: list[dict]) -> list[dict]:
    resolved = {r.get("ref_at") for r in rows if r.get("kind") in ("order_resolved",)}
    resolved |= {r.get("recovered_from_at") for r in rows if r.get("kind") == "order" and r.get("recovered_from_at")}
    return [r for r in rows if r.get("kind") == "order" and r.get("state") == "unknown" and not r.get("order_no")
            and r.get("at") not in resolved]


def resolve_unknown_order_rows(kis: Any, token: Any, rows: list[dict], *, now: datetime | None = None) -> tuple[list[dict], list[str]]:
    """unknown 주문을 브로커 당일 주문체결조회로 확인. 찾으면 주문번호를 복구한 order 행(recovered_from_at)을,
    당일 장이 끝났는데도 없으면 order_resolved(not_found) 행을 돌려준다. 조회 실패는 건너뛴다(다음 실행에서 재시도)."""
    now = _now_kst(now)
    out, notes = [], []
    for o in unresolved_unknown_orders(rows):
        sess = str(o.get("session") or "")
        try:
            truth = kis.find_kr_order_truth_by_session(token, ticker=o["ticker"], side=o["side"],
                                                       session=sess.replace("-", ""), qty=int(o.get("qty") or 0))
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{o['ticker']} {o['side']} {sess} unknown 되짚기 실패: {type(exc).__name__}")
            continue
        if truth and truth.get("order_no"):
            out.append({**o, "order_no": str(truth["order_no"]), "success": True, "state": "ok",
                        "msg": "recovered_from_broker", "recovered_from_at": o.get("at"),
                        "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            notes.append(f"{o['ticker']} {o['side']} {sess} unknown → 브로커 주문 {truth['order_no']} 복구")
        elif o.get("side") == "sell" or sess < now.date().isoformat() or now.time() >= time(16, 0):
            # 매도는 조회가 성공했는데 없으면 바로 미접수 확정(접수된 주문은 즉시 체결조회에 보인다) → 같은 날 재매도 길을 연다.
            # 매수는 이중 매수 위험이 있어 당일 장 마감(16:00) 뒤에만 확정한다.
            out.append({"kind": "order_resolved", "ref_at": o.get("at"), "ticker": o["ticker"], "side": o["side"],
                        "session": sess, "result": "not_found", "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
            notes.append(f"{o['ticker']} {o['side']} {sess} unknown → 브로커에 없음(미접수 확정)")
    return out, notes
