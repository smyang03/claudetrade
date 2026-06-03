"""
1차 티어 품질 테스트 — 60개 확장 풀
- in_prompt 35개 + seen_not_prompt 상위 25개 = 최대 60개
- V5_two_pass 프롬프트로 1차 필터
- 측정: BUY_READY false negative율, WATCH false negative율, 압축률
"""
import os, sys, sqlite3, json, time
from dotenv import load_dotenv

load_dotenv(r"E:\code\claudetrade\.env.live")
sys.path.insert(0, r"E:\code\claudetrade")
import anthropic

DB_PATH = r"E:\code\claudetrade\data\audit\candidate_audit.db"
MODEL = "claude-haiku-4-5-20251001"
EXPAND_TO = 60

PROMPT_V5 = """\
[1차 TRIAGE] US 종목 선별
목적: {n}개 후보를 SHORTLIST(2차 분석 대상)와 SKIP(제외)으로 분류.

필드: ticker chg liq bucket s=trainer_score

[PASS 1 — 무조건 SHORTLIST 확정]
다음 중 하나라도 해당하면 즉시 SHORTLIST 확정, 이후 조건 무시:
  A. liq=high
  B. bucket 포함: liquidity_leader, gap_pullback, opening_range_pullback, pullback_watch
  C. s >= 60

[PASS 2 — PASS 1 미해당 후보만 처리]
  liq=low AND s < 55 AND chg < +4%  → SKIP
  liq=mid AND s < 52 AND chg < +3%  → SKIP
  그 외                              → SHORTLIST

애매하면 SHORTLIST. 전략 판단 금지.

후보:
{candidates}

JSON만 출력 (코드펜스 없음):
{{"shortlist":["T1","T2"],"skip":["T3"]}}"""

TEST_SESSIONS = [
    ("2026-05-18", "live_live_US_20260518_f273ce461a06730ed2b7"),
    ("2026-05-19", "live_live_US_20260519_a992471e06b7209ab931"),
    ("2026-05-20", "live_live_US_20260520_d2b14c1945282e97b0b4"),
    ("2026-05-21", "live_live_US_20260521_ce74ef882f5a8420e394"),
    ("2026-05-22", "live_live_US_20260522_fe095c258803d594e765"),
    ("2026-05-26", "live_live_US_20260526_7c53b5c694079357054b"),
    ("2026-05-27", "live_live_US_20260527_65948c3aaf2ba7818290"),
    ("2026-05-28", "live_live_US_20260528_820669065752c948721f"),
    ("2026-05-29", "live_live_US_20260529_fabcbab191691d003ab7"),
    ("2026-06-01", "live_live_US_20260601_4ef403342053779487bd"),
]


def load_expanded_session(call_id: str, date: str, con) -> tuple[list, set, set]:
    cur = con.cursor()

    # 1) in_prompt 후보 + 실제 판단 결과
    cur.execute("""
        SELECT ticker, change_pct, liquidity_bucket, primary_bucket,
               trainer_plan_a_score, claude_action, prompt_rank
        FROM audit_candidate_rows
        WHERE call_id=? AND in_prompt=1
        ORDER BY prompt_rank
    """, (call_id,))
    prompt_rows = cur.fetchall()

    prompt_tickers = {r[0] for r in prompt_rows}
    actual_br   = {r[0] for r in prompt_rows if r[5] in ("BUY_READY", "PROBE_READY")}
    actual_watch = {r[0] for r in prompt_rows if r[5] == "WATCH"}
    actual_pw   = {r[0] for r in prompt_rows if r[5] == "PULLBACK_WAIT"}

    # 2) seen_not_prompt 후보 — 중복 제거 후 raw_score 상위 채움
    expand_needed = max(0, EXPAND_TO - len(prompt_rows))
    cur.execute("""
        SELECT ticker, change_pct, liquidity_bucket, primary_bucket,
               trainer_plan_a_score, MAX(raw_score_current) raw_score
        FROM audit_candidate_rows
        WHERE session_date=? AND market='US'
          AND screener_seen=1 AND in_prompt=0
          AND ticker NOT IN ({})
        GROUP BY ticker
        ORDER BY raw_score DESC
        LIMIT ?
    """.format(",".join("?" * len(prompt_tickers))),
        (date, *prompt_tickers, expand_needed))
    extra_rows = cur.fetchall()

    # 합치기: in_prompt 먼저, 그 다음 확장 후보
    all_rows = []
    for r in prompt_rows:
        all_rows.append({
            "ticker": r[0], "chg": r[1], "liq": r[2], "bucket": r[3],
            "score": r[4], "source": "original"
        })
    for r in extra_rows:
        all_rows.append({
            "ticker": r[0], "chg": r[1], "liq": r[2], "bucket": r[3],
            "score": r[4], "source": "expanded"
        })

    return all_rows, actual_br, actual_watch | actual_pw


def build_lines(rows: list) -> str:
    lines = []
    for r in rows:
        chg_s   = f"{r['chg']:+.1f}%" if r["chg"] is not None else "?%"
        score_s = f"s={r['score']:.0f}" if r["score"] is not None else "s=?"
        lines.append(f"{r['ticker']} {chg_s} liq={r['liq'] or '?'} bucket={r['bucket'] or '?'} {score_s}")
    return "\n".join(lines)


def call_claude(prompt_text: str) -> tuple[dict, object]:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt_text}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        start, end = raw.find("{"), raw.rfind("}") + 1
        return json.loads(raw[start:end]), resp.usage
    except Exception:
        return {"shortlist": [], "skip": []}, resp.usage


def run():
    con = sqlite3.connect(DB_PATH)
    stats = []
    total_in, total_out = 0, 0

    for date, call_id in TEST_SESSIONS:
        rows, actual_br, actual_watch = load_expanded_session(call_id, date, con)
        if not rows:
            print(f"[{date}] 데이터 없음")
            continue

        orig_count = sum(1 for r in rows if r["source"] == "original")
        exp_count  = sum(1 for r in rows if r["source"] == "expanded")
        total = len(rows)

        cands_text = build_lines(rows)
        prompt = PROMPT_V5.format(n=total, candidates=cands_text)
        result, usage = call_claude(prompt)

        shortlist = {t.upper() for t in result.get("shortlist", [])}
        skip      = {t.upper() for t in result.get("skip", [])}

        br_missed    = actual_br - shortlist
        watch_missed = actual_watch - shortlist
        val_missed   = (actual_br | actual_watch) - shortlist

        # 확장 후보 중 SKIP된 것
        exp_tickers = {r["ticker"] for r in rows if r["source"] == "expanded"}
        exp_skipped = exp_tickers & skip
        exp_kept    = exp_tickers & shortlist

        fn_br  = len(br_missed)   / len(actual_br)             * 100 if actual_br    else 0
        fn_val = len(val_missed)  / len(actual_br | actual_watch) * 100 if (actual_br | actual_watch) else 0
        comp   = len(skip) / total * 100

        total_in  += usage.input_tokens
        total_out += usage.output_tokens

        status = "✓" if not br_missed else "✗"
        print(f"[{date}] orig={orig_count} +exp={exp_count} → total={total}")
        print(f"  BR={sorted(actual_br)}  W={sorted(actual_watch)[:5]}")
        print(f"  SL={len(shortlist)} SKIP={len(skip)}({comp:.0f}%)  "
              f"BR_FN={fn_br:.0f}%{status}  VAL_FN={fn_val:.0f}%")
        print(f"  BR누락={sorted(br_missed)}  W누락={sorted(watch_missed)[:3]}")
        print(f"  확장후보: SKIP={sorted(exp_skipped)}  KEEP={sorted(exp_kept)[:5]}{'...' if len(exp_kept)>5 else ''}")
        print(f"  tok={usage.input_tokens}+{usage.output_tokens}")
        print()

        stats.append({
            "date": date, "total": total, "orig": orig_count, "exp": exp_count,
            "fn_br": fn_br, "fn_val": fn_val, "comp": comp,
            "br_missed": sorted(br_missed), "exp_skip_cnt": len(exp_skipped),
        })
        time.sleep(0.8)

    con.close()

    print("=" * 65)
    print("종합 결과 — 60개 확장 풀")
    print("=" * 65)
    avg_fn_br  = sum(s["fn_br"]  for s in stats) / len(stats)
    avg_fn_val = sum(s["fn_val"] for s in stats) / len(stats)
    avg_comp   = sum(s["comp"]   for s in stats) / len(stats)
    avg_orig   = sum(s["orig"]   for s in stats) / len(stats)
    avg_total  = sum(s["total"]  for s in stats) / len(stats)
    total_br_missed = sum(len(s["br_missed"]) for s in stats)
    avg_exp_skip = sum(s["exp_skip_cnt"] for s in stats) / len(stats)
    cost = (total_in * 0.8 + total_out * 4) / 1e6

    print(f"  세션 수        : {len(stats)}")
    print(f"  평균 후보 수   : 기존 {avg_orig:.0f}개 → 확장 {avg_total:.0f}개")
    print(f"  BR false neg   : {avg_fn_br:.1f}%  (총 누락 {total_br_missed}건)")
    print(f"  VAL false neg  : {avg_fn_val:.1f}%")
    print(f"  평균 압축률    : {avg_comp:.1f}%")
    print(f"  확장후보 평균 SKIP: {avg_exp_skip:.1f}개/세션")
    print(f"  API 비용       : ${cost:.4f}  (tok {total_in}+{total_out})")

    if total_br_missed > 0:
        print("\n  ⚠ 누락 BUY_READY 상세:")
        for s in stats:
            if s["br_missed"]:
                print(f"    [{s['date']}] {s['br_missed']}")
    else:
        print("\n  ✓ 모든 BUY_READY 보존됨")


if __name__ == "__main__":
    run()
