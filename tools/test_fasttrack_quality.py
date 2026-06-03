"""
Fast-Track 프롬프트 품질 테스트
- 기존 WATCH → BUY_READY 전환 케이스를 positive 예시로 사용
- 기존 WATCH → WATCH 유지 케이스를 negative 예시로 사용
- Fast-Track 프롬프트가 이를 얼마나 잘 구분하는지 측정
"""
import os, sys, sqlite3, json, time
from dotenv import load_dotenv

load_dotenv(r"E:\code\claudetrade\.env.live")
sys.path.insert(0, r"E:\code\claudetrade")
import anthropic

DB_PATH = r"E:\code\claudetrade\data\audit\candidate_audit.db"
MODEL   = "claude-haiku-4-5-20251001"

# ── Fast-Track 프롬프트 ───────────────────────────────────────────

FAST_TRACK_PROMPT = """\
[Fast-Track 승격 체크] US 장중
목적: WATCH/IGNORE 후보가 지금 BUY_READY로 승격 가능한지 빠르게 판단.

현재 시장: {market_ctx}

후보 (현재 상태):
{candidates}

각 후보에 대해 판단:
  PROMOTE  : 지금 당장 BUY_READY 조건 충족
  KEEP     : 아직 조건 미충족, 계속 관찰
  EXPIRE   : 조건 악화 또는 기회 소멸

판단 기준:
- PROMOTE: 모멘텀 지속 + 과열 아님 + 명확한 entry 구간 존재
- EXPIRE : chg 역전 / 과열(chg>20%) / 전략 미스매치
- KEEP   : 나머지 전부

JSON만 출력 (코드펜스 없음):
{{"results":[{{"ticker":"X","verdict":"PROMOTE/KEEP/EXPIRE","reason":"한줄"}}]}}"""

# ── 테스트 데이터 수집 ────────────────────────────────────────────

def get_test_cases(con, n_sessions=8):
    cur = con.cursor()
    # 세션별로 WATCH→BUY_READY, WATCH→WATCH 쌍 수집
    cur.execute("""
        SELECT DISTINCT r.session_date
        FROM audit_candidate_rows r
        JOIN audit_claude_calls c ON r.call_id=c.call_id
        WHERE r.market='US' AND r.in_prompt=1
          AND r.session_date BETWEEN '2026-05-20' AND '2026-06-01'
          AND c.label LIKE '%select%'
          AND r.claude_action='WATCH'
        GROUP BY r.session_date
        HAVING COUNT(*) >= 3
        ORDER BY r.session_date DESC
        LIMIT ?
    """, (n_sessions,))
    sessions = [r[0] for r in cur.fetchall()]

    test_cases = []
    for date in sessions:
        # 이 세션에서 처음 WATCH였다가 나중에 BUY_READY된 것 (positive)
        cur.execute("""
            WITH first_watch AS (
                SELECT r.ticker, MIN(c.called_at) watch_at,
                       r.change_pct, r.liquidity_bucket, r.primary_bucket,
                       r.trainer_plan_a_score
                FROM audit_candidate_rows r
                JOIN audit_claude_calls c ON r.call_id=c.call_id
                WHERE r.market='US' AND r.in_prompt=1
                  AND r.session_date=? AND r.claude_action='WATCH'
                  AND c.label LIKE '%select%'
                GROUP BY r.ticker
            ),
            later_br AS (
                SELECT r.ticker, MIN(c.called_at) br_at
                FROM audit_candidate_rows r
                JOIN audit_claude_calls c ON r.call_id=c.call_id
                WHERE r.market='US' AND r.in_prompt=1
                  AND r.session_date=?
                  AND r.claude_action IN ('BUY_READY','PROBE_READY')
                GROUP BY r.ticker
            )
            SELECT fw.ticker, fw.change_pct, fw.liquidity_bucket,
                   fw.primary_bucket, fw.trainer_plan_a_score,
                   'SHOULD_PROMOTE' expected,
                   CAST((julianday(lb.br_at)-julianday(fw.watch_at))*24*60 AS INT) elapsed
            FROM first_watch fw
            JOIN later_br lb ON fw.ticker=lb.ticker
              AND lb.br_at > fw.watch_at
            WHERE CAST((julianday(lb.br_at)-julianday(fw.watch_at))*24*60 AS INT) <= 60
            LIMIT 4
        """, (date, date))
        positives = cur.fetchall()

        # WATCH였다가 끝까지 BUY_READY 안 된 것 (negative)
        cur.execute("""
            WITH first_watch AS (
                SELECT r.ticker, MIN(c.called_at) watch_at,
                       r.change_pct, r.liquidity_bucket, r.primary_bucket,
                       r.trainer_plan_a_score
                FROM audit_candidate_rows r
                JOIN audit_claude_calls c ON r.call_id=c.call_id
                WHERE r.market='US' AND r.in_prompt=1
                  AND r.session_date=? AND r.claude_action='WATCH'
                  AND c.label LIKE '%select%'
                GROUP BY r.ticker
            )
            SELECT fw.ticker, fw.change_pct, fw.liquidity_bucket,
                   fw.primary_bucket, fw.trainer_plan_a_score,
                   'SHOULD_KEEP' expected, 0 elapsed
            FROM first_watch fw
            WHERE NOT EXISTS (
                SELECT 1 FROM audit_candidate_rows r2
                JOIN audit_claude_calls c2 ON r2.call_id=c2.call_id
                WHERE r2.market='US' AND r2.in_prompt=1
                  AND r2.session_date=? AND r2.ticker=fw.ticker
                  AND r2.claude_action IN ('BUY_READY','PROBE_READY')
                  AND c2.called_at > fw.watch_at
            )
            LIMIT 4
        """, (date, date))
        negatives = cur.fetchall()

        if positives and negatives:
            test_cases.append({
                "date": date,
                "positives": positives,
                "negatives": negatives
            })
    return test_cases


def build_candidate_lines(cases):
    lines = []
    all_tickers = []
    for r in cases:
        ticker, chg, liq, bucket, score, expected, elapsed = r
        chg_s   = f"{chg:+.1f}%" if chg else "?%"
        score_s = f"s={score:.0f}" if score else "s=?"
        status  = "WATCH" if "KEEP" in expected else "WATCH(승격후보)"
        lines.append(f"{ticker} {chg_s} liq={liq or '?'} bucket={bucket or '?'} {score_s} [{status}]")
        all_tickers.append((ticker, expected))
    return "\n".join(lines), all_tickers


def call_fasttrack(candidates_text: str, market_ctx: str) -> tuple[dict, object]:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    prompt = FAST_TRACK_PROMPT.format(
        market_ctx=market_ctx,
        candidates=candidates_text
    )
    resp = client.messages.create(
        model=MODEL, max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    raw = resp.content[0].text.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]
    try:
        start, end = raw.find("{"), raw.rfind("}") + 1
        return json.loads(raw[start:end]), resp.usage
    except Exception:
        return {"results": []}, resp.usage


def run():
    con = sqlite3.connect(DB_PATH)
    test_cases = get_test_cases(con, n_sessions=8)
    con.close()

    print(f"테스트 세션: {len(test_cases)}개\n")

    total_promote_correct = 0
    total_promote_total   = 0
    total_keep_correct    = 0
    total_keep_total      = 0
    total_in = 0
    total_out = 0

    for tc in test_cases:
        date = tc["date"]
        all_cands = tc["positives"] + tc["negatives"]
        cands_text, ticker_expected = build_candidate_lines(all_cands)
        market_ctx = "RISK_ON SP+0.8%"  # 고정값 (실제론 snapshot에서 가져옴)

        result, usage = call_fasttrack(cands_text, market_ctx)
        total_in  += usage.input_tokens
        total_out += usage.output_tokens

        verdict_map = {
            r["ticker"].upper(): r["verdict"]
            for r in result.get("results", [])
        }

        print(f"[{date}] pos={len(tc['positives'])} neg={len(tc['negatives'])}")
        session_ok = True
        for ticker, expected in ticker_expected:
            verdict = verdict_map.get(ticker.upper(), "MISSING")
            if "PROMOTE" in expected:
                total_promote_total += 1
                correct = verdict == "PROMOTE"
                if correct: total_promote_correct += 1
                mark = "✓" if correct else "✗"
                print(f"  {mark} {ticker:6s} 기대=PROMOTE  실제={verdict}")
            else:
                total_keep_total += 1
                correct = verdict in ("KEEP", "EXPIRE")
                if correct: total_keep_correct += 1
                mark = "✓" if correct else "✗"
                print(f"  {mark} {ticker:6s} 기대=KEEP     실제={verdict}")
        print()
        time.sleep(0.8)

    print("=" * 55)
    print("Fast-Track 품질 결과")
    print("=" * 55)
    promote_acc = total_promote_correct/total_promote_total*100 if total_promote_total else 0
    keep_acc    = total_keep_correct/total_keep_total*100       if total_keep_total    else 0
    cost = (total_in*0.8 + total_out*4)/1e6
    print(f"  PROMOTE 정확도: {total_promote_correct}/{total_promote_total} = {promote_acc:.0f}%")
    print(f"  KEEP    정확도: {total_keep_correct}/{total_keep_total} = {keep_acc:.0f}%")
    print(f"  API 비용: ${cost:.4f}  tok={total_in}+{total_out}")
    print()
    if promote_acc >= 70 and keep_acc >= 70:
        print("  ✓ 품질 기준 충족 (70% 이상)")
    else:
        print("  ✗ 품질 기준 미달 — 프롬프트 개선 필요")


if __name__ == "__main__":
    run()
