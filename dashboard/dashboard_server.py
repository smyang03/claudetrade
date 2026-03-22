"""
dashboard_server.py
Flask 기반 트레이딩 대시보드 서버 (4-page edition)

페이지:
  /            — 오늘 현황
  /history     — 기간별 성과
  /trades      — 매매 원장
  /analytics   — 분석

실행: python dashboard_server.py
접속: http://localhost:5000
"""

from flask import Flask, jsonify, render_template_string, request
from pathlib import Path
from datetime import datetime, date, timedelta, time as dt_time
import json, sys, os

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from datetime import timezone, timedelta as _td
    class ZoneInfo:
        def __new__(cls, _): return timezone(_td(hours=9))

KST = ZoneInfo("Asia/Seoul")

sys.path.insert(0, str(Path(__file__).parent.parent))
from runtime_paths import get_runtime_path
from credit_tracker import summary as credit_summary

app = Flask(__name__)

BASE_DIR   = Path(__file__).parent.parent
LOG_DIR    = get_runtime_path("logs", "daily_judgment", make_parents=False)
BRAIN_PATH = get_runtime_path("state", "brain.json")

PAPER_CASH = float(os.getenv("PAPER_CASH", "10000000"))


# ── 데이터 로더 ────────────────────────────────────────────────────────────────

def current_market() -> str:
    now = datetime.now(KST).time()
    if now >= dt_time(22, 20) or now < dt_time(5, 0):
        return "US"
    return "KR"


def best_market_with_data() -> str:
    today = date.today().strftime("%Y%m%d")
    for mkt in ("US", "KR"):
        p = LOG_DIR / f"{today}_{mkt}.json"
        if p.exists():
            try:
                d = json.load(open(p, encoding="utf-8"))
                if d.get("mode") != "historical_sim":
                    return mkt
            except Exception:
                pass
    return current_market()


def load_records(days: int = 9999, market: str = "KR") -> list:
    if not LOG_DIR.exists():
        return []
    records = []
    for path in sorted(LOG_DIR.glob(f"*_{market}.json")):
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            if rec.get("mode") == "historical_sim":
                continue
            records.append(rec)
        except Exception:
            pass
    return records[-days:]


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
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            if rec.get("mode") != "historical_sim":
                return rec
        except Exception:
            pass
    if not LOG_DIR.exists():
        return {}
    for path in reversed(sorted(LOG_DIR.glob(f"*_{market}.json"))):
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            if rec.get("mode") != "historical_sim":
                return rec
        except Exception:
            pass
    return {}


def _parse_date(s: str):
    try:
        return date.fromisoformat(s[:10])
    except Exception:
        return date.min


def load_records_filtered(market: str, period: str, start: str, end: str) -> list:
    all_recs = load_records(9999, market)
    today = date.today()
    if period == "week":
        d_start = today - timedelta(days=today.weekday())
        d_end   = today
    elif period == "month":
        d_start = today.replace(day=1)
        d_end   = today
    elif period == "3month":
        d_start = today - timedelta(days=90)
        d_end   = today
    elif period == "custom":
        d_start = _parse_date(start)
        d_end   = _parse_date(end) if end else today
    else:  # all
        return all_recs
    return [r for r in all_recs
            if d_start <= _parse_date(r.get("date", "")) <= d_end]


def group_by_month(records: list) -> dict:
    groups = {}
    for r in records:
        key = r.get("date", "")[:7]  # YYYY-MM
        groups.setdefault(key, []).append(r)
    return groups


# ── API 엔드포인트 ─────────────────────────────────────────────────────────────

@app.route("/api/summary")
def api_summary():
    market  = request.args.get("market", best_market_with_data())
    records = load_records(60, market)
    if not records:
        other = "US" if market == "KR" else "KR"
        records = load_records(60, other)
        if records:
            market = other
    if not records:
        return jsonify({})

    today_rec = load_today(market)
    result    = today_rec.get("actual_result", {})

    wins      = [r for r in records if r.get("actual_result", {}).get("win")]
    total_pnl = sum(r.get("actual_result", {}).get("pnl_pct", 0) for r in records)
    win_rate  = len(wins) / len(records) * 100 if records else 0

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
            "date":       today_rec.get("date", ""),
            "pnl_pct":    result.get("pnl_pct", 0),
            "pnl_krw":    result.get("pnl_krw", 0),
            "win":        result.get("win", False),
            "trades":     result.get("trades", 0),
            "mode":       today_rec.get("consensus", {}).get("mode", "-"),
            "cumulative": result.get("cumulative", PAPER_CASH),
        },
        "period": {
            "days":        len(records),
            "wins":        len(wins),
            "losses":      len(records) - len(wins),
            "win_rate":    round(win_rate, 1),
            "total_pnl":   round(total_pnl, 2),
            "streak":      streak,
            "streak_type": streak_type,
        }
    })


@app.route("/api/judgments")
def api_judgments():
    market = request.args.get("market", best_market_with_data())
    rec    = load_today(market)
    if not rec:
        return jsonify({})
    judgments  = rec.get("judgments", {})
    postmortem = rec.get("postmortem", {})
    return jsonify({
        "date":     rec.get("date", ""),
        "bull":     {**judgments.get("bull", {}),
                     "result": postmortem.get("bull_result", ""),
                     "why":    postmortem.get("bull_why", "")},
        "bear":     {**judgments.get("bear", {}),
                     "result": postmortem.get("bear_result", ""),
                     "why":    postmortem.get("bear_why", "")},
        "neutral":  {**judgments.get("neutral", {}),
                     "result": postmortem.get("neutral_result", ""),
                     "why":    postmortem.get("neutral_why", "")},
        "consensus": rec.get("consensus", {}),
        "lesson":   postmortem.get("key_lesson", ""),
    })


@app.route("/api/chart/equity")
def api_equity_chart():
    market  = request.args.get("market", best_market_with_data())
    period  = request.args.get("period", "all")
    start   = request.args.get("start", "")
    end     = request.args.get("end", "")
    records = load_records_filtered(market, period, start, end)

    labels, values, pnls, wins, modes = [], [], [], [], []
    for r in records:
        result = r.get("actual_result", {})
        d = r.get("date", "")
        labels.append(d[-5:] if len(d) >= 5 else d)
        values.append(result.get("cumulative", PAPER_CASH))
        pnls.append(result.get("pnl_pct", 0))
        wins.append(result.get("win", False))
        modes.append(r.get("consensus", {}).get("mode", ""))
    return jsonify({"labels": labels, "equity": values,
                    "pnl": pnls, "wins": wins, "modes": modes})


@app.route("/api/chart/analyst")
def api_analyst_chart():
    market  = request.args.get("market", best_market_with_data())
    records = load_records(30, market)
    labels  = []
    bull_hits, bear_hits, neut_hits = [], [], []
    window = 7
    for i, r in enumerate(records):
        labels.append(r.get("date", "")[-5:])
        start_i = max(0, i - window + 1)
        wnd = records[start_i:i+1]
        def rate(analyst, _wnd=wnd):
            key = f"{analyst}_result"
            hits = sum(1 for rec in _wnd
                       if rec.get("postmortem", {}).get(key) == "HIT")
            return round(hits / len(_wnd) * 100, 1)
        bull_hits.append(rate("bull"))
        bear_hits.append(rate("bear"))
        neut_hits.append(rate("neutral"))
    return jsonify({"labels": labels, "bull": bull_hits,
                    "bear": bear_hits, "neutral": neut_hits})


@app.route("/api/patterns")
def api_patterns():
    market  = request.args.get("market", best_market_with_data())
    records = load_records(60, market)
    lessons = {}
    modes   = {}
    for r in records:
        lesson = r.get("postmortem", {}).get("key_lesson", "")
        if lesson:
            lessons[lesson] = lessons.get(lesson, 0) + 1
        mode   = r.get("consensus", {}).get("mode", "")
        result = r.get("actual_result", {})
        if mode:
            if mode not in modes:
                modes[mode] = {"count": 0, "wins": 0, "total_pnl": 0}
            modes[mode]["count"]     += 1
            modes[mode]["wins"]      += 1 if result.get("win") else 0
            modes[mode]["total_pnl"] += result.get("pnl_pct", 0)
    for m in modes:
        c = modes[m]["count"]
        modes[m]["win_rate"] = round(modes[m]["wins"] / c * 100, 1) if c else 0
        modes[m]["avg_pnl"]  = round(modes[m]["total_pnl"] / c, 2) if c else 0
    top_lessons = sorted(lessons.items(), key=lambda x: x[1], reverse=True)[:10]
    return jsonify({
        "lessons": [{"text": k, "count": v} for k, v in top_lessons],
        "modes":   modes,
    })


@app.route("/api/credits")
def api_credits():
    try:
        usd_krw = float(os.getenv("USD_KRW_RATE", "1350"))
        return jsonify(credit_summary(usd_krw))
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/brain")
def api_brain():
    brain  = load_brain()
    market = brain.get("markets", {}).get("KR", {})
    return jsonify({
        "trained_days": market.get("trained_days", 0),
        "regime":       market.get("current_regime", "unknown"),
        "analyst":      market.get("analyst_performance", {}),
        "beliefs":      market.get("current_beliefs", {}),
        "version":      brain.get("meta", {}).get("version", 0),
        "updated":      brain.get("meta", {}).get("last_updated", ""),
        "strategy":     market.get("strategy_performance", {}),
    })


# ── 신규 API 엔드포인트 ────────────────────────────────────────────────────────

@app.route("/api/stats/period")
def api_stats_period():
    market = request.args.get("market", best_market_with_data())
    period = request.args.get("period", "month")
    start  = request.args.get("start", "")
    end    = request.args.get("end", "")
    records = load_records_filtered(market, period, start, end)

    if not records:
        return jsonify({"days": 0, "wins": 0, "losses": 0,
                        "win_rate": 0, "total_pnl": 0, "avg_pnl": 0, "trades": 0})

    wins      = [r for r in records if r.get("actual_result", {}).get("win")]
    total_pnl = sum(r.get("actual_result", {}).get("pnl_pct", 0) for r in records)
    trades    = sum(r.get("actual_result", {}).get("trades", 0) for r in records)
    n         = len(records)
    return jsonify({
        "days":      n,
        "wins":      len(wins),
        "losses":    n - len(wins),
        "win_rate":  round(len(wins) / n * 100, 1) if n else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl":   round(total_pnl / n, 2) if n else 0,
        "trades":    trades,
    })


@app.route("/api/history/monthly")
def api_history_monthly():
    market  = request.args.get("market", best_market_with_data())
    records = load_records(9999, market)
    groups  = group_by_month(records)

    result = []
    for month in sorted(groups.keys(), reverse=True):
        recs  = groups[month]
        wins  = [r for r in recs if r.get("actual_result", {}).get("win")]
        pnls  = [r.get("actual_result", {}).get("pnl_pct", 0) for r in recs]
        total = sum(pnls)
        n     = len(recs)
        trades_sum = sum(r.get("actual_result", {}).get("trades", 0) for r in recs)

        best_rec  = max(recs, key=lambda r: r.get("actual_result", {}).get("pnl_pct", 0), default=None)
        worst_rec = min(recs, key=lambda r: r.get("actual_result", {}).get("pnl_pct", 0), default=None)

        result.append({
            "month":      month,
            "days":       n,
            "wins":       len(wins),
            "losses":     n - len(wins),
            "win_rate":   round(len(wins) / n * 100, 1) if n else 0,
            "total_pnl":  round(total, 2),
            "avg_pnl":    round(total / n, 2) if n else 0,
            "trades":     trades_sum,
            "best_day":   {
                "date": best_rec.get("date", "") if best_rec else "",
                "pnl":  round(best_rec.get("actual_result", {}).get("pnl_pct", 0), 2) if best_rec else 0
            },
            "worst_day":  {
                "date": worst_rec.get("date", "") if worst_rec else "",
                "pnl":  round(worst_rec.get("actual_result", {}).get("pnl_pct", 0), 2) if worst_rec else 0
            },
        })
    return jsonify(result)


@app.route("/api/history/equity")
def api_history_equity():
    market  = request.args.get("market", best_market_with_data())
    period  = request.args.get("period", "all")
    start   = request.args.get("start", "")
    end     = request.args.get("end", "")
    records = load_records_filtered(market, period, start, end)

    labels, equity, pnl, wins, modes = [], [], [], [], []
    for r in records:
        result = r.get("actual_result", {})
        d = r.get("date", "")
        labels.append(d[:10] if len(d) >= 10 else d)
        equity.append(result.get("cumulative", PAPER_CASH))
        pnl.append(result.get("pnl_pct", 0))
        wins.append(result.get("win", False))
        modes.append(r.get("consensus", {}).get("mode", ""))
    return jsonify({"labels": labels, "equity": equity,
                    "pnl": pnl, "wins": wins, "modes": modes})


@app.route("/api/trades/list")
def api_trades_list():
    market   = request.args.get("market", best_market_with_data())
    period   = request.args.get("period", "all")
    start    = request.args.get("start", "")
    end      = request.args.get("end", "")
    ticker   = request.args.get("ticker", "").upper()
    strategy = request.args.get("strategy", "")
    side     = request.args.get("side", "")
    limit    = int(request.args.get("limit", "200"))

    records = load_records_filtered(market, period, start, end)
    trades  = []
    for r in records:
        rec_date = r.get("date", "")
        for t in r.get("trades", []):
            t_side     = t.get("side", "")
            t_ticker   = t.get("ticker", "")
            t_strategy = t.get("strategy", "")
            if ticker   and ticker   not in t_ticker.upper():
                continue
            if strategy and strategy != t_strategy:
                continue
            if side     and side     != t_side:
                continue
            trades.append({
                "date":     t.get("date", rec_date),
                "side":     t_side,
                "ticker":   t_ticker,
                "strategy": t_strategy,
                "price":    t.get("price", 0),
                "qty":      t.get("qty", 0),
                "pnl_pct":  round(t.get("pnl_pct", 0), 2),
                "pnl":      t.get("pnl", t.get("pnl_krw", 0)),
                "reason":   t.get("reason", ""),
            })

    trades.sort(key=lambda x: (x["date"], x["side"]), reverse=True)
    return jsonify(trades[:limit])


# ── HTML 헬퍼 ─────────────────────────────────────────────────────────────────

def _head(title: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — TRADINGBRAIN</title>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.0/chart.umd.min.js"></script>
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;600;700&family=Noto+Sans+KR:wght@300;400;500;700&display=swap');

:root {{
  --bg:       #0a0e1a;
  --surface:  #111827;
  --surface2: #161f2e;
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
}}

* {{ margin:0; padding:0; box-sizing:border-box; }}
body {{
  background: var(--bg);
  color: var(--text);
  font-family: var(--sans);
  min-height: 100vh;
}}

/* ── 헤더 ── */
header {{
  display: flex; align-items: center; justify-content: space-between;
  padding: 0 24px; height: 56px;
  background: var(--surface);
  border-bottom: 1px solid var(--border);
  position: sticky; top: 0; z-index: 100;
  gap: 16px;
}}
.logo {{
  font-family: var(--mono); font-weight: 700; font-size: 17px;
  color: var(--cyan); letter-spacing: 2px; text-decoration: none;
  white-space: nowrap;
}}
.logo span {{ color: var(--muted); font-weight: 300; }}

nav {{ display: flex; gap: 4px; }}
nav a {{
  padding: 6px 14px; border-radius: 6px; font-size: 13px; font-weight: 500;
  color: var(--muted); text-decoration: none; transition: all 0.15s;
  white-space: nowrap;
}}
nav a:hover {{ color: var(--text); background: rgba(255,255,255,0.05); }}
nav a.active {{ color: var(--cyan); background: rgba(6,182,212,0.12);
                border: 1px solid rgba(6,182,212,0.25); }}

.header-right {{ display: flex; align-items: center; gap: 10px; }}

.status-dot {{
  width: 7px; height: 7px; border-radius: 50%;
  background: var(--green); box-shadow: 0 0 6px var(--green);
  animation: pulse 2s infinite; display: inline-block;
}}
@keyframes pulse {{ 0%,100%{{opacity:1}} 50%{{opacity:0.35}} }}

#clock {{ font-family: var(--mono); font-size: 12px; color: var(--muted); white-space: nowrap; }}

.mkt-btn {{
  padding: 5px 12px; border-radius: 5px; font-family: var(--mono); font-size: 12px;
  cursor: pointer; border: 1px solid var(--border); background: transparent;
  color: var(--muted); transition: all 0.15s;
}}
.mkt-btn.active {{
  background: rgba(6,182,212,0.15); border-color: rgba(6,182,212,0.4); color: var(--cyan);
}}
.mkt-btn:hover {{ color: var(--text); }}

/* ── 기간 필터 바 ── */
.period-bar {{
  background: var(--surface2); border-bottom: 1px solid var(--border);
  padding: 10px 24px; display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
}}
.period-btn {{
  padding: 5px 14px; border-radius: 5px; font-size: 12px; font-weight: 500;
  cursor: pointer; border: 1px solid var(--border); background: transparent;
  color: var(--muted); transition: all 0.15s;
}}
.period-btn.active {{
  background: rgba(59,130,246,0.15); border-color: rgba(59,130,246,0.4); color: var(--blue);
}}
.period-btn:hover {{ color: var(--text); }}
.period-sep {{ width: 1px; height: 20px; background: var(--border); margin: 0 4px; }}
.date-input {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 5px;
  color: var(--text); padding: 5px 10px; font-family: var(--mono); font-size: 12px;
}}
.date-input:focus {{ outline: none; border-color: var(--blue); }}
.apply-btn {{
  padding: 5px 14px; border-radius: 5px; font-size: 12px; font-weight: 600;
  cursor: pointer; border: 1px solid rgba(59,130,246,0.4);
  background: rgba(59,130,246,0.15); color: var(--blue); transition: all 0.15s;
}}
.apply-btn:hover {{ background: rgba(59,130,246,0.3); }}

/* ── 레이아웃 ── */
main {{ padding: 20px 24px; max-width: 1600px; margin: 0 auto; }}

.grid-5 {{ display: grid; grid-template-columns: repeat(5,1fr); gap: 16px; margin-bottom: 20px; }}
.grid-4 {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 16px; margin-bottom: 20px; }}
.grid-3 {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 16px; margin-bottom: 20px; }}
.grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }}

/* ── 카드 ── */
.card {{
  background: var(--surface); border: 1px solid var(--border);
  border-radius: 12px; padding: 20px; position: relative; overflow: hidden;
}}
.card::before {{
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 2px;
}}
.card.green::before  {{ background: var(--green); }}
.card.red::before    {{ background: var(--red); }}
.card.blue::before   {{ background: var(--blue); }}
.card.yellow::before {{ background: var(--yellow); }}
.card.purple::before {{ background: var(--purple); }}
.card.cyan::before   {{ background: var(--cyan); }}

.card-label {{
  font-size: 11px; font-weight: 500; letter-spacing: 1.5px;
  color: var(--muted); text-transform: uppercase; margin-bottom: 8px;
}}
.card-value {{
  font-family: var(--mono); font-size: 26px; font-weight: 700; line-height: 1.1;
}}
.card-sub {{ font-size: 12px; color: var(--muted); margin-top: 6px; font-family: var(--mono); }}

.up   {{ color: var(--green); }}
.down {{ color: var(--red); }}
.neutral-color {{ color: var(--yellow); }}

/* ── 섹션 타이틀 ── */
.section-title {{
  font-size: 11px; font-weight: 600; letter-spacing: 2px; color: var(--muted);
  text-transform: uppercase; margin-bottom: 14px;
  display: flex; align-items: center; gap: 8px;
}}
.section-title::after {{ content: ''; flex: 1; height: 1px; background: var(--border); }}

/* ── 차트 ── */
.chart-container {{ position: relative; height: 220px; }}

/* ── 테이블 ── */
.table-wrap {{ overflow-x: auto; }}
table {{ width: 100%; border-collapse: collapse; font-size: 13px; }}
th {{
  text-align: left; padding: 8px 12px;
  font-family: var(--mono); font-size: 11px; color: var(--muted);
  letter-spacing: 1px; border-bottom: 1px solid var(--border);
  background: rgba(255,255,255,0.02); white-space: nowrap;
}}
td {{
  padding: 9px 12px; border-bottom: 1px solid rgba(31,41,55,0.5);
  font-family: var(--mono); font-size: 12px;
}}
tr:hover td {{ background: rgba(255,255,255,0.02); }}

/* ── 배지 / 뱃지 ── */
.mode-badge {{
  padding: 2px 8px; border-radius: 4px;
  font-family: var(--mono); font-size: 11px; font-weight: 600; white-space: nowrap;
}}
.mode-AGGRESSIVE    {{ background: rgba(16,185,129,0.2);  color: var(--green); }}
.mode-MODERATE_BULL {{ background: rgba(59,130,246,0.2);  color: var(--blue); }}
.mode-CAUTIOUS      {{ background: rgba(245,158,11,0.2);  color: var(--yellow); }}
.mode-DEFENSIVE     {{ background: rgba(139,92,246,0.2);  color: var(--purple); }}
.mode-HALT          {{ background: rgba(239,68,68,0.2);   color: var(--red); }}

.result-badge {{ padding: 2px 8px; border-radius: 4px; font-weight: 700; font-size: 11px; }}
.hit     {{ background: rgba(16,185,129,0.2); color: var(--green); }}
.miss    {{ background: rgba(239,68,68,0.2);  color: var(--red); }}
.partial {{ background: rgba(245,158,11,0.2); color: var(--yellow); }}

.side-buy  {{ color: var(--green); }}
.side-sell {{ color: var(--red); }}

/* ── 분석가 카드 ── */
.analyst-grid {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 16px; margin-bottom: 20px; }}
.analyst-card {{
  background: var(--surface); border: 1px solid var(--border); border-radius: 12px; padding: 20px;
}}
.analyst-header {{ display: flex; align-items: center; gap: 10px; margin-bottom: 14px; }}
.analyst-icon {{
  width: 36px; height: 36px; border-radius: 50%;
  display: flex; align-items: center; justify-content: center; font-size: 16px; font-weight: 700;
}}
.analyst-icon.bull {{ background: rgba(16,185,129,0.2); color: var(--green); }}
.analyst-icon.bear {{ background: rgba(239,68,68,0.2);  color: var(--red); }}
.analyst-icon.neut {{ background: rgba(245,158,11,0.2); color: var(--yellow); }}
.analyst-name {{ font-weight: 600; font-size: 15px; }}
.analyst-stance {{
  display: inline-block; padding: 2px 8px; border-radius: 4px;
  font-size: 11px; font-family: var(--mono); font-weight: 600; margin-top: 4px;
}}
.stance-bull {{ background: rgba(16,185,129,0.15); color: var(--green); border: 1px solid rgba(16,185,129,0.3); }}
.stance-bear {{ background: rgba(239,68,68,0.15);  color: var(--red);   border: 1px solid rgba(239,68,68,0.3); }}
.stance-neut {{ background: rgba(245,158,11,0.15); color: var(--yellow);border: 1px solid rgba(245,158,11,0.3); }}
.analyst-confidence {{ font-family: var(--mono); font-size: 12px; color: var(--muted); margin: 10px 0 6px; }}
.conf-bar {{ height: 4px; background: var(--border); border-radius: 2px; margin-bottom: 12px; overflow: hidden; }}
.conf-bar-fill {{ height: 100%; border-radius: 2px; transition: width 0.5s; }}
.analyst-reason {{
  font-size: 13px; line-height: 1.6; color: var(--text);
  padding: 10px; background: rgba(255,255,255,0.03);
  border-radius: 8px; border-left: 3px solid var(--border); margin-bottom: 10px;
}}
.postmortem {{ font-size: 12px; color: var(--muted); margin-top: 8px; line-height: 1.5; font-style: italic; }}
.lesson-box {{
  background: rgba(245,158,11,0.08); border: 1px solid rgba(245,158,11,0.2);
  border-radius: 8px; padding: 12px 16px; font-size: 13px; line-height: 1.6; color: var(--yellow);
}}

/* ── 교훈 ── */
.lesson-item {{ display: flex; align-items: flex-start; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); }}
.lesson-count {{
  background: rgba(59,130,246,0.2); color: var(--blue); border-radius: 4px;
  padding: 2px 8px; font-family: var(--mono); font-size: 12px; font-weight: 700;
  white-space: nowrap; min-width: 40px; text-align: center;
}}
.lesson-text {{ font-size: 13px; line-height: 1.5; }}

/* ── 진행 바 ── */
.mini-bar-wrap {{ background: var(--border); border-radius: 2px; height: 4px; width: 80px; display: inline-block; vertical-align: middle; overflow: hidden; }}
.mini-bar-fill  {{ height: 100%; border-radius: 2px; }}

/* ── 거래 원장 날짜 그룹 헤더 ── */
.date-group-row td {{
  background: var(--surface2); color: var(--muted); font-size: 11px;
  letter-spacing: 2px; padding: 6px 12px; border-bottom: 1px solid var(--border);
}}

/* ── Brain 상태 ── */
.brain-stats {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; margin-top: 12px; }}
.brain-stat {{
  background: rgba(255,255,255,0.03); border-radius: 8px;
  padding: 10px; text-align: center;
}}
.brain-stat-val {{ font-family: var(--mono); font-size: 20px; font-weight: 700; margin-bottom: 4px; }}
.brain-stat-label {{ font-size: 11px; color: var(--muted); }}

/* ── 반응형 ── */
@media (max-width: 1200px) {{
  .grid-5 {{ grid-template-columns: repeat(3,1fr); }}
  .grid-4 {{ grid-template-columns: repeat(2,1fr); }}
}}
@media (max-width: 768px) {{
  .grid-5, .grid-4, .grid-3, .grid-2 {{ grid-template-columns: 1fr; }}
  .analyst-grid {{ grid-template-columns: 1fr; }}
  header {{ flex-wrap: wrap; height: auto; padding: 10px 16px; gap: 8px; }}
  main {{ padding: 12px; }}
}}
</style>
</head>
<body>
"""


def _header_html(active_page: str) -> str:
    pages = [
        ("/",          "오늘 현황"),
        ("/history",   "기간별 성과"),
        ("/trades",    "매매 원장"),
        ("/analytics", "분석"),
    ]
    nav_links = "".join(
        f'<a href="{url}" class="{"active" if url == active_page else ""}">{label}</a>'
        for url, label in pages
    )
    return f"""
<header>
  <a href="/" class="logo">TRADING<span>BRAIN</span></a>
  <nav>{nav_links}</nav>
  <div class="header-right">
    <span class="status-dot"></span>
    <button class="mkt-btn" id="btn-kr" onclick="setMarket('KR')">🇰🇷 KR</button>
    <button class="mkt-btn" id="btn-us" onclick="setMarket('US')">🇺🇸 US</button>
    <span id="clock"></span>
  </div>
</header>
"""


def _period_bar_html(extra_filters: str = "") -> str:
    return f"""
<div class="period-bar">
  <button class="period-btn" data-p="week"    onclick="setPeriod('week')"   >이번주</button>
  <button class="period-btn" data-p="month"   onclick="setPeriod('month')"  >이번달</button>
  <button class="period-btn" data-p="3month"  onclick="setPeriod('3month')" >3개월</button>
  <button class="period-btn" data-p="all"     onclick="setPeriod('all')"    >전체</button>
  <div class="period-sep"></div>
  <input  class="date-input" type="date" id="date-start" placeholder="시작일">
  <span style="color:var(--muted);font-size:12px">~</span>
  <input  class="date-input" type="date" id="date-end"   placeholder="종료일">
  <button class="apply-btn" onclick="applyCustomDate()">적용</button>
  {extra_filters}
</div>
"""


COMMON_JS_BLOCK = """
<script>
// ── 공통 상태 ──────────────────────────────────────────────────────────────────
let MARKET = localStorage.getItem('market') || 'KR';
let PERIOD = localStorage.getItem('period') || 'month';
let DATE_START = localStorage.getItem('date_start') || '';
let DATE_END   = localStorage.getItem('date_end')   || '';

let charts = {};

function setMarket(m) {
  MARKET = m;
  localStorage.setItem('market', m);
  document.querySelectorAll('.mkt-btn').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('btn-' + m.toLowerCase());
  if (btn) btn.classList.add('active');
  if (typeof loadAll === 'function') loadAll();
}

function setPeriod(p) {
  PERIOD = p;
  localStorage.setItem('period', p);
  document.querySelectorAll('.period-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.p === p);
  });
  if (typeof loadAll === 'function') loadAll();
}

function applyCustomDate() {
  const s = document.getElementById('date-start');
  const e = document.getElementById('date-end');
  DATE_START = s ? s.value : '';
  DATE_END   = e ? e.value : '';
  localStorage.setItem('date_start', DATE_START);
  localStorage.setItem('date_end',   DATE_END);
  PERIOD = 'custom';
  localStorage.setItem('period', 'custom');
  document.querySelectorAll('.period-btn').forEach(b => b.classList.remove('active'));
  if (typeof loadAll === 'function') loadAll();
}

function marketParam(extra = '') {
  const p  = new URLSearchParams();
  p.set('market', MARKET);
  if (PERIOD) p.set('period', PERIOD);
  if (PERIOD === 'custom') {
    if (DATE_START) p.set('start', DATE_START);
    if (DATE_END)   p.set('end',   DATE_END);
  }
  if (extra) {
    extra.split('&').forEach(kv => {
      const [k, v] = kv.split('=');
      if (k && v !== undefined) p.set(k, v);
    });
  }
  return '?' + p.toString();
}

// ── 포맷터 ─────────────────────────────────────────────────────────────────────
const fmt = {
  pct:   v => (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%',
  krw:   v => (v >= 0 ? '+' : '') + Math.round(v).toLocaleString() + '원',
  asset: v => Math.round(v).toLocaleString() + '원',
  num:   v => Number(v).toLocaleString(),
};

function colorClass(v) {
  return v > 0 ? 'up' : v < 0 ? 'down' : 'neutral-color';
}
function colorVar(v) {
  return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--muted)';
}

// ── 시계 ───────────────────────────────────────────────────────────────────────
function updateClock() {
  const el = document.getElementById('clock');
  if (!el) return;
  el.textContent = new Date().toLocaleString('ko-KR', {
    timeZone: 'Asia/Seoul',
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit'
  });
}
setInterval(updateClock, 1000);
updateClock();

// ── 초기화 ─────────────────────────────────────────────────────────────────────
(function initState() {
  // 마켓 버튼
  const btn = document.getElementById('btn-' + MARKET.toLowerCase());
  if (btn) btn.classList.add('active');

  // 기간 버튼
  document.querySelectorAll('.period-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.p === PERIOD);
  });

  // 날짜 인풋 복원
  const ds = document.getElementById('date-start');
  const de = document.getElementById('date-end');
  if (ds && DATE_START) ds.value = DATE_START;
  if (de && DATE_END)   de.value = DATE_END;
})();

// ── 자동 새로고침 ──────────────────────────────────────────────────────────────
setTimeout(() => {
  if (typeof loadAll === 'function') setInterval(loadAll, 30000);
}, 1000);
</script>
"""


# ── 페이지 1: 오늘 현황 ─────────────────────────────────────────────────────────

PAGE_TODAY_HTML = """
<main>

<!-- 5 요약 카드 -->
<div class="grid-5">
  <div class="card cyan">
    <div class="card-label">오늘 손익</div>
    <div class="card-value" id="today-pnl">--</div>
    <div class="card-sub"  id="today-krw">-- 원</div>
  </div>
  <div class="card blue">
    <div class="card-label">누적 자산</div>
    <div class="card-value" id="cumulative">--</div>
    <div class="card-sub"  id="today-mode">모드: --</div>
  </div>
  <div class="card green">
    <div class="card-label">기간 승률</div>
    <div class="card-value" id="win-rate">--</div>
    <div class="card-sub"  id="win-detail">-- 승 / -- 패</div>
  </div>
  <div class="card yellow">
    <div class="card-label">연속 기록</div>
    <div class="card-value" id="streak-val">--</div>
    <div class="card-sub"  id="total-pnl">누적: --</div>
  </div>
  <div class="card purple">
    <div class="card-label">AI 크레딧 (오늘)</div>
    <div class="card-value" id="credit-today" style="font-size:22px">--</div>
    <div class="card-sub"  id="credit-total">누적: --</div>
    <div class="card-sub"  id="credit-calls" style="margin-top:4px">호출: --회</div>
  </div>
</div>

<!-- 3 판단 카드 -->
<div class="section-title">오늘 마이너리티 판단</div>
<div class="analyst-grid" id="analyst-section"></div>

<!-- 2 차트 -->
<div class="grid-2">
  <div class="card blue">
    <div class="section-title">누적 자산 곡선</div>
    <div class="chart-container"><canvas id="equityChart"></canvas></div>
  </div>
  <div class="card purple">
    <div class="section-title">AI 크레딧 사용량 (최근 7일)</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:center;height:220px;">
      <div class="chart-container" style="height:180px"><canvas id="creditChart"></canvas></div>
      <div id="credit-detail" style="font-family:var(--mono);font-size:12px;line-height:2;color:var(--text)"></div>
    </div>
  </div>
</div>

</main>

<script>

async function loadSummary() {
  const d = await fetch('/api/summary?market=' + MARKET).then(r => r.json()).catch(() => ({}));
  if (!d.today) return;
  const t = d.today, p = d.period;

  const pnlEl = document.getElementById('today-pnl');
  pnlEl.textContent = fmt.pct(t.pnl_pct);
  pnlEl.className = 'card-value ' + colorClass(t.pnl_pct);
  document.getElementById('today-krw').textContent = fmt.krw(t.pnl_krw);
  document.getElementById('cumulative').textContent = fmt.asset(t.cumulative);
  document.getElementById('today-mode').innerHTML =
    `모드: <span class="mode-badge mode-${t.mode}">${t.mode}</span>&nbsp; 거래 ${t.trades}건`;

  const wrEl = document.getElementById('win-rate');
  wrEl.textContent = p.win_rate + '%';
  wrEl.className = 'card-value ' + (p.win_rate >= 55 ? 'up' : p.win_rate >= 45 ? 'neutral-color' : 'down');
  document.getElementById('win-detail').textContent =
    `${p.wins}승 / ${p.losses}패 (${p.days}일)`;

  const emoji = p.streak_type === 'win' ? '🔥' : '❄️';
  document.getElementById('streak-val').innerHTML =
    `<span style="font-size:20px">${emoji}</span> ${p.streak}연속`;
  document.getElementById('total-pnl').textContent = `누적: ${fmt.pct(p.total_pnl)}`;
}

async function loadJudgments() {
  const d = await fetch('/api/judgments?market=' + MARKET).then(r => r.json()).catch(() => ({}));
  if (!d.bull) return;

  function analystCard(info, label, iconClass, stanceClass) {
    const conf = Math.round((info.confidence || 0) * 100);
    const res  = info.result || '';
    const rb   = res === 'HIT' ? 'hit' : res === 'MISS' ? 'miss' : 'partial';
    const barC = iconClass === 'bull' ? 'var(--green)' : iconClass === 'bear' ? 'var(--red)' : 'var(--yellow)';
    return `
    <div class="analyst-card">
      <div class="analyst-header">
        <div class="analyst-icon ${iconClass}">${label[0]}</div>
        <div>
          <div class="analyst-name">${label}</div>
          <div class="analyst-stance ${stanceClass}">${info.stance || '-'}</div>
        </div>
        ${res ? `<div style="margin-left:auto"><span class="result-badge ${rb}">${res}</span></div>` : ''}
      </div>
      <div class="analyst-confidence">신뢰도 ${conf}%</div>
      <div class="conf-bar"><div class="conf-bar-fill" style="width:${conf}%;background:${barC}"></div></div>
      <div class="analyst-reason">📋 ${info.key_reason || '-'}</div>
      ${info.why ? `<div class="postmortem">→ ${info.why}</div>` : ''}
    </div>`;
  }

  const sec = document.getElementById('analyst-section');
  sec.innerHTML =
    analystCard(d.bull,    '🟢 Bull 분석가',    'bull', 'stance-bull') +
    analystCard(d.bear,    '🔴 Bear 분석가',    'bear', 'stance-bear') +
    analystCard(d.neutral, '⚪ Neutral 분석가', 'neut', 'stance-neut');

  if (d.lesson) {
    sec.innerHTML += `<div class="lesson-box" style="grid-column:1/-1">💡 오늘의 교훈: ${d.lesson}</div>`;
  }
}

async function loadEquityChart() {
  const d = await fetch('/api/chart/equity?market=' + MARKET + '&period=3month').then(r => r.json()).catch(() => ({}));
  if (!d.labels) return;

  const colors = (d.wins || []).map(w => w ? 'rgba(16,185,129,0.8)' : 'rgba(239,68,68,0.8)');
  if (charts.equity) charts.equity.destroy();
  charts.equity = new Chart(document.getElementById('equityChart').getContext('2d'), {
    type: 'bar',
    data: {
      labels: d.labels,
      datasets: [
        {
          type: 'line', label: '누적 자산', data: d.equity,
          borderColor: 'rgba(6,182,212,0.9)', backgroundColor: 'rgba(6,182,212,0.05)',
          borderWidth: 2, pointRadius: 0, tension: 0.3, yAxisID: 'y1', fill: true,
        },
        {
          type: 'bar', label: '일별 손익%', data: d.pnl,
          backgroundColor: colors, yAxisID: 'y2', barThickness: 'flex',
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#64748b', font: { size: 11 } } } },
      scales: {
        x:  { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: 'rgba(31,41,55,0.5)' } },
        y1: { position: 'left',
              ticks: { color: '#06b6d4', font: { size: 10 },
                       callback: v => fmt.asset(v) },
              grid: { color: 'rgba(31,41,55,0.3)' } },
        y2: { position: 'right',
              ticks: { color: '#64748b', font: { size: 10 }, callback: v => v.toFixed(1) + '%' },
              grid: { display: false } },
      }
    }
  });
}

async function loadCredits() {
  const d = await fetch('/api/credits').then(r => r.json()).catch(() => ({}));
  if (d.error || !d.today) return;

  const td = d.today, tot = d.total;
  document.getElementById('credit-today').textContent = `$${td.cost_usd.toFixed(3)}`;
  document.getElementById('credit-total').textContent = `누적: $${tot.cost_usd.toFixed(3)}`;
  document.getElementById('credit-calls').textContent = `오늘 호출: ${td.calls}회`;

  document.getElementById('credit-detail').innerHTML = `
    <div><span style="color:var(--muted)">오늘 입력</span> ${td.input.toLocaleString()} tok</div>
    <div><span style="color:var(--muted)">오늘 출력</span> ${td.output.toLocaleString()} tok</div>
    <div><span style="color:var(--muted)">오늘 비용</span> <span style="color:var(--purple)">$${td.cost_usd.toFixed(4)} ≈ ${td.cost_krw.toLocaleString()}원</span></div>
    <div style="border-top:1px solid var(--border);margin:4px 0"></div>
    <div><span style="color:var(--muted)">누적 비용</span> <span style="color:var(--cyan)">$${tot.cost_usd.toFixed(4)}</span></div>
    <div><span style="color:var(--muted)">누적 입력</span> ${tot.input.toLocaleString()} tok</div>
    <div><span style="color:var(--muted)">누적 출력</span> ${tot.output.toLocaleString()} tok</div>
  `;

  const days   = d.daily_7 || [];
  const labels = days.map(x => x.date.slice(5));
  const costs  = days.map(x => x.cost_usd);
  if (charts.credit) charts.credit.destroy();
  charts.credit = new Chart(document.getElementById('creditChart').getContext('2d'), {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: '일별 비용 ($)', data: costs,
        backgroundColor: 'rgba(139,92,246,0.6)', borderColor: 'rgba(139,92,246,1)',
        borderWidth: 1, borderRadius: 4,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false },
                 tooltip: { callbacks: { label: ctx => '$' + ctx.raw.toFixed(4) } } },
      scales: {
        x: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: 'rgba(31,41,55,0.5)' } },
        y: { ticks: { color: '#64748b', font: { size: 10 }, callback: v => '$' + v.toFixed(3) },
             grid: { color: 'rgba(31,41,55,0.3)' } },
      }
    }
  });
}

async function loadAll() {
  await Promise.all([loadSummary(), loadJudgments(), loadEquityChart(), loadCredits()]);
}

loadAll();
</script>
"""


# ── 페이지 2: 기간별 성과 ──────────────────────────────────────────────────────

PAGE_HISTORY_HTML = """
<main>

<!-- 4 통계 카드 -->
<div class="grid-4" id="stat-cards">
  <div class="card green">
    <div class="card-label">기간 승률</div>
    <div class="card-value" id="h-win-rate">--</div>
    <div class="card-sub"  id="h-win-detail">-- 승 / -- 패</div>
  </div>
  <div class="card cyan">
    <div class="card-label">총 손익</div>
    <div class="card-value" id="h-total-pnl">--</div>
    <div class="card-sub"  id="h-days">-- 거래일</div>
  </div>
  <div class="card blue">
    <div class="card-label">평균 일손익</div>
    <div class="card-value" id="h-avg-pnl">--</div>
    <div class="card-sub" style="color:var(--muted)">일 평균</div>
  </div>
  <div class="card yellow">
    <div class="card-label">거래 수</div>
    <div class="card-value" id="h-trades">--</div>
    <div class="card-sub" style="color:var(--muted)">총 체결 건수</div>
  </div>
</div>

<!-- 2 차트 -->
<div class="grid-2">
  <div class="card blue">
    <div class="section-title">수익 곡선</div>
    <div class="chart-container"><canvas id="histEquityChart"></canvas></div>
  </div>
  <div class="card red">
    <div class="section-title">월별 손익</div>
    <div class="chart-container"><canvas id="monthlyChart"></canvas></div>
  </div>
</div>

<!-- 월별 테이블 -->
<div class="card">
  <div class="section-title">월별 성과 요약</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>월</th><th>거래일</th><th>승</th><th>패</th>
          <th>승률</th><th>총손익%</th><th>평균손익%</th>
          <th>거래수</th><th>최고일</th><th>최악일</th>
        </tr>
      </thead>
      <tbody id="monthly-tbody"></tbody>
    </table>
  </div>
</div>

</main>

<script>

async function loadPeriodStats() {
  const d = await fetch('/api/stats/period' + marketParam()).then(r => r.json()).catch(() => ({}));

  const wrEl = document.getElementById('h-win-rate');
  wrEl.textContent = (d.win_rate || 0) + '%';
  wrEl.className   = 'card-value ' + colorClass(d.win_rate - 50);
  document.getElementById('h-win-detail').textContent = `${d.wins || 0}승 / ${d.losses || 0}패`;

  const tpEl = document.getElementById('h-total-pnl');
  tpEl.textContent = fmt.pct(d.total_pnl || 0);
  tpEl.className   = 'card-value ' + colorClass(d.total_pnl || 0);
  document.getElementById('h-days').textContent = `${d.days || 0} 거래일`;

  const apEl = document.getElementById('h-avg-pnl');
  apEl.textContent = fmt.pct(d.avg_pnl || 0);
  apEl.className   = 'card-value ' + colorClass(d.avg_pnl || 0);

  document.getElementById('h-trades').textContent = fmt.num(d.trades || 0);
}

async function loadHistEquity() {
  const d = await fetch('/api/history/equity' + marketParam()).then(r => r.json()).catch(() => ({}));
  if (!d.labels) return;

  const colors = (d.wins || []).map(w => w ? 'rgba(16,185,129,0.75)' : 'rgba(239,68,68,0.75)');
  if (charts.histEquity) charts.histEquity.destroy();
  charts.histEquity = new Chart(document.getElementById('histEquityChart').getContext('2d'), {
    type: 'bar',
    data: {
      labels: d.labels,
      datasets: [
        {
          type: 'line', label: '누적 자산', data: d.equity,
          borderColor: 'rgba(59,130,246,0.9)', backgroundColor: 'rgba(59,130,246,0.05)',
          borderWidth: 2, pointRadius: 0, tension: 0.3, yAxisID: 'y1', fill: true,
        },
        {
          type: 'bar', label: '일별 손익%', data: d.pnl,
          backgroundColor: colors, yAxisID: 'y2', barThickness: 'flex',
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#64748b', font: { size: 11 } } } },
      scales: {
        x:  { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: 'rgba(31,41,55,0.5)' } },
        y1: { position: 'left',
              ticks: { color: '#3b82f6', font: { size: 10 }, callback: v => fmt.asset(v) },
              grid: { color: 'rgba(31,41,55,0.3)' } },
        y2: { position: 'right',
              ticks: { color: '#64748b', font: { size: 10 }, callback: v => v.toFixed(1) + '%' },
              grid: { display: false } },
      }
    }
  });
}

async function loadMonthly() {
  const rows = await fetch('/api/history/monthly?market=' + MARKET).then(r => r.json()).catch(() => []);

  // 월별 차트
  const labels = rows.map(r => r.month);
  const pnls   = rows.map(r => r.total_pnl);
  const bgs    = pnls.map(v => v >= 0 ? 'rgba(16,185,129,0.7)' : 'rgba(239,68,68,0.7)');

  if (charts.monthly) charts.monthly.destroy();
  charts.monthly = new Chart(document.getElementById('monthlyChart').getContext('2d'), {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: '월별 손익%', data: pnls,
        backgroundColor: bgs, borderRadius: 4,
      }]
    },
    options: {
      indexAxis: 'y',
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: '#64748b', font: { size: 10 }, callback: v => v.toFixed(1)+'%' },
             grid: { color: 'rgba(31,41,55,0.3)' } },
        y: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { display: false } },
      }
    }
  });

  // 테이블
  const tbody = document.getElementById('monthly-tbody');
  tbody.innerHTML = rows.map(r => {
    const wrPct = r.win_rate;
    const wrColor = wrPct >= 55 ? 'var(--green)' : wrPct >= 45 ? 'var(--yellow)' : 'var(--red)';
    const barW  = Math.min(100, Math.round(wrPct));
    return `
    <tr>
      <td style="font-weight:600;color:var(--text)">${r.month}</td>
      <td>${r.days}</td>
      <td class="up">${r.wins}</td>
      <td class="down">${r.losses}</td>
      <td>
        <div style="display:flex;align-items:center;gap:8px">
          <span style="color:${wrColor};min-width:36px">${wrPct}%</span>
          <div class="mini-bar-wrap"><div class="mini-bar-fill" style="width:${barW}%;background:${wrColor}"></div></div>
        </div>
      </td>
      <td class="${colorClass(r.total_pnl)}">${fmt.pct(r.total_pnl)}</td>
      <td class="${colorClass(r.avg_pnl)}">${fmt.pct(r.avg_pnl)}</td>
      <td>${r.trades}</td>
      <td><span class="up">${r.best_day.date ? r.best_day.date.slice(5) : '-'}</span>
          ${r.best_day.pnl ? '<small class="up"> +'+r.best_day.pnl.toFixed(2)+'%</small>' : ''}</td>
      <td><span class="down">${r.worst_day.date ? r.worst_day.date.slice(5) : '-'}</span>
          ${r.worst_day.pnl !== undefined ? '<small class="down"> '+r.worst_day.pnl.toFixed(2)+'%</small>' : ''}</td>
    </tr>`;
  }).join('');
}

async function loadAll() {
  await Promise.all([loadPeriodStats(), loadHistEquity(), loadMonthly()]);
}

loadAll();
</script>
"""


# ── 페이지 3: 매매 원장 ────────────────────────────────────────────────────────

PAGE_TRADES_HTML = """
<main>

<!-- 4 요약 카드 -->
<div class="grid-4">
  <div class="card blue">
    <div class="card-label">총 거래 수</div>
    <div class="card-value" id="t-count">--</div>
    <div class="card-sub" id="t-buy-sell">매수 -- / 매도 --</div>
  </div>
  <div class="card cyan">
    <div class="card-label">매도 손익 합계</div>
    <div class="card-value" id="t-total-pnl">--</div>
    <div class="card-sub"  id="t-total-krw">-- 원</div>
  </div>
  <div class="card green">
    <div class="card-label">수익 / 손실 거래</div>
    <div class="card-value" id="t-win-loss">-- / --</div>
    <div class="card-sub"  id="t-win-rate">승률 --%</div>
  </div>
  <div class="card yellow">
    <div class="card-label">최대 수익 / 손실</div>
    <div class="card-value" id="t-best-worst">-- / --</div>
    <div class="card-sub"  id="t-best-ticker" style="font-size:11px"></div>
  </div>
</div>

<!-- 거래 테이블 -->
<div class="card">
  <div class="section-title" style="justify-content:space-between">
    <span>매매 원장</span>
    <span id="trades-count-label" style="color:var(--muted);font-size:11px"></span>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>구분</th><th>종목</th><th>전략</th>
          <th>가격</th><th>수량</th><th>손익%</th><th>손익(원)</th><th>사유</th>
        </tr>
      </thead>
      <tbody id="trades-tbody"></tbody>
    </table>
  </div>
</div>

</main>

<script>
let _allTrades = [];

async function loadTrades() {
  const filterTicker   = document.getElementById('f-ticker').value.trim().toUpperCase();
  const filterStrategy = document.getElementById('f-strategy').value;
  const filterSide     = document.getElementById('f-side').value;

  let url = '/api/trades/list' + marketParam();
  if (filterTicker)   url += '&ticker='   + encodeURIComponent(filterTicker);
  if (filterStrategy) url += '&strategy=' + encodeURIComponent(filterStrategy);
  if (filterSide)     url += '&side='     + encodeURIComponent(filterSide);

  const trades = await fetch(url).then(r => r.json()).catch(() => []);
  _allTrades = trades;
  renderTrades(trades);
  renderSummaryCards(trades);
}

function renderSummaryCards(trades) {
  const sells = trades.filter(t => t.side === 'sell' || t.side === '매도');
  const buys  = trades.filter(t => t.side === 'buy'  || t.side === '매수');
  const wins  = sells.filter(t => t.pnl_pct > 0);
  const losses= sells.filter(t => t.pnl_pct <= 0);
  const totalPnl = sells.reduce((s, t) => s + (t.pnl_pct || 0), 0);
  const totalKrw = sells.reduce((s, t) => s + (t.pnl || 0), 0);
  const maxWin  = sells.length ? Math.max(...sells.map(t => t.pnl_pct)) : 0;
  const maxLoss = sells.length ? Math.min(...sells.map(t => t.pnl_pct)) : 0;

  document.getElementById('t-count').textContent   = fmt.num(trades.length);
  document.getElementById('t-buy-sell').textContent = `매수 ${buys.length} / 매도 ${sells.length}`;

  const tpEl = document.getElementById('t-total-pnl');
  tpEl.textContent = fmt.pct(totalPnl);
  tpEl.className   = 'card-value ' + colorClass(totalPnl);
  document.getElementById('t-total-krw').textContent = fmt.krw(totalKrw);

  const wlEl = document.getElementById('t-win-loss');
  wlEl.innerHTML = `<span class="up">${wins.length}</span> / <span class="down">${losses.length}</span>`;
  const wr = sells.length ? (wins.length / sells.length * 100).toFixed(1) : 0;
  document.getElementById('t-win-rate').textContent = `승률 ${wr}%`;

  document.getElementById('t-best-worst').innerHTML =
    `<span class="up">${fmt.pct(maxWin)}</span> / <span class="down">${fmt.pct(maxLoss)}</span>`;

  document.getElementById('trades-count-label').textContent = `${trades.length}건 표시`;
}

function dayOfWeek(dateStr) {
  const days = ['일','월','화','수','목','금','토'];
  try { return days[new Date(dateStr).getDay()]; } catch { return ''; }
}

function renderTrades(trades) {
  const tbody = document.getElementById('trades-tbody');
  if (!trades.length) {
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:24px">거래 내역이 없습니다</td></tr>`;
    return;
  }

  let html = '';
  let lastDate = null;

  for (const t of trades) {
    const d = (t.date || '').slice(0, 10);
    if (d !== lastDate) {
      lastDate = d;
      const dow = dayOfWeek(d);
      const sep = '─'.repeat(30);
      html += `<tr class="date-group-row"><td colspan="8">── ${d} (${dow}) ${sep}</td></tr>`;
    }

    const isSell  = t.side === 'sell' || t.side === '매도';
    const sideLbl = isSell ? '🔴 매도' : '🟢 매수';
    const sideCls = isSell ? 'side-sell' : 'side-buy';
    const pnlPct  = isSell ? `<span class="${colorClass(t.pnl_pct)}">${fmt.pct(t.pnl_pct)}</span>` : '<span style="color:var(--muted)">-</span>';
    const pnlKrw  = isSell ? `<span class="${colorClass(t.pnl)}">${fmt.krw(t.pnl)}</span>`      : '<span style="color:var(--muted)">-</span>';
    const price   = t.price ? Number(t.price).toLocaleString() : '-';
    const qty     = t.qty   ? Number(t.qty).toLocaleString()   : '-';

    html += `
    <tr>
      <td class="${sideCls}" style="font-weight:600;white-space:nowrap">${sideLbl}</td>
      <td style="font-weight:600">${t.ticker || '-'}</td>
      <td><span style="color:var(--blue)">${t.strategy || '-'}</span></td>
      <td>${price}</td>
      <td>${qty}</td>
      <td>${pnlPct}</td>
      <td>${pnlKrw}</td>
      <td style="color:var(--muted);max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap"
          title="${(t.reason||'').replace(/"/g,'&quot;')}">${t.reason || '-'}</td>
    </tr>`;
  }

  tbody.innerHTML = html;
}

async function loadAll() {
  await loadTrades();
}

loadAll();
</script>
"""

TRADES_EXTRA_FILTERS = """
  <div class="period-sep"></div>
  <input  class="date-input" id="f-ticker"   type="text"    placeholder="종목코드 또는 이름" style="width:160px">
  <select class="date-input" id="f-strategy">
    <option value="">전략 전체</option>
    <option value="momentum">momentum</option>
    <option value="mean_reversion">mean_reversion</option>
    <option value="gap_pullback">gap_pullback</option>
    <option value="volatility_breakout">volatility_breakout</option>
  </select>
  <select class="date-input" id="f-side">
    <option value="">구분 전체</option>
    <option value="buy">매수</option>
    <option value="sell">매도</option>
  </select>
  <button class="apply-btn" onclick="loadAll()">적용</button>
"""


# ── 페이지 4: 분석 ────────────────────────────────────────────────────────────

PAGE_ANALYTICS_HTML = """
<main>

<!-- Row 1: 분석가 적중률 추이 + 모드별 성과 -->
<div class="grid-2">
  <div class="card purple">
    <div class="section-title">분석가 적중률 추이 (7일 이동평균)</div>
    <div class="chart-container"><canvas id="analystChart"></canvas></div>
  </div>
  <div class="card blue">
    <div class="section-title">모드별 성과</div>
    <div id="modes-table" class="table-wrap">
      <table>
        <thead><tr><th>모드</th><th>횟수</th><th>승률</th><th>평균손익%</th></tr></thead>
        <tbody id="modes-tbody"></tbody>
      </table>
    </div>
  </div>
</div>

<!-- Row 2: 전략별 성과 + 반복 교훈 -->
<div class="grid-2">
  <div class="card cyan">
    <div class="section-title">전략별 성과</div>
    <div class="table-wrap">
      <table>
        <thead><tr><th>전략명</th><th>횟수</th><th>승률</th><th>평균손익%</th></tr></thead>
        <tbody id="strategy-tbody"></tbody>
      </table>
    </div>
  </div>
  <div class="card yellow">
    <div class="section-title">반복 교훈 패턴</div>
    <div id="lessons-list"></div>
  </div>
</div>

<!-- Row 3: Brain 상태 + 분석가 상세 -->
<div class="grid-3">
  <div class="card">
    <div class="section-title">Brain 상태</div>
    <div id="brain-status"></div>
  </div>
  <div class="card" style="grid-column: span 2">
    <div class="section-title">분석가 성과 상세</div>
    <div id="analyst-perf-detail"></div>
  </div>
</div>

</main>

<script>

async function loadAnalystChart() {
  const d = await fetch('/api/chart/analyst?market=' + MARKET).then(r => r.json()).catch(() => ({}));
  if (!d.labels) return;

  if (charts.analyst) charts.analyst.destroy();
  charts.analyst = new Chart(document.getElementById('analystChart').getContext('2d'), {
    type: 'line',
    data: {
      labels: d.labels,
      datasets: [
        { label: 'Bull',    data: d.bull,    borderColor: 'var(--green)',  backgroundColor: 'rgba(16,185,129,0.08)',  borderWidth: 2, pointRadius: 0, tension: 0.4, fill: true },
        { label: 'Bear',    data: d.bear,    borderColor: 'var(--red)',    backgroundColor: 'rgba(239,68,68,0.08)',   borderWidth: 2, pointRadius: 0, tension: 0.4, fill: true },
        { label: 'Neutral', data: d.neutral, borderColor: 'var(--yellow)', backgroundColor: 'rgba(245,158,11,0.08)', borderWidth: 2, pointRadius: 0, tension: 0.4 },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#64748b', font: { size: 11 } } } },
      scales: {
        x: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: 'rgba(31,41,55,0.5)' } },
        y: { min: 0, max: 100,
             ticks: { color: '#64748b', font: { size: 10 }, callback: v => v + '%' },
             grid: { color: 'rgba(31,41,55,0.3)' } },
      }
    }
  });
}

async function loadPatterns() {
  const d = await fetch('/api/patterns?market=' + MARKET).then(r => r.json()).catch(() => ({}));

  // 모드별 테이블
  const modes = d.modes || {};
  document.getElementById('modes-tbody').innerHTML = Object.entries(modes)
    .sort((a, b) => b[1].count - a[1].count)
    .map(([mode, v]) => {
      const wc = v.win_rate >= 55 ? 'var(--green)' : v.win_rate >= 45 ? 'var(--yellow)' : 'var(--red)';
      const bw = Math.min(100, Math.round(v.win_rate));
      return `<tr>
        <td><span class="mode-badge mode-${mode}">${mode}</span></td>
        <td>${v.count}</td>
        <td>
          <div style="display:flex;align-items:center;gap:8px">
            <span style="color:${wc};min-width:36px">${v.win_rate}%</span>
            <div class="mini-bar-wrap"><div class="mini-bar-fill" style="width:${bw}%;background:${wc}"></div></div>
          </div>
        </td>
        <td class="${colorClass(v.avg_pnl)}">${fmt.pct(v.avg_pnl)}</td>
      </tr>`;
    }).join('');

  // 교훈
  const lessons = d.lessons || [];
  document.getElementById('lessons-list').innerHTML = lessons.map(l => `
    <div class="lesson-item">
      <span class="lesson-count">${l.count}회</span>
      <span class="lesson-text">${l.text}</span>
    </div>
  `).join('') || '<div style="color:var(--muted);font-size:13px">아직 없음</div>';
}

async function loadBrain() {
  const d = await fetch('/api/brain').then(r => r.json()).catch(() => ({}));
  const a = d.analyst || {};

  // Brain 상태 카드
  document.getElementById('brain-status').innerHTML = `
    <div style="font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:12px">
      버전 ${d.version || '-'} &nbsp;|&nbsp; ${d.trained_days || 0}일 학습 &nbsp;|&nbsp; 장세: <span style="color:var(--text)">${d.regime || '-'}</span>
    </div>
    <div style="font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:4px">마지막 업데이트</div>
    <div style="font-family:var(--mono);font-size:12px;margin-bottom:16px">${d.updated || '-'}</div>
    <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;color:var(--muted);margin-bottom:10px">분석가 적중률</div>
    ${['bull','bear','neutral'].map(k => {
      const perf = a[k] || {};
      const rate = Math.round((perf.rate || 0) * 100);
      const col  = k === 'bull' ? 'var(--green)' : k === 'bear' ? 'var(--red)' : 'var(--yellow)';
      const lbl  = k === 'bull' ? '🟢 Bull' : k === 'bear' ? '🔴 Bear' : '⚪ Neutral';
      return `
      <div style="margin-bottom:10px">
        <div style="display:flex;justify-content:space-between;font-family:var(--mono);font-size:12px;margin-bottom:4px">
          <span style="color:${col}">${lbl}</span>
          <span>${rate}%</span>
        </div>
        <div class="conf-bar"><div class="conf-bar-fill" style="width:${rate}%;background:${col}"></div></div>
      </div>`;
    }).join('')}
  `;

  // 분석가 성과 상세
  const rows = Object.entries(a);
  if (!rows.length) {
    document.getElementById('analyst-perf-detail').innerHTML =
      '<div style="color:var(--muted);font-size:13px">Brain 데이터 없음</div>';
    return;
  }
  document.getElementById('analyst-perf-detail').innerHTML = `
    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:16px">
      ${rows.map(([k, v]) => {
        const col = k === 'bull' ? 'var(--green)' : k === 'bear' ? 'var(--red)' : 'var(--yellow)';
        const lbl = k === 'bull' ? '🟢 Bull 분석가' : k === 'bear' ? '🔴 Bear 분석가' : '⚪ Neutral 분석가';
        const rate = Math.round((v.rate || 0) * 100);
        return `
        <div style="background:rgba(255,255,255,0.03);border-radius:8px;padding:16px;border:1px solid var(--border)">
          <div style="color:${col};font-weight:600;margin-bottom:10px">${lbl}</div>
          <div style="font-family:var(--mono);font-size:12px;line-height:2;color:var(--muted)">
            <div>적중률 <span style="color:${col}">${rate}%</span></div>
            <div>총 판단 <span style="color:var(--text)">${v.total || 0}회</span></div>
            <div>HIT <span class="up">${v.hits || 0}</span> / MISS <span class="down">${v.misses || 0}</span> / PARTIAL <span class="neutral-color">${v.partials || 0}</span></div>
            ${v.recent_streak !== undefined ? `<div>최근 연속 <span style="color:var(--text)">${v.recent_streak}</span></div>` : ''}
            ${v.avg_confidence !== undefined ? `<div>평균 신뢰도 <span style="color:var(--text)">${Math.round((v.avg_confidence||0)*100)}%</span></div>` : ''}
          </div>
        </div>`;
      }).join('')}
    </div>
  `;

  // 전략별 성과 (brain에 strategy 필드 있을 경우)
  const strategy = d.strategy || {};
  const stratRows = Object.entries(strategy);
  if (stratRows.length) {
    document.getElementById('strategy-tbody').innerHTML = stratRows
      .sort((a, b) => b[1].count - a[1].count)
      .map(([name, v]) => {
        const wr   = v.win_rate !== undefined ? v.win_rate : (v.wins && v.count ? Math.round(v.wins/v.count*100) : 0);
        const avg  = v.avg_pnl !== undefined ? v.avg_pnl  : (v.total_pnl && v.count ? v.total_pnl/v.count : 0);
        const wc   = wr >= 55 ? 'var(--green)' : wr >= 45 ? 'var(--yellow)' : 'var(--red)';
        const bw   = Math.min(100, Math.round(wr));
        return `<tr>
          <td style="color:var(--cyan)">${name}</td>
          <td>${v.count || 0}</td>
          <td>
            <div style="display:flex;align-items:center;gap:8px">
              <span style="color:${wc};min-width:36px">${wr}%</span>
              <div class="mini-bar-wrap"><div class="mini-bar-fill" style="width:${bw}%;background:${wc}"></div></div>
            </div>
          </td>
          <td class="${colorClass(avg)}">${fmt.pct(avg)}</td>
        </tr>`;
      }).join('');
  } else {
    document.getElementById('strategy-tbody').innerHTML =
      `<tr><td colspan="4" style="color:var(--muted);text-align:center;padding:16px">전략 데이터 없음</td></tr>`;
  }
}

async function loadAll() {
  await Promise.all([loadAnalystChart(), loadPatterns(), loadBrain()]);
}

loadAll();
</script>
"""


# ── Flask 라우트 ───────────────────────────────────────────────────────────────

@app.route("/")
def page_today():
    html = (
        _head("오늘 현황")
        + _header_html("/")
        + COMMON_JS_BLOCK
        + PAGE_TODAY_HTML
        + "</body></html>"
    )
    return render_template_string(html)


@app.route("/history")
def page_history():
    html = (
        _head("기간별 성과")
        + _header_html("/history")
        + _period_bar_html()
        + COMMON_JS_BLOCK
        + PAGE_HISTORY_HTML
        + "</body></html>"
    )
    return render_template_string(html)


@app.route("/trades")
def page_trades():
    html = (
        _head("매매 원장")
        + _header_html("/trades")
        + _period_bar_html(extra_filters=TRADES_EXTRA_FILTERS)
        + COMMON_JS_BLOCK
        + PAGE_TRADES_HTML
        + "</body></html>"
    )
    return render_template_string(html)


@app.route("/analytics")
def page_analytics():
    html = (
        _head("분석")
        + _header_html("/analytics")
        + COMMON_JS_BLOCK
        + PAGE_ANALYTICS_HTML
        + "</body></html>"
    )
    return render_template_string(html)


# ── 실행 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 52)
    print("  TRADINGBRAIN Dashboard 시작")
    print("  http://localhost:5000 으로 접속하세요")
    print()
    print("  /            오늘 현황")
    print("  /history     기간별 성과")
    print("  /trades      매매 원장")
    print("  /analytics   분석")
    print("=" * 52)
    app.run(host="0.0.0.0", port=5000, debug=False)
