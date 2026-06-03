import sqlite3

db = r'E:\code\claudetrade\data\audit\candidate_audit.db'
con = sqlite3.connect(db)
cur = con.cursor()

# IGNORE→BR 된 것 vs 안 된 것: rank/score 비교
cur.execute("""
WITH first_ignore AS (
    SELECT r.ticker, r.session_date,
           MIN(c.called_at) first_at,
           r.prompt_rank, r.trainer_plan_a_score,
           r.liquidity_bucket, r.change_pct
    FROM audit_candidate_rows r
    JOIN audit_claude_calls c ON r.call_id=c.call_id
    WHERE r.market='US' AND r.in_prompt=1
      AND (r.claude_action='' OR r.claude_action IS NULL)
      AND r.session_date >= '2026-05-01'
      AND c.label LIKE '%select%'
    GROUP BY r.ticker, r.session_date
),
first_br AS (
    SELECT DISTINCT ticker, session_date
    FROM audit_candidate_rows
    WHERE market='US' AND in_prompt=1
      AND claude_action IN ('BUY_READY','PROBE_READY')
      AND session_date >= '2026-05-01'
)
SELECT
    CASE WHEN fb.ticker IS NOT NULL THEN 'IGNORE_to_BR' ELSE 'IGNORE_stays' END fate,
    AVG(fi.prompt_rank) avg_rank,
    AVG(fi.trainer_plan_a_score) avg_score,
    AVG(fi.change_pct) avg_chg,
    AVG(CASE WHEN fi.liquidity_bucket='high' THEN 1.0 ELSE 0.0 END) liq_high_rate,
    COUNT(*) cnt
FROM first_ignore fi
LEFT JOIN first_br fb ON fi.ticker=fb.ticker AND fi.session_date=fb.session_date
GROUP BY 1
""")
print('=== IGNORE→BR vs IGNORE→stays 특성 ===')
for r in cur.fetchall():
    print(f'  {r[0]:15s} rank={r[1]:.1f} score={r[2]:.1f} chg={r[3]:+.1f}% liq_high={r[4]*100:.0f}% n={r[5]}')

# rank 기준 IGNORE→BR 비율 — fast-track 대상 선별 기준
cur.execute("""
WITH first_ignore AS (
    SELECT r.ticker, r.session_date, r.prompt_rank
    FROM audit_candidate_rows r
    JOIN audit_claude_calls c ON r.call_id=c.call_id
    WHERE r.market='US' AND r.in_prompt=1
      AND (r.claude_action='' OR r.claude_action IS NULL)
      AND r.session_date >= '2026-05-01'
      AND c.label LIKE '%select%'
    GROUP BY r.ticker, r.session_date
    HAVING MIN(c.called_at)=MIN(c.called_at)
),
first_br AS (
    SELECT DISTINCT ticker, session_date
    FROM audit_candidate_rows
    WHERE market='US' AND in_prompt=1
      AND claude_action IN ('BUY_READY','PROBE_READY')
      AND session_date >= '2026-05-01'
)
SELECT
    CASE WHEN fi.prompt_rank <= 10 THEN 'rank01-10'
         WHEN fi.prompt_rank <= 20 THEN 'rank11-20'
         ELSE 'rank21+' END rank_bucket,
    COUNT(*) total,
    SUM(CASE WHEN fb.ticker IS NOT NULL THEN 1 ELSE 0 END) became_br,
    ROUND(SUM(CASE WHEN fb.ticker IS NOT NULL THEN 1 ELSE 0 END)*100.0/COUNT(*),1) br_rate
FROM first_ignore fi
LEFT JOIN first_br fb ON fi.ticker=fb.ticker AND fi.session_date=fb.session_date
GROUP BY 1 ORDER BY 1
""")
print()
print('=== rank별 IGNORE→BR 전환율 ===')
for r in cur.fetchall():
    print(f'  {r[0]}: total={r[1]} became_BR={r[2]} rate={r[3]}%')

con.close()

# 비용 계산
HAIKU_IN=0.8; HAIKU_OUT=4.0; SONNET_IN=3.0; SONNET_OUT=15.0
tier2_full  = (4946*SONNET_IN + 1200*SONNET_OUT) / 1e6
tier1_60    = (1500*HAIKU_IN  + 250*HAIKU_OUT)   / 1e6
fast_track  = (800*HAIKU_IN   + 200*HAIKU_OUT)   / 1e6  # 20개 압축, Haiku
current     = tier2_full * 21
session_hrs = 4.5

print()
print('='*60)
print('최종 옵션별 비용/지연 비교')
print('='*60)

configs = [
    ('현행 13분',         13,  0,   0,   '100%', 6),
    ('30분+15분WATCH',    30,  20,  12,  '~100%',15),
    ('30분+10분FastTrack',30,  20,  27,  '~100%',10),
    ('20분+10분FastTrack',20,  20,  27,  '~100%', 8),
]
for name, full_min, t1_calls, ft_calls, cov, avg_delay in configs:
    full_calls = int(session_hrs*60/full_min)
    cost = tier2_full*full_calls + tier1_60*t1_calls + fast_track*ft_calls
    saving = (current-cost)/current*100
    print(f'  {name:25s} ${cost:.3f}/일 절감{saving:3.0f}% 커버:{cov} 평균지연:~{avg_delay}분')
