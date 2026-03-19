"""
dashboard_server.py
Flask 기반 트레이딩 대시보드 서버

실행: python dashboard_server.py
접속: http://localhost:5000

기능:
  - 오늘 수익/손실 실시간
  - 3명 판단 이유 + 결과
  - 누적 성과 그래프
  - 매매 내역 상세
  - 판단 패턴 분석
"""

from flask import Flask, jsonify, render_template_string
from pathlib import Path
from datetime import datetime, date, timedelta
import json, sys, os
from runtime_paths import get_runtime_path

sys.path.insert(0, str(Path(__file__).parent.parent))

app = Flask(__name__)

BASE_DIR    = Path(__file__).parent.parent
LOG_DIR     = get_runtime_path("logs", "daily_judgment", make_parents=False)
BRAIN_PATH  = get_runtime_path("state", "brain.json")


# ── 데이터 로더 ────────────────────────────────────────────────────────────────

def load_records(days: int = 60, market: str = "KR") -> list[dict]:
    records = []
    for path in sorted(LOG_DIR.glob(f"*_{market}.json"))[-days:]:
        try:
            with open(path, encoding="utf-8") as f:
                records.append(json.load(f))
        except Exception:
            pass
    return records

def load_brain() -> dict:
    source = BRAIN_PATH if BRAIN_PATH.exists() else (BASE_DIR / "claude_memory" / "brain.json")
    if source.exists():
        with open(source, encoding="utf-8") as f:
            return json.load(f)
    return {}

def load_today(market: str = "KR") -> dict:
    today = date.today().strftime("%Y%m%d")
    path  = LOG_DIR / f"{today}_{market}.json"
    if path.exists():
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    # 없으면 가장 최근 날짜 반환
    files = sorted(LOG_DIR.glob(f"*_{market}.json"))
    if files:
        with open(files[-1], encoding="utf-8") as f:
            return json.load(f)
    return {}


# ── API 엔드포인트 ─────────────────────────────────────────────────────────────

@app.route("/api/summary")
def api_summary():
    """오늘 + 누적 요약"""
    records = load_records(60)
    if not records:
        return jsonify({})

    today_rec = load_today()
    result    = today_rec.get("actual_result", {})

    # 누적 성과
    wins       = [r for r in records if r.get("actual_result", {}).get("win")]
    total_pnl  = sum(r.get("actual_result", {}).get("pnl_pct", 0) for r in records)
    win_rate   = len(wins) / len(records) * 100 if records else 0

    # 연속 승패
    streak = 0
    streak_type = None
    for r in reversed(records):
        w = r.get("actual_result", {}).get("win")
        if streak_type is None:
            streak_type = "win" if w else "lose"
            streak = 1
        elif (w and streak_type == "win") or (not w and streak_type == "lose"):
            streak += 1
        else:
            break

    return jsonify({
        "today": {
            "date":     today_rec.get("date", ""),
            "pnl_pct":  result.get("pnl_pct", 0),
            "pnl_krw":  result.get("pnl_krw", 0),
            "win":      result.get("win", False),
            "trades":   result.get("trades", 0),
            "mode":     today_rec.get("consensus", {}).get("mode", "-"),
            "cumulative": result.get("cumulative", 10_000_000),
        },
        "period": {
            "days":      len(records),
            "wins":      len(wins),
            "losses":    len(records) - len(wins),
            "win_rate":  round(win_rate, 1),
            "total_pnl": round(total_pnl, 2),
            "streak":    streak,
            "streak_type": streak_type,
        }
    })


@app.route("/api/judgments")
def api_judgments():
    """오늘 3명 판단 상세"""
    rec = load_today()
    if not rec:
        return jsonify({})

    judgments = rec.get("judgments", {})
    postmortem = rec.get("postmortem", {})

    return jsonify({
        "date":   rec.get("date", ""),
        "bull":   {**judgments.get("bull", {}),
                   "result": postmortem.get("bull_result", ""),
                   "why": postmortem.get("bull_why", "")},
        "bear":   {**judgments.get("bear", {}),
                   "result": postmortem.get("bear_result", ""),
                   "why": postmortem.get("bear_why", "")},
        "neutral":{**judgments.get("neutral", {}),
                   "result": postmortem.get("neutral_result", ""),
                   "why": postmortem.get("neutral_why", "")},
        "consensus": rec.get("consensus", {}),
        "lesson": postmortem.get("key_lesson", ""),
    })


@app.route("/api/chart/equity")
def api_equity_chart():
    """누적 자산 곡선 데이터"""
    records = load_records(60)
    labels, values, pnls, wins = [], [], [], []

    for r in records:
        result = r.get("actual_result", {})
        labels.append(r.get("date", "")[-5:])   # MM-DD
        values.append(result.get("cumulative", 0))
        pnls.append(result.get("pnl_pct", 0))
        wins.append(result.get("win", False))

    return jsonify({"labels": labels, "equity": values,
                    "pnl": pnls, "wins": wins})


@app.route("/api/chart/analyst")
def api_analyst_chart():
    """분석가별 적중률 추이 (최근 30일)"""
    records = load_records(30)
    labels  = []
    bull_hits, bear_hits, neut_hits = [], [], []

    window = 7   # 7일 이동평균
    for i, r in enumerate(records):
        labels.append(r.get("date", "")[-5:])
        start = max(0, i - window + 1)
        window_recs = records[start:i+1]
        def rate(analyst):
            result_key = f"{analyst}_result"
            hits = sum(1 for rec in window_recs
                       if rec.get("postmortem", {}).get(result_key) == "HIT")
            return round(hits / len(window_recs) * 100, 1)

        bull_hits.append(rate("bull"))
        bear_hits.append(rate("bear"))
        neut_hits.append(rate("neutral"))

    return jsonify({"labels": labels, "bull": bull_hits,
                    "bear": bear_hits, "neutral": neut_hits})


@app.route("/api/trades")
def api_trades():
    """최근 매매 내역"""
    records = load_records(30)
    trades  = []
    for r in records:
        for t in r.get("trades", []):
            trades.append({
                "date":     r.get("date", ""),
                "ticker":   t.get("ticker", ""),
                "strategy": t.get("strategy", ""),
                "pnl_pct":  round(t.get("pnl_pct", 0), 2),
                "pnl_krw":  t.get("pnl_krw", 0),
                "reason":   t.get("reason", ""),
                "hold_min": t.get("hold_min", 0),
            })
    return jsonify(sorted(trades, key=lambda x: x["date"], reverse=True)[:50])


@app.route("/api/patterns")
def api_patterns():
    """판단 패턴 분석 - 왜 맞았나/왜 틀렸나"""
    records = load_records(60)
    lessons = {}
    modes   = {}

    for r in records:
        # 교훈 집계
        lesson = r.get("postmortem", {}).get("key_lesson", "")
        if lesson:
            lessons[lesson] = lessons.get(lesson, 0) + 1

        # 모드별 성과
        mode   = r.get("consensus", {}).get("mode", "")
        result = r.get("actual_result", {})
        if mode:
            if mode not in modes:
                modes[mode] = {"count": 0, "wins": 0, "total_pnl": 0}
            modes[mode]["count"]     += 1
            modes[mode]["wins"]      += 1 if result.get("win") else 0
            modes[mode]["total_pnl"] += result.get("pnl_pct", 0)

    # 모드별 평균 계산
    for m in modes:
        c = modes[m]["count"]
        modes[m]["win_rate"] = round(modes[m]["wins"]/c*100, 1) if c else 0
        modes[m]["avg_pnl"]  = round(modes[m]["total_pnl"]/c, 2) if c else 0

    top_lessons = sorted(lessons.items(), key=lambda x: x[1], reverse=True)[:10]

    return jsonify({
        "lessons": [{"text": k, "count": v} for k, v in top_lessons],
        "modes":   modes,
    })


@app.route("/api/brain")
def api_brain():
    """brain.json 현재 상태"""
    brain = load_brain()
    market = brain.get("markets", {}).get("KR", {})
    return jsonify({
        "trained_days": market.get("trained_days", 0),
        "regime":       market.get("current_regime", "unknown"),
        "analyst":      market.get("analyst_performance", {}),
        "beliefs":      market.get("current_beliefs", {}),
        "version":      brain.get("meta", {}).get("version", 0),
        "updated":      brain.get("meta", {}).get("last_updated", ""),
    })


# ── 메인 HTML 대시보드 ─────────────────────────────────────────────────────────

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Trading Dashboard</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;600;700&family=Noto+Sans+KR:wght@300;400;500;700&display=swap');

  :root {
    --bg:       #0a0e1a;
    --surface:  #111827;
    --border:   #1f2937;
    --text:     #e2e8f0;
    --muted:    #64748b;
    --green:    #10b981;
    --red:      #ef4444;
    --yellow:   #f59e0b;
    --blue:     #3b82f6;
    --purple:   #8b5cf6;
    --cyan:     #06b6d4;
    --mono:     'JetBrains Mono', monospace;
    --sans:     'Noto Sans KR', sans-serif;
  }

  * { margin:0; padding:0; box-sizing:border-box; }
  body {
    background: var(--bg);
    color: var(--text);
    font-family: var(--sans);
    min-height: 100vh;
  }

  /* 헤더 */
  header {
    display: flex; align-items: center; justify-content: space-between;
    padding: 16px 24px;
    background: var(--surface);
    border-bottom: 1px solid var(--border);
    position: sticky; top: 0; z-index: 100;
  }
  .logo {
    font-family: var(--mono); font-weight: 700; font-size: 18px;
    color: var(--cyan); letter-spacing: 2px;
  }
  .logo span { color: var(--muted); font-weight: 300; }
  #clock {
    font-family: var(--mono); font-size: 14px; color: var(--muted);
  }
  .status-dot {
    width: 8px; height: 8px; border-radius: 50%;
    background: var(--green);
    box-shadow: 0 0 8px var(--green);
    animation: pulse 2s infinite;
    display: inline-block; margin-right: 8px;
  }
  @keyframes pulse {
    0%,100% { opacity:1; } 50% { opacity:0.4; }
  }

  /* 레이아웃 */
  main { padding: 20px 24px; max-width: 1600px; margin: 0 auto; }

  .grid-4 {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 16px; margin-bottom: 20px;
  }
  .grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px; margin-bottom: 20px;
  }
  .grid-3 {
    display: grid;
    grid-template-columns: 2fr 1fr 1fr;
    gap: 16px; margin-bottom: 20px;
  }

  /* 카드 */
  .card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px;
    padding: 20px;
    position: relative; overflow: hidden;
  }
  .card::before {
    content: '';
    position: absolute; top: 0; left: 0; right: 0;
    height: 2px;
  }
  .card.green::before { background: var(--green); }
  .card.red::before   { background: var(--red); }
  .card.blue::before  { background: var(--blue); }
  .card.yellow::before{ background: var(--yellow); }
  .card.purple::before{ background: var(--purple); }
  .card.cyan::before  { background: var(--cyan); }

  .card-label {
    font-size: 11px; font-weight: 500; letter-spacing: 1.5px;
    color: var(--muted); text-transform: uppercase; margin-bottom: 8px;
  }
  .card-value {
    font-family: var(--mono); font-size: 28px; font-weight: 700;
    line-height: 1.1;
  }
  .card-sub {
    font-size: 12px; color: var(--muted); margin-top: 6px;
    font-family: var(--mono);
  }
  .up   { color: var(--green); }
  .down { color: var(--red); }
  .neutral-color { color: var(--yellow); }

  /* 분석가 판단 카드 */
  .analyst-grid {
    display: grid; grid-template-columns: repeat(3, 1fr);
    gap: 16px; margin-bottom: 20px;
  }
  .analyst-card {
    background: var(--surface);
    border: 1px solid var(--border);
    border-radius: 12px; padding: 20px;
  }
  .analyst-header {
    display: flex; align-items: center; gap: 10px; margin-bottom: 14px;
  }
  .analyst-icon {
    width: 36px; height: 36px; border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px; font-weight: 700;
  }
  .analyst-icon.bull { background: rgba(16,185,129,0.2); color: var(--green); }
  .analyst-icon.bear { background: rgba(239,68,68,0.2);  color: var(--red); }
  .analyst-icon.neut { background: rgba(245,158,11,0.2); color: var(--yellow); }

  .analyst-name { font-weight: 600; font-size: 15px; }
  .analyst-stance {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-family: var(--mono); font-weight: 600;
    margin-top: 4px;
  }
  .stance-bull { background: rgba(16,185,129,0.15); color: var(--green); border: 1px solid rgba(16,185,129,0.3); }
  .stance-bear { background: rgba(239,68,68,0.15);  color: var(--red);   border: 1px solid rgba(239,68,68,0.3); }
  .stance-neut { background: rgba(245,158,11,0.15); color: var(--yellow);border: 1px solid rgba(245,158,11,0.3); }

  .analyst-confidence {
    font-family: var(--mono); font-size: 12px; color: var(--muted);
    margin: 10px 0 8px;
  }
  .conf-bar {
    height: 4px; background: var(--border); border-radius: 2px;
    margin-bottom: 12px; overflow: hidden;
  }
  .conf-bar-fill { height: 100%; border-radius: 2px; transition: width 0.5s; }

  .analyst-reason {
    font-size: 13px; line-height: 1.6; color: var(--text);
    padding: 10px; background: rgba(255,255,255,0.03);
    border-radius: 8px; border-left: 3px solid var(--border);
    margin-bottom: 10px;
  }
  .analyst-result {
    display: flex; align-items: center; gap: 6px;
    font-family: var(--mono); font-size: 12px; margin-top: 10px;
  }
  .result-badge {
    padding: 2px 8px; border-radius: 4px; font-weight: 700;
    font-size: 11px;
  }
  .hit  { background: rgba(16,185,129,0.2); color: var(--green); }
  .miss { background: rgba(239,68,68,0.2);  color: var(--red); }
  .partial { background: rgba(245,158,11,0.2); color: var(--yellow); }

  .postmortem {
    font-size: 12px; color: var(--muted); margin-top: 8px;
    line-height: 1.5; font-style: italic;
  }

  /* 차트 */
  .chart-container { position: relative; height: 220px; }

  /* 테이블 */
  .table-wrap { overflow-x: auto; }
  table { width: 100%; border-collapse: collapse; font-size: 13px; }
  th {
    text-align: left; padding: 8px 12px;
    font-family: var(--mono); font-size: 11px;
    color: var(--muted); letter-spacing: 1px;
    border-bottom: 1px solid var(--border);
    background: rgba(255,255,255,0.02);
  }
  td {
    padding: 10px 12px;
    border-bottom: 1px solid rgba(31,41,55,0.5);
    font-family: var(--mono); font-size: 12px;
  }
  tr:hover td { background: rgba(255,255,255,0.02); }

  /* 패턴 */
  .lesson-item {
    display: flex; align-items: flex-start; gap: 12px;
    padding: 10px 0; border-bottom: 1px solid var(--border);
  }
  .lesson-count {
    background: rgba(59,130,246,0.2); color: var(--blue);
    border-radius: 4px; padding: 2px 8px;
    font-family: var(--mono); font-size: 12px; font-weight: 700;
    white-space: nowrap; min-width: 40px; text-align: center;
  }
  .lesson-text { font-size: 13px; line-height: 1.5; }

  /* 모드 배지 */
  .mode-badge {
    padding: 3px 8px; border-radius: 4px;
    font-family: var(--mono); font-size: 11px; font-weight: 600;
  }
  .mode-AGGRESSIVE    { background: rgba(16,185,129,0.2);  color: var(--green); }
  .mode-MODERATE_BULL { background: rgba(59,130,246,0.2);  color: var(--blue); }
  .mode-CAUTIOUS      { background: rgba(245,158,11,0.2);  color: var(--yellow); }
  .mode-DEFENSIVE     { background: rgba(139,92,246,0.2);  color: var(--purple); }
  .mode-HALT          { background: rgba(239,68,68,0.2);   color: var(--red); }

  /* brain 상태 */
  .brain-stats {
    display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px;
    margin-top: 12px;
  }
  .brain-stat {
    background: rgba(255,255,255,0.03); border-radius: 8px;
    padding: 10px; text-align: center;
  }
  .brain-stat-val {
    font-family: var(--mono); font-size: 20px; font-weight: 700;
    margin-bottom: 4px;
  }
  .brain-stat-label { font-size: 11px; color: var(--muted); }

  /* 섹션 타이틀 */
  .section-title {
    font-size: 12px; font-weight: 600; letter-spacing: 2px;
    color: var(--muted); text-transform: uppercase;
    margin-bottom: 14px; display: flex; align-items: center; gap: 8px;
  }
  .section-title::after {
    content: ''; flex: 1; height: 1px; background: var(--border);
  }

  /* 새로고침 버튼 */
  .refresh-btn {
    background: rgba(59,130,246,0.2); border: 1px solid rgba(59,130,246,0.4);
    color: var(--blue); padding: 6px 14px; border-radius: 6px;
    cursor: pointer; font-family: var(--mono); font-size: 12px;
    transition: all 0.2s;
  }
  .refresh-btn:hover { background: rgba(59,130,246,0.35); }

  /* 연속 승/패 */
  .streak {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 3px 10px; border-radius: 20px;
    font-family: var(--mono); font-size: 13px; font-weight: 700;
  }
  .streak.win  { background: rgba(16,185,129,0.15); color: var(--green); }
  .streak.lose { background: rgba(239,68,68,0.15);  color: var(--red); }

  /* 오늘 교훈 */
  .lesson-box {
    background: rgba(245,158,11,0.08);
    border: 1px solid rgba(245,158,11,0.2);
    border-radius: 8px; padding: 12px 16px;
    font-size: 13px; line-height: 1.6;
    color: var(--yellow); margin-top: 12px;
  }
  .lesson-box::before {
    content: '💡 오늘의 교훈: ';
    font-weight: 700;
  }
</style>
</head>
<body>

<header>
  <div class="logo">TRADING<span>BRAIN</span></div>
  <div style="display:flex; align-items:center; gap:16px;">
    <span><span class="status-dot"></span><span style="font-size:12px;color:var(--muted)">LIVE</span></span>
    <span id="clock"></span>
    <button class="refresh-btn" onclick="loadAll()">↺ 새로고침</button>
  </div>
</header>

<main>

  <!-- 상단 요약 카드 -->
  <div class="grid-4" id="summary-cards">
    <div class="card cyan">
      <div class="card-label">오늘 손익</div>
      <div class="card-value" id="today-pnl">--</div>
      <div class="card-sub" id="today-krw">-- 원</div>
    </div>
    <div class="card blue">
      <div class="card-label">누적 자산</div>
      <div class="card-value" id="cumulative">--</div>
      <div class="card-sub" id="today-mode">모드: --</div>
    </div>
    <div class="card green">
      <div class="card-label">기간 승률</div>
      <div class="card-value" id="win-rate">--</div>
      <div class="card-sub" id="win-detail">-- 승 / -- 패</div>
    </div>
    <div class="card yellow">
      <div class="card-label">연속 기록</div>
      <div class="card-value" id="streak-val">--</div>
      <div class="card-sub" id="total-pnl">누적 수익: --%</div>
    </div>
  </div>

  <!-- 3명 판단 -->
  <div class="section-title">오늘 마이너리티 판단</div>
  <div class="analyst-grid" id="analyst-section">
    <!-- JS로 채움 -->
  </div>

  <!-- 그래프 -->
  <div class="grid-2">
    <div class="card blue">
      <div class="section-title">누적 자산 곡선</div>
      <div class="chart-container">
        <canvas id="equityChart"></canvas>
      </div>
    </div>
    <div class="card purple">
      <div class="section-title">분석가 적중률 추이 (7일 이동평균)</div>
      <div class="chart-container">
        <canvas id="analystChart"></canvas>
      </div>
    </div>
  </div>

  <!-- 매매 내역 + 패턴 -->
  <div class="grid-3">
    <div class="card">
      <div class="section-title">최근 매매 내역</div>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>날짜</th><th>종목</th><th>전략</th>
              <th>손익%</th><th>손익(원)</th><th>사유</th><th>보유</th>
            </tr>
          </thead>
          <tbody id="trades-tbody"></tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="section-title">반복 교훈 패턴</div>
      <div id="lessons-list"></div>
    </div>

    <div class="card">
      <div class="section-title">모드별 성과</div>
      <div id="modes-list"></div>
      <div class="section-title" style="margin-top:20px;">Brain 상태</div>
      <div id="brain-status"></div>
    </div>
  </div>

</main>

<script>
let charts = {};

// ── 유틸 ─────────────────────────────────────────────────────────────────────
const fmt = {
  pct:  v => (v >= 0 ? '+' : '') + v.toFixed(2) + '%',
  krw:  v => (v >= 0 ? '+' : '') + Math.round(v).toLocaleString() + '원',
  asset: v => Math.round(v).toLocaleString() + '원',
};

function colorClass(v) {
  return v > 0 ? 'up' : v < 0 ? 'down' : 'neutral-color';
}

// ── 시계 ─────────────────────────────────────────────────────────────────────
function updateClock() {
  const now = new Date();
  document.getElementById('clock').textContent =
    now.toLocaleString('ko-KR', {
      year:'numeric', month:'2-digit', day:'2-digit',
      hour:'2-digit', minute:'2-digit', second:'2-digit'
    });
}
setInterval(updateClock, 1000); updateClock();

// ── 요약 로드 ─────────────────────────────────────────────────────────────────
async function loadSummary() {
  const d = await fetch('/api/summary').then(r => r.json());
  if (!d.today) return;

  const t = d.today, p = d.period;
  const pnlEl = document.getElementById('today-pnl');
  pnlEl.textContent = fmt.pct(t.pnl_pct);
  pnlEl.className = 'card-value ' + colorClass(t.pnl_pct);
  document.getElementById('today-krw').textContent = fmt.krw(t.pnl_krw);
  document.getElementById('cumulative').textContent = fmt.asset(t.cumulative);
  document.getElementById('today-mode').innerHTML =
    `모드: <span class="mode-badge mode-${t.mode}">${t.mode}</span>  거래 ${t.trades}건`;

  const wrEl = document.getElementById('win-rate');
  wrEl.textContent = p.win_rate + '%';
  wrEl.className = 'card-value ' + (p.win_rate >= 55 ? 'up' : p.win_rate >= 45 ? 'neutral-color' : 'down');
  document.getElementById('win-detail').textContent =
    `${p.wins}승 / ${p.losses}패  (${p.days}일)`;

  const sk = document.getElementById('streak-val');
  sk.innerHTML = `<span class="streak ${p.streak_type}">${p.streak_type === 'win' ? '🔥' : '❄️'} ${p.streak}연속</span>`;
  document.getElementById('total-pnl').textContent =
    `누적 수익: ${fmt.pct(p.total_pnl)}`;
}

// ── 판단 카드 ─────────────────────────────────────────────────────────────────
function analystCard(key, data, label, iconClass, stanceClass) {
  const conf = Math.round((data.confidence || 0) * 100);
  const result = data.result || '';
  const resultBadge = result === 'HIT' ? 'hit' :
                      result === 'MISS' ? 'miss' : 'partial';
  const barColor = iconClass === 'bull' ? 'var(--green)' :
                   iconClass === 'bear' ? 'var(--red)' : 'var(--yellow)';

  return `
  <div class="analyst-card">
    <div class="analyst-header">
      <div class="analyst-icon ${iconClass}">${label[0]}</div>
      <div>
        <div class="analyst-name">${label}</div>
        <div class="analyst-stance ${stanceClass}">${data.stance || '-'}</div>
      </div>
      ${result ? `<div style="margin-left:auto">
        <span class="result-badge ${resultBadge}">${result}</span>
      </div>` : ''}
    </div>
    <div class="analyst-confidence">신뢰도 ${conf}%</div>
    <div class="conf-bar">
      <div class="conf-bar-fill" style="width:${conf}%;background:${barColor}"></div>
    </div>
    <div class="analyst-reason">📋 ${data.key_reason || '-'}</div>
    ${data.why ? `<div class="postmortem">→ ${data.why}</div>` : ''}
  </div>`;
}

async function loadJudgments() {
  const d = await fetch('/api/judgments').then(r => r.json());
  if (!d.bull) return;

  document.getElementById('analyst-section').innerHTML =
    analystCard('bull', d.bull, '🟢 Bull 분석가', 'bull', 'stance-bull') +
    analystCard('bear', d.bear, '🔴 Bear 분석가', 'bear', 'stance-bear') +
    analystCard('neutral', d.neutral, '⚪ Neutral 분석가', 'neut', 'stance-neut');

  if (d.lesson) {
    document.getElementById('analyst-section').innerHTML +=
      `<div class="lesson-box" style="grid-column:1/-1">${d.lesson}</div>`;
  }
}

// ── 차트 ─────────────────────────────────────────────────────────────────────
async function loadEquityChart() {
  const d = await fetch('/api/chart/equity').then(r => r.json());

  const colors = d.wins.map(w => w ?
    'rgba(16,185,129,0.8)' : 'rgba(239,68,68,0.8)');

  if (charts.equity) charts.equity.destroy();
  charts.equity = new Chart(
    document.getElementById('equityChart').getContext('2d'), {
    type: 'bar',
    data: {
      labels: d.labels,
      datasets: [
        {
          type: 'line',
          label: '누적 자산',
          data: d.equity,
          borderColor: 'rgba(6,182,212,0.9)',
          backgroundColor: 'rgba(6,182,212,0.05)',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.3,
          yAxisID: 'y1',
          fill: true,
        },
        {
          type: 'bar',
          label: '일별 손익%',
          data: d.pnl,
          backgroundColor: colors,
          yAxisID: 'y2',
          barThickness: 'flex',
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#64748b', font: { size: 11 } } } },
      scales: {
        x: { ticks: { color: '#64748b', font: { size: 10 } },
             grid: { color: 'rgba(31,41,55,0.5)' } },
        y1: { position: 'left',
              ticks: { color: '#06b6d4', font: { size: 10 },
                       callback: v => (v/10000000*100-100).toFixed(1)+'%' },
              grid: { color: 'rgba(31,41,55,0.3)' } },
        y2: { position: 'right',
              ticks: { color: '#64748b', font: { size: 10 },
                       callback: v => v.toFixed(1)+'%' },
              grid: { display: false } },
      }
    }
  });
}

async function loadAnalystChart() {
  const d = await fetch('/api/chart/analyst').then(r => r.json());

  if (charts.analyst) charts.analyst.destroy();
  charts.analyst = new Chart(
    document.getElementById('analystChart').getContext('2d'), {
    type: 'line',
    data: {
      labels: d.labels,
      datasets: [
        { label: 'Bull', data: d.bull,
          borderColor: 'var(--green)', backgroundColor: 'rgba(16,185,129,0.1)',
          borderWidth: 2, pointRadius: 0, tension: 0.4, fill: true },
        { label: 'Bear', data: d.bear,
          borderColor: 'var(--red)',   backgroundColor: 'rgba(239,68,68,0.1)',
          borderWidth: 2, pointRadius: 0, tension: 0.4, fill: true },
        { label: 'Neutral', data: d.neutral,
          borderColor: 'var(--yellow)', backgroundColor: 'rgba(245,158,11,0.1)',
          borderWidth: 2, pointRadius: 0, tension: 0.4, fill: false },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#64748b', font: { size: 11 } } } },
      scales: {
        x: { ticks: { color: '#64748b', font: { size: 10 } },
             grid: { color: 'rgba(31,41,55,0.5)' } },
        y: { min: 0, max: 100,
             ticks: { color: '#64748b', font: { size: 10 },
                      callback: v => v + '%' },
             grid: { color: 'rgba(31,41,55,0.3)' } },
      }
    }
  });
}

// ── 매매 내역 ─────────────────────────────────────────────────────────────────
async function loadTrades() {
  const trades = await fetch('/api/trades').then(r => r.json());
  const tbody  = document.getElementById('trades-tbody');
  tbody.innerHTML = trades.map(t => `
    <tr>
      <td style="color:var(--muted)">${t.date.slice(5)}</td>
      <td style="font-weight:600">${t.ticker}</td>
      <td><span style="color:var(--blue)">${t.strategy}</span></td>
      <td class="${colorClass(t.pnl_pct)}">${fmt.pct(t.pnl_pct)}</td>
      <td class="${colorClass(t.pnl_krw)}">${fmt.krw(t.pnl_krw)}</td>
      <td style="color:${t.reason==='익절'?'var(--green)':t.reason==='손절'?'var(--red)':'var(--muted)'}">${t.reason}</td>
      <td style="color:var(--muted)">${t.hold_min}분</td>
    </tr>
  `).join('');
}

// ── 패턴 + 모드 + Brain ───────────────────────────────────────────────────────
async function loadPatterns() {
  const d = await fetch('/api/patterns').then(r => r.json());

  // 교훈
  document.getElementById('lessons-list').innerHTML =
    d.lessons.map(l => `
      <div class="lesson-item">
        <span class="lesson-count">${l.count}회</span>
        <span class="lesson-text">${l.text}</span>
      </div>
    `).join('') || '<div style="color:var(--muted);font-size:13px">아직 없음</div>';

  // 모드별
  const modes = d.modes;
  document.getElementById('modes-list').innerHTML =
    Object.entries(modes).map(([mode, v]) => `
      <div style="display:flex;align-items:center;gap:8px;padding:8px 0;
                  border-bottom:1px solid var(--border);">
        <span class="mode-badge mode-${mode}">${mode}</span>
        <span style="font-family:var(--mono);font-size:12px;margin-left:auto">
          ${v.count}회
        </span>
        <span class="mode-badge ${v.win_rate>=55?'mode-MODERATE_BULL':'mode-CAUTIOUS'}"
              style="font-size:11px">${v.win_rate}%</span>
        <span class="mode-badge ${v.avg_pnl>=0?'mode-AGGRESSIVE':'mode-HALT'}"
              style="font-size:11px">${fmt.pct(v.avg_pnl)}</span>
      </div>
    `).join('');
}

async function loadBrain() {
  const d = await fetch('/api/brain').then(r => r.json());
  const a = d.analyst || {};

  document.getElementById('brain-status').innerHTML = `
    <div style="font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:8px">
      v${d.version}  |  ${d.trained_days}일 학습  |  ${d.updated}
    </div>
    <div class="brain-stats">
      <div class="brain-stat">
        <div class="brain-stat-val up">${((a.bull||{}).rate||0)*100|0}%</div>
        <div class="brain-stat-label">🟢 Bull</div>
      </div>
      <div class="brain-stat">
        <div class="brain-stat-val down">${((a.bear||{}).rate||0)*100|0}%</div>
        <div class="brain-stat-label">🔴 Bear</div>
      </div>
      <div class="brain-stat">
        <div class="brain-stat-val neutral-color">${((a.neutral||{}).rate||0)*100|0}%</div>
        <div class="brain-stat-label">⚪ Neutral</div>
      </div>
    </div>
    <div style="margin-top:12px;font-size:12px;color:var(--muted)">
      장세: <span style="color:var(--text)">${d.regime}</span>
    </div>
  `;
}

// ── 전체 로드 ─────────────────────────────────────────────────────────────────
async function loadAll() {
  await Promise.all([
    loadSummary(), loadJudgments(),
    loadEquityChart(), loadAnalystChart(),
    loadTrades(), loadPatterns(), loadBrain()
  ]);
}

loadAll();

// 30초마다 자동 새로고침
setInterval(loadAll, 30000);
</script>
</body>
</html>"""

@app.route("/")
def index():
    return render_template_string(DASHBOARD_HTML)


if __name__ == "__main__":
    print("=" * 50)
    print("  Trading Dashboard 시작")
    print("  http://localhost:5000 으로 접속하세요")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=False)
