# -*- coding: utf-8 -*-
"""KR 급락일 투자자 수급 수집 (2026-09-10, 강제매도 가설 수급 재검정 1단계).

09-10 오전까지 강제매도는 **가격 지문**(갭다운·장중회복·연쇄하락·거래량)으로만 찍었고 매수 신호로는 전부 죽었다.
운영자가 KRX 계정을 열어줘서 이제 "그날 실제로 누가 팔았나"를 직접 볼 수 있다.

가설: 급락일에 **개인이 순매도**면 반대매매·신용청산 계열의 강제 매도(정보 없는 매도) → 반등.
      급락일에 **개인이 순매수**면 정보 있는 쪽(기관·외국인)이 던지고 개인이 받은 것 → 계속 하락.

수집: `stock.get_market_net_purchases_of_equities(d, d, "ALL", 투자자)` — 하루 전종목이 0.4~0.8초.
세션 하나당 개인·기관합계·외국인 3회. 결과는 JSONL 캐시에 쌓아 재실행 시 건너뛴다.

⚠️ 2026-09-10 실측: 무제한으로 때리면 **약 270요청(5분) 뒤부터 전부 실패**한다(응답이 JSON이 아니게 되고 파서가 KeyError).
KRX가 속도를 제한한다. 그래서 요청 간 간격(THROTTLE_SEC)을 두고, 실패하면 재로그인 + 지수 백오프로 재시도한다.
**에러로 기록된 날짜는 '수집 완료'로 치지 않는다** — 재실행하면 그 날짜만 다시 받는다.
자격증명은 `.env`의 KRX_ID/KRX_PW(환경변수)만 쓰고 어디에도 기록하지 않는다.

사용: python tools/research/research_krx_flow_collect.py <out_jsonl> [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--max-days N]
"""
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB = f"file:{ROOT / 'data' / 'shadow' / 'virtual_books.db'}?mode=ro"
INVESTORS = ("개인", "기관합계", "외국인")
THROTTLE_SEC = 0.6      # 요청 간 최소 간격 — 09-10 실측 무제한 호출 시 5분 뒤 전량 차단
MAX_TRIES = 4           # 실패 시 재로그인 + 백오프(2/4/8초)


def session_dates(lo: str, hi: str) -> list[str]:
    """급락 풀(xkr_fallen3) 백필이 실제로 거래를 낸 세션 날짜."""
    with closing(sqlite3.connect(DB, uri=True, timeout=5)) as con:
        rows = con.execute(
            "select distinct session_date from trades where strategy_id='xkr_fallen3' "
            "and backfill=1 and session_date>=? and session_date<=? order by 1", (lo, hi)).fetchall()
    return [r[0] for r in rows]


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a.split("=")[0]: (a.split("=", 1)[1] if "=" in a else "") for a in sys.argv[1:] if a.startswith("--")}
    if not args:
        print(__doc__)
        return 1
    out = Path(args[0])
    lo = flags.get("--from", "2000-01-01")
    hi = flags.get("--to", "2100-01-01")
    max_days = int(flags.get("--max-days", "0") or 0)

    done: set[str] = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("errors"):
                continue   # 실패 행은 '완료'가 아니다 — 재실행 시 다시 받는다
            done.add(r["date"])
    todo = [d for d in session_dates(lo, hi) if d not in done]
    if max_days:
        todo = todo[:max_days]
    print(f"[FLOW] 대상 세션 {len(todo)} (이미 수집 {len(done)})")
    if not todo:
        return 0

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
    if not (os.environ.get("KRX_ID") and os.environ.get("KRX_PW")):
        print("[FLOW] KRX_ID/KRX_PW 없음 — 중단")
        return 1
    from pykrx.website.comm import auth
    sess = auth.build_krx_session()
    if not sess:
        print("[FLOW] KRX 로그인 실패 — 중단")
        return 1
    auth.set_auth_session(sess)
    from pykrx import stock

    out.parent.mkdir(parents=True, exist_ok=True)
    ok = fail = 0
    t_start = time.time()
    with out.open("a", encoding="utf-8") as fh:
        for i, d in enumerate(todo, 1):
            ymd = d.replace("-", "")
            row: dict = {"date": d, "by": {}}
            bad = False
            for inv in INVESTORS:
                for attempt in range(1, MAX_TRIES + 1):
                    time.sleep(THROTTLE_SEC)
                    try:
                        df = stock.get_market_net_purchases_of_equities(ymd, ymd, "ALL", inv)
                        col = "순매수거래대금" if "순매수거래대금" in df.columns else "순매수거래량"
                        row["by"][inv] = {str(tk): int(v) for tk, v in df[col].items() if v == v}
                        break
                    except Exception as exc:  # noqa: BLE001
                        if attempt == MAX_TRIES:
                            row.setdefault("errors", []).append(f"{inv}:{type(exc).__name__}")
                            bad = True
                            break
                        # 차단은 세션이 상해서 오는 경우가 많다 — 재로그인 후 백오프
                        time.sleep(2 ** attempt)
                        try:
                            if sess.refresh(os.environ["KRX_ID"], os.environ["KRX_PW"]):
                                auth.set_auth_session(sess)
                        except Exception:  # noqa: BLE001
                            pass
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            ok += 0 if bad else 1
            fail += 1 if bad else 0
            if bad:
                time.sleep(5)   # 연속 차단을 끊는다
            if i % 20 == 0 or i == len(todo):
                el = time.time() - t_start
                print(f"[FLOW] {i}/{len(todo)} 성공 {ok} 실패 {fail} · {el:.0f}s "
                      f"(잔여 {el / i * (len(todo) - i):.0f}s)", flush=True)
    print(f"[FLOW] 완료 — 성공 {ok} 실패 {fail} → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
