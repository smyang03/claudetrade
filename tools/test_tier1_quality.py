"""
1차 티어 프롬프트 품질 테스트 — V1/V2/V3 비교
- audit DB 히스토리 세션 리플레이
- 각 버전의 BUY_READY/WATCH false negative율, 압축률 측정
"""
import os, sys, sqlite3, json, time
from dotenv import load_dotenv

load_dotenv(r"E:\code\claudetrade\.env.live")
sys.path.insert(0, r"E:\code\claudetrade")
import anthropic

DB_PATH = r"E:\code\claudetrade\data\audit\candidate_audit.db"
MODEL = "claude-haiku-4-5-20251001"

# ── 프롬프트 버전 ────────────────────────────────────────────────

PROMPTS = {

# ─────────────────────────────────────────────────────────────────
"V4_strict_safe": """\
[1차 TRIAGE] US 종목 선별
목적: {n}개 후보 중 2차 상세 판단 대상 선별. 목표: 18~25개 SHORTLIST.

필드: ticker chg liq bucket s=trainer_score

━━━ ⛔ 절대 SKIP 금지 — 아래 중 하나라도 해당하면 무조건 SHORTLIST ━━━
• liq=high                         (chg가 -30%여도 유지)
• bucket=liquidity_leader          (대형주 반락, PROBE 후보)
• bucket=gap_pullback              (마이너스 등락 정상)
• bucket=opening_range_pullback    (마이너스 등락 정상)
• bucket=pullback_watch            (pullback 전략 후보)
• s >= 60                          (trainer 고점수)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

위 조건 모두 해당 없을 때(= liq≠high, 특수버킷 아님, s<60)만 아래 적용:
  SKIP: liq=low AND s < 55 AND chg < +4%
  SKIP: liq=mid AND s < 50 AND chg < +3%
  나머지는 SHORTLIST

전략 판단·가격 플랜·순위 매기기 금지. 2차 역할.

후보:
{candidates}

JSON만 출력 (코드펜스 없음):
{{"shortlist":["T1","T2"],"skip":["T3"]}}""",

# ─────────────────────────────────────────────────────────────────
"V5_two_pass": """\
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
{{"shortlist":["T1","T2"],"skip":["T3"]}}""",
}

# ── 테스트 세션 ──────────────────────────────────────────────────

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

# ── 유틸 ─────────────────────────────────────────────────────────

def load_session(call_id, date, con):
    cur = con.cursor()
    if call_id is None:
        cur.execute("""
            SELECT r.call_id
            FROM audit_candidate_rows r
            JOIN audit_claude_calls c ON r.call_id=c.call_id
            WHERE r.market='US' AND r.session_date=? AND r.in_prompt=1
              AND c.label LIKE '%select%'
            GROUP BY r.call_id
            HAVING SUM(CASE WHEN r.claude_action IN ('BUY_READY','PROBE_READY') THEN 1 ELSE 0 END) >= 2
            ORDER BY r.call_id LIMIT 1
        """, (date,))
        row = cur.fetchone()
        if not row:
            return None, None
        call_id = row[0]
    cur.execute("""
        SELECT ticker, prompt_rank, claude_action,
               change_pct, liquidity_bucket, primary_bucket, trainer_plan_a_score
        FROM audit_candidate_rows
        WHERE call_id=? AND in_prompt=1 ORDER BY prompt_rank
    """, (call_id,))
    return call_id, cur.fetchall()


def build_lines(rows):
    lines = []
    for r in rows:
        ticker, rank, action, chg, liq, bucket, score = r
        chg_s = f"{chg:+.1f}%" if chg is not None else "?%"
        score_s = f"s={score:.0f}" if score is not None else "s=?"
        lines.append(f"{ticker} {chg_s} liq={liq or '?'} bucket={bucket or '?'} {score_s}")
    return "\n".join(lines)


def call_claude(prompt_text: str) -> tuple[dict, object]:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    resp = client.messages.create(
        model=MODEL,
        max_tokens=800,
        messages=[{"role": "user", "content": prompt_text}],
    )
    raw = resp.content[0].text.strip()
    # 마크다운 코드펜스 제거
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


# ── 메인 ─────────────────────────────────────────────────────────

def run():
    con = sqlite3.connect(DB_PATH)
    version_stats: dict[str, list] = {v: [] for v in PROMPTS}
    total_tokens = {v: {"in": 0, "out": 0} for v in PROMPTS}

    sessions = []
    for date, call_id in TEST_SESSIONS:
        cid, rows = load_session(call_id, date, con)
        if rows:
            sessions.append((date, cid, rows))

    print(f"테스트 세션 {len(sessions)}개 × 버전 {len(PROMPTS)}개 = {len(sessions)*len(PROMPTS)}회 API 호출\n")

    for date, call_id, rows in sessions:
        actual_br   = {r[0] for r in rows if r[2] in ("BUY_READY", "PROBE_READY")}
        actual_watch = {r[0] for r in rows if r[2] == "WATCH"}
        actual_pw   = {r[0] for r in rows if r[2] == "PULLBACK_WAIT"}
        valuable = actual_br | actual_watch | actual_pw  # 2차로 가야 할 전체
        cands_text = build_lines(rows)
        n = len(rows)

        print(f"[{date}] {call_id[-20:]}  cands={n}  BR={sorted(actual_br)}  W={sorted(actual_watch)[:5]}")

        for vname, template in PROMPTS.items():
            prompt = template.format(n=n, candidates=cands_text)
            result, usage = call_claude(prompt)
            shortlist = {t.upper() for t in result.get("shortlist", [])}
            skip      = {t.upper() for t in result.get("skip", [])}

            br_missed    = actual_br - shortlist
            watch_missed = actual_watch - shortlist
            pw_missed    = actual_pw - shortlist
            val_missed   = valuable - shortlist

            fn_br  = len(br_missed) / len(actual_br) * 100   if actual_br   else 0
            fn_val = len(val_missed) / len(valuable) * 100   if valuable    else 0
            comp   = len(skip) / n * 100                     if n           else 0

            total_tokens[vname]["in"]  += usage.input_tokens
            total_tokens[vname]["out"] += usage.output_tokens

            status = "✓" if not br_missed else "✗"
            print(f"  {vname:15s} SL={len(shortlist):2d} SKIP={len(skip):2d}({comp:2.0f}%)"
                  f"  BR_FN={fn_br:3.0f}%{status}  VAL_FN={fn_val:3.0f}%"
                  f"  BR미={sorted(br_missed)}  W미={sorted(watch_missed)[:3]}"
                  f"  tok={usage.input_tokens}+{usage.output_tokens}")
            version_stats[vname].append({
                "fn_br": fn_br, "fn_val": fn_val, "comp": comp,
                "br_missed": sorted(br_missed), "watch_missed": sorted(watch_missed),
            })
            time.sleep(0.5)
        print()

    con.close()

    print("=" * 70)
    print("버전별 종합 (세션 평균)")
    print("=" * 70)
    for vname, stats in version_stats.items():
        if not stats:
            continue
        avg_fn_br  = sum(s["fn_br"]  for s in stats) / len(stats)
        avg_fn_val = sum(s["fn_val"] for s in stats) / len(stats)
        avg_comp   = sum(s["comp"]   for s in stats) / len(stats)
        total_br_missed = sum(len(s["br_missed"]) for s in stats)
        tin  = total_tokens[vname]["in"]
        tout = total_tokens[vname]["out"]
        cost = (tin * 0.8 + tout * 4) / 1e6
        print(f"  {vname:15s}  BR_FN={avg_fn_br:4.1f}%  VAL_FN={avg_fn_val:4.1f}%"
              f"  압축={avg_comp:4.1f}%  총BR누락={total_br_missed}건"
              f"  tok={tin}+{tout}  cost=${cost:.4f}")
    total_cost = sum((total_tokens[v]["in"]*0.8 + total_tokens[v]["out"]*4)/1e6 for v in PROMPTS)
    print(f"\n  전체 API 비용: ${total_cost:.4f}")


if __name__ == "__main__":
    run()
