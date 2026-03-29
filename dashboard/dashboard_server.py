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
import json, sys, os, re, subprocess, threading, atexit

# .env 로드 (trading_bot과 동일한 환경변수 사용)
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    pass

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
from kis_api import get_access_token, get_balance, get_usd_krw

app = Flask(__name__)


@app.after_request
def add_no_cache_headers(resp):
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp

BASE_DIR   = Path(__file__).parent.parent
LOG_DIR    = get_runtime_path("logs", "daily_judgment", make_parents=False)
BRAIN_PATH = get_runtime_path("state", "brain.json")
DIGEST_DIR = BASE_DIR / "data" / "daily_digest"
JUDGMENT_LOG_DIR = get_runtime_path("logs", "judgment", make_parents=False)
CLAUDE_CONTROL_PATH = get_runtime_path("state", "claude_control.json")
BOT_PID_PATH = get_runtime_path("state", "trading_bot.pid")
DASHBOARD_PID_PATH = get_runtime_path("state", "dashboard_server.pid")
RESTART_DIR = get_runtime_path("state", "restart", make_parents=False)
RESTART_DIR.mkdir(parents=True, exist_ok=True)

PAPER_CASH = float(os.getenv("PAPER_CASH", "10000000"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "10") or 10)
MAX_PYRAMID = int(os.getenv("MAX_PYRAMID", "8") or 8)
SYSTEM_LOG_DIR = get_runtime_path("logs", "system", make_parents=False)


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


def _write_pid_file(path: Path, process_name: str, command: list[str]) -> None:
    payload = {
        "pid": os.getpid(),
        "process": process_name,
        "started_at": datetime.now(KST).isoformat(),
        "command": command,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _clear_pid_file(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass


def _read_pid_file(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pid_alive(pid: int) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        try:
            res = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}"],
                capture_output=True,
                text=True,
                check=False,
            )
            return str(pid) in (res.stdout or "")
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


def _detached_flags() -> int:
    if os.name != "nt":
        return 0
    return 0x00000008 | 0x00000200


def _spawn_detached(command: list[str], cwd: Path) -> None:
    subprocess.Popen(
        command,
        cwd=str(cwd),
        creationflags=_detached_flags(),
        close_fds=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _write_launcher(path: Path, lines: list[str]) -> Path:
    path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    return path


def _restart_dashboard_process() -> None:
    dashboard_path = Path(__file__).resolve()
    script_path = _write_launcher(
        RESTART_DIR / "restart_dashboard.cmd",
        [
            "@echo off",
            "setlocal",
            "timeout /t 2 /nobreak >nul",
            f'cd /d "{dashboard_path.parent}"',
            f'start "" "{sys.executable}" "{dashboard_path}"',
            "endlocal",
        ],
    )
    _spawn_detached(["cmd", "/c", str(script_path)], dashboard_path.parent)
    threading.Timer(1.0, lambda: os._exit(0)).start()


def _restart_bot_process_legacy() -> tuple[bool, str]:
    info = _read_pid_file(BOT_PID_PATH)
    pid = int(info.get("pid", 0) or 0)
    if not pid or not _pid_alive(pid):
        return False, "봇 PID 파일이 없거나 프로세스가 실행 중이 아닙니다"
    helper = (
        "import subprocess,time,sys; "
        f"subprocess.run(['taskkill','/PID','{pid}','/T','/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(1.2); "
        f"subprocess.Popen(['cmd','/c','start','','{sys.executable}','{str(BASE_DIR / 'trading_bot.py')}'], "
        f"cwd={repr(str(BASE_DIR))}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
    )
    _spawn_detached([sys.executable, "-c", helper], BASE_DIR)
    return True, "봇 재시작 요청을 보냈습니다"


def _restart_bot_process() -> tuple[bool, str]:
    info = _read_pid_file(BOT_PID_PATH)
    pid = int(info.get("pid", 0) or 0)
    if not pid or not _pid_alive(pid):
        return False, "봇 PID 파일이 없거나 프로세스가 실행 중이 아닙니다"
    bot_path = BASE_DIR / "trading_bot.py"
    script_path = _write_launcher(
        RESTART_DIR / "restart_bot.cmd",
        [
            "@echo off",
            "setlocal",
            f"taskkill /PID {pid} /T /F >nul 2>nul",
            "timeout /t 2 /nobreak >nul",
            f'cd /d "{BASE_DIR}"',
            f'start "" "{sys.executable}" "{bot_path}"',
            "endlocal",
        ],
    )
    _spawn_detached(["cmd", "/c", str(script_path)], BASE_DIR)
    return True, "봇 재시작 요청을 보냈습니다"


def _is_fresh_live_status(live: dict, today_rec: dict) -> bool:
    if not live or not live.get("session_active"):
        return False

    trading_date = live.get("trading_date", "")
    today_date = today_rec.get("date", "")
    if not trading_date or not today_date:
        return False

    current_date = date.today().isoformat()
    if trading_date[:10] != today_date[:10] or today_date[:10] != current_date:
        return False
    market = live.get("market") or today_rec.get("market") or "KR"
    return _session_status(market).get("active", False)


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


def _ticker_market(ticker: str) -> str:
    ticker = (ticker or "").strip().upper()
    return "US" if ticker.isalpha() else "KR"


def _normalized_trades(rec: dict, market: str) -> list:
    trades = rec.get("trades", []) or []
    result = []
    seen = set()
    for t in trades:
        ticker = (t.get("ticker", "") or "").strip().upper()
        if not ticker or _ticker_market(ticker) != market:
            continue
        side = t.get("side", "") or ""
        qty = int(t.get("qty", 0) or 0)
        price = round(float(t.get("price", 0) or 0), 6)
        pnl = round(float(t.get("pnl", t.get("pnl_krw", 0)) or 0), 6)
        pnl_pct = round(float(t.get("pnl_pct", 0) or 0), 6)
        reason = t.get("reason", "") or ""
        trade_date = t.get("date", rec.get("date", "")) or ""
        strategy = t.get("strategy", "") or ""
        key = (trade_date, side, ticker, strategy, qty, price, pnl, pnl_pct, reason)
        if key in seen:
            continue
        seen.add(key)
        result.append({
            "date": trade_date,
            "side": side,
            "ticker": ticker,
            "strategy": strategy,
            "price": price,
            "qty": qty,
            "pnl_pct": round(pnl_pct, 2),
            "pnl": pnl,
            "reason": reason,
        })
    return result


def _log_path_for_date(rec_date: str) -> Path:
    ymd = (rec_date or "").replace("-", "")[:8]
    return SYSTEM_LOG_DIR / f"trading_{ymd}.log"


def _parse_trade_log_lines(rec_date: str, market: str) -> list:
    path = _log_path_for_date(rec_date)
    if not path.exists():
        return []

    buy_re = re.compile(
        r"\[PAPER BUY\]\s+(?P<ticker>[A-Z0-9]+)\s+(?P<qty>\d+)@(?P<price>[0-9,]+(?:\.[0-9]+)?)\s+\|\s+(?P<strategy>[^|]+?)\s+\|\s+주문번호=(?P<order_no>\d+)"
    )
    sell_re = re.compile(
        r"\[PAPER SELL\]\s+(?P<ticker>[A-Z0-9]+)\s+(?P<qty>\d+)@(?P<price>[0-9,]+(?:\.[0-9]+)?)\s+\|\s+주문번호=(?P<order_no>\d+)"
    )
    close_re = re.compile(
        r"\[(?P<reason>[a-zA-Z_]+)\]\s+(?P<ticker>[A-Z0-9]+)\s+(?P<pnl>[+\-]?[0-9,]+(?:\.[0-9]+)?)\s+\((?P<pnl_pct>[+\-]?[0-9.]+)%\)"
    )

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    rows = []
    pending_sell = None
    rec_date_iso = f"{rec_date[:4]}-{rec_date[4:6]}-{rec_date[6:8]}" if "-" not in rec_date else rec_date[:10]
    for line in lines:
        buy_match = buy_re.search(line)
        if buy_match:
            ticker = buy_match.group("ticker").upper()
            if _ticker_market(ticker) != market:
                continue
            rows.append({
                "date": rec_date_iso,
                "time": line[11:16],
                "side": "buy",
                "ticker": ticker,
                "strategy": buy_match.group("strategy").strip(),
                "price": float(buy_match.group("price").replace(",", "")),
                "qty": int(buy_match.group("qty")),
                "pnl_pct": 0.0,
                "pnl": 0.0,
                "reason": "paper_buy",
                "order_no": buy_match.group("order_no"),
                "price_source": "system_log",
                "currency": "USD" if market == "US" else "KRW",
                "display_price": float(buy_match.group("price").replace(",", "")),
                "fill_time": "",
            })
            continue

        sell_match = sell_re.search(line)
        if sell_match:
            ticker = sell_match.group("ticker").upper()
            if _ticker_market(ticker) != market:
                pending_sell = None
                continue
            sell_price = float(sell_match.group("price").replace(",", ""))
            if sell_price <= 0:
                pending_sell = None
                continue
            pending_sell = {
                "date": rec_date_iso,
                "time": line[11:16],
                "side": "sell",
                "ticker": ticker,
                "strategy": "",
                "price": sell_price,
                "qty": int(sell_match.group("qty")),
                "pnl_pct": 0.0,
                "pnl": 0.0,
                "reason": "paper_sell",
                "order_no": sell_match.group("order_no"),
                "price_source": "system_log",
                "currency": "USD" if market == "US" else "KRW",
                "display_price": sell_price,
                "fill_time": "",
            }
            rows.append(pending_sell)
            continue

        close_match = close_re.search(line)
        if close_match and pending_sell:
            ticker = close_match.group("ticker").upper()
            if ticker != pending_sell.get("ticker"):
                continue
            pending_sell["reason"] = close_match.group("reason")
            pending_sell["pnl"] = float(close_match.group("pnl").replace(",", ""))
            pending_sell["pnl_pct"] = round(float(close_match.group("pnl_pct")), 2)
            pending_sell = None

    seen = set()
    result = []
    for row in rows:
        key = (
            row.get("date", ""),
            row.get("side", ""),
            row.get("ticker", ""),
            row.get("order_no", ""),
            row.get("qty", 0),
            round(float(row.get("price", 0) or 0), 6),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def _trades_for_record(rec: dict, market: str) -> list:
    trades = _normalized_trades(rec, market)
    log_rows = _parse_trade_log_lines(rec.get("date", ""), market)
    if not trades:
        return log_rows

    # Backfill missing trade metadata such as time/order_no/fill_time from system logs.
    log_by_order = {}
    log_candidates = {}
    log_candidates_loose = {}
    for row in log_rows:
        order_no = row.get("order_no", "") or ""
        if order_no:
            log_by_order[order_no] = row
        key = (
            row.get("side", ""),
            row.get("ticker", ""),
            int(row.get("qty", 0) or 0),
            round(float(row.get("price", 0) or 0), 4),
        )
        log_candidates.setdefault(key, []).append(row)
        loose_key = (
            row.get("side", ""),
            row.get("ticker", ""),
            int(row.get("qty", 0) or 0),
            row.get("reason", ""),
        )
        log_candidates_loose.setdefault(loose_key, []).append(row)

    enriched = []
    used_ids = set()
    for t in trades:
        row = dict(t)
        row["strategy"] = _ko_strategy_name(row.get("strategy", ""))
        match = None
        order_no = row.get("order_no", "") or ""
        if order_no:
            match = log_by_order.get(order_no)
        if match is None:
            key = (
                row.get("side", ""),
                row.get("ticker", ""),
                int(row.get("qty", 0) or 0),
                round(float(row.get("display_price", row.get("price", 0)) or 0), 4),
            )
            for cand in log_candidates.get(key, []):
                cand_id = (
                    cand.get("date", ""),
                    cand.get("time", ""),
                    cand.get("side", ""),
                    cand.get("ticker", ""),
                    cand.get("order_no", ""),
                )
                if cand_id in used_ids:
                    continue
                match = cand
                used_ids.add(cand_id)
                break
        if match is None:
            loose_key = (
                row.get("side", ""),
                row.get("ticker", ""),
                int(row.get("qty", 0) or 0),
                row.get("reason", ""),
            )
            for cand in log_candidates_loose.get(loose_key, []):
                cand_id = (
                    cand.get("date", ""),
                    cand.get("time", ""),
                    cand.get("side", ""),
                    cand.get("ticker", ""),
                    cand.get("order_no", ""),
                )
                if cand_id in used_ids:
                    continue
                match = cand
                used_ids.add(cand_id)
                break
        if match is None:
            loose_key = (
                row.get("side", ""),
                row.get("ticker", ""),
                int(row.get("qty", 0) or 0),
                "",
            )
            for cand in log_candidates_loose.get(loose_key, []):
                cand_id = (
                    cand.get("date", ""),
                    cand.get("time", ""),
                    cand.get("side", ""),
                    cand.get("ticker", ""),
                    cand.get("order_no", ""),
                )
                if cand_id in used_ids:
                    continue
                match = cand
                used_ids.add(cand_id)
                break
        if match:
            for field in ("time", "order_no", "fill_time", "price_source", "currency", "display_price", "strategy"):
                if not row.get(field):
                    row[field] = match.get(field, row.get(field, ""))
            if not row.get("reason"):
                row["reason"] = match.get("reason", "")
        if not row.get("time") and row.get("reason") == "session_close":
            row["time"] = "05:00" if market == "US" else "16:00"
        row["strategy"] = _ko_strategy_name(row.get("strategy", ""))
        row["source_kind"] = "trade_record"
        enriched.append(row)
    return enriched


def _record_trade_count(rec: dict, market: str) -> int:
    return int(_record_metrics(rec, market).get("trades", 0) or 0)


def _record_metrics(rec: dict, market: str) -> dict:
    trades = _trades_for_record(rec, market)
    sells = [t for t in trades if t.get("side") == "sell"]
    actual = rec.get("actual_result", {}) or {}

    if sells:
        pnl_krw = round(sum(float(t.get("pnl", 0) or 0) for t in sells), 6)
        cumulative = float(actual.get("cumulative", PAPER_CASH) or PAPER_CASH)
        base_equity = cumulative - pnl_krw
        pnl_pct = float(actual.get("pnl_pct", 0) or 0)
        if base_equity > 0:
            pnl_pct = (pnl_krw / base_equity) * 100.0
        return {
            "trades": len(sells),
            "pnl_krw": pnl_krw,
            "pnl_pct": pnl_pct,
            "win": pnl_krw > 0,
            "cumulative": cumulative,
        }

    if trades:
        return {
            "trades": len(trades),
            "pnl_krw": float(actual.get("pnl_krw", 0) or 0),
            "pnl_pct": float(actual.get("pnl_pct", 0) or 0),
            "win": bool(actual.get("win", False)),
            "cumulative": float(actual.get("cumulative", PAPER_CASH) or PAPER_CASH),
        }

    return {
        "trades": int(actual.get("trades", 0) or 0),
        "pnl_krw": float(actual.get("pnl_krw", 0) or 0),
        "pnl_pct": float(actual.get("pnl_pct", 0) or 0),
        "win": bool(actual.get("win", False)),
        "cumulative": float(actual.get("cumulative", PAPER_CASH) or PAPER_CASH),
    }


def _live_trades(market: str) -> list:
    live = _load_live_status(market)
    if not live or not live.get("session_active"):
        return []
    trade_date = live.get("trading_date", "")
    if trade_date[:10] != date.today().isoformat():
        return []
    rows = []
    seen = set()
    for order in live.get("pending_orders", []) or []:
        ticker = (order.get("ticker", "") or "").strip().upper()
        if not ticker or _ticker_market(ticker) != market:
            continue
        key = ("pending", ticker, order.get("order_no", ""))
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "date": trade_date,
            "time": (str(order.get("created_at", ""))[11:16] if order.get("created_at") else live.get("updated_at", "")),
            "side": "buy",
            "ticker": ticker,
            "strategy": order.get("strategy", ""),
            "price": round(float(order.get("raw_price", 0) or 0), 6),
            "qty": int(order.get("qty", 0) or 0),
            "pnl_pct": 0.0,
            "pnl": 0.0,
            "reason": "pending_order",
            "order_no": order.get("order_no", ""),
            "price_source": "pending_order",
            "currency": "USD" if market == "US" else "KRW",
            "display_price": round(float(order.get("raw_price", 0) or 0), 6),
            "fill_time": "",
            "source_kind": "pending_order",
        })
    for pos in live.get("positions", []) or []:
        ticker = (pos.get("ticker", "") or "").strip().upper()
        if not ticker or _ticker_market(ticker) != market:
            continue
        key = ("position", ticker)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "date": trade_date,
            "time": live.get("updated_at", ""),
            "side": "buy",
            "ticker": ticker,
            "strategy": pos.get("strategy", ""),
            "price": round(float(pos.get("avg_price", 0) or 0), 6),
            "qty": int(pos.get("qty", 0) or 0),
            "pnl_pct": 0.0,
            "pnl": 0.0,
            "reason": "live_position",
            "order_no": pos.get("order_no", ""),
            "price_source": pos.get("price_source", "broker_balance"),
            "currency": pos.get("currency", "USD" if market == "US" else "KRW"),
            "display_price": round(float(pos.get("avg_price", 0) or 0), 6),
            "fill_time": pos.get("fill_time", ""),
            "source_kind": "live_position",
        })
    return rows


def _trade_rows_for_records(records: list, market: str) -> list:
    trades = []
    for r in records:
        for t in _trades_for_record(r, market):
            trades.append(t)
    seen = set()
    deduped = []
    for t in trades:
        key = (
            t.get("date", ""),
            t.get("time", ""),
            t.get("side", ""),
            t.get("ticker", ""),
            t.get("order_no", ""),
            int(t.get("qty", 0) or 0),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(t)
    return deduped


def _enrich_trade_row(row: dict, market: str) -> dict:
    t = dict(row or {})
    t["source_kind"] = t.get("source_kind", "trade_record")
    t["source_kind_label"] = {
        "trade_record": "체결",
        "pending_order": "미체결",
        "live_position": "라이브",
        "recovered": "복구",
    }.get(t["source_kind"], t["source_kind"])
    t["strategy"] = _ko_strategy_name(t.get("strategy", ""))
    currency = t.get("currency") or ("USD" if market == "US" else "KRW")
    qty = int(t.get("qty", 0) or 0)
    display_price = float(t.get("display_price", t.get("price", 0)) or 0)
    pnl_pct = float(t.get("pnl_pct", 0) or 0)
    pnl_krw = float(t.get("pnl", 0) or 0)
    side = (t.get("side", "") or "").lower()

    if side == "sell" and qty > 0 and display_price > 0:
        buy_price = float(t.get("buy_price_native", 0) or 0)
        # Some US sell rows in daily_judgment are stored in KRW risk_price, not native USD.
        # Normalize them back to native price before rendering.
        if market == "US" and not t.get("currency") and display_price >= 1000 and buy_price <= 0 and pnl_pct > -99.99:
            inferred_buy = display_price / (1.0 + (pnl_pct / 100.0)) if (1.0 + (pnl_pct / 100.0)) != 0 else 0.0
            if inferred_buy >= 1000:
                try:
                    usdkrw = float(get_usd_krw())
                except Exception:
                    usdkrw = float(os.getenv("USD_KRW_RATE", "1350") or 1350)
                if usdkrw > 0:
                    display_price = display_price / usdkrw
                    inferred_buy = inferred_buy / usdkrw
                    currency = "USD"
                    t["price_source"] = t.get("price_source") or "reconstructed_native"
                    buy_price = inferred_buy
        if buy_price <= 0 and pnl_pct > -99.99:
            try:
                buy_price = display_price / (1.0 + (pnl_pct / 100.0))
            except ZeroDivisionError:
                buy_price = 0.0
        if market == "US" and display_price > 0 and display_price < 1000:
            currency = "USD"
        t["buy_price_native"] = round(buy_price, 6) if buy_price > 0 else 0.0
    elif market == "US" and display_price > 0 and display_price < 1000:
        currency = "USD"

    t["currency"] = currency
    t["display_price"] = round(display_price, 6)
    t["trade_total_native"] = round(display_price * qty, 6) if qty > 0 and display_price > 0 else 0.0
    if not t.get("time") and t.get("reason") == "session_close":
        t["time"] = "05:00" if market == "US" else "16:00"

    if side == "sell" and qty > 0 and display_price > 0:
        buy_price = float(t.get("buy_price_native", 0) or 0)
        t["buy_total_native"] = round(buy_price * qty, 6) if buy_price > 0 else 0.0
        t["sell_total_native"] = round(display_price * qty, 6)
        t["pnl_krw"] = round(pnl_krw, 6)
    else:
        t["buy_price_native"] = round(float(t.get("buy_price_native", 0) or 0), 6)
        t["buy_total_native"] = round(float(t.get("buy_total_native", 0) or 0), 6)
        t["sell_total_native"] = round(float(t.get("sell_total_native", 0) or 0), 6)
        t["pnl_krw"] = round(pnl_krw, 6)

    return t


def _trade_stats_from_rows(trades: list, days: int) -> dict:
    sells = [t for t in trades if t.get("side") == "sell"]
    wins = [t for t in sells if float(t.get("pnl", 0) or 0) > 0]
    losses = [t for t in sells if float(t.get("pnl", 0) or 0) < 0]
    total_pnl = sum(float(t.get("pnl_pct", 0) or 0) for t in sells)
    trade_count = len(sells)
    return {
        "days": days,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / trade_count * 100, 1) if trade_count else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / trade_count, 2) if trade_count else 0,
        "trades": trade_count,
        "basis": "closed_trades",
    }


def _parse_runtime_events(market: str, limit: int = 200) -> list:
    path = _log_path_for_date(date.today().isoformat())
    if not path.exists():
        return []

    ts_re = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    trailing_re = re.compile(r"\[TRAILING\]\s+(?P<ticker>[A-Z0-9]+)\s+trail_sl=(?P<price>[0-9,]+(?:\.[0-9]+)?)")
    buy_re = re.compile(r"\[PAPER BUY\]\s+(?P<ticker>[A-Z0-9]+)\s+(?P<qty>\d+)@(?P<price>[0-9,]+(?:\.[0-9]+)?)")
    sell_re = re.compile(r"\[PAPER SELL\]\s+(?P<ticker>[A-Z0-9]+)\s+(?P<qty>\d+)@(?P<price>[0-9,]+(?:\.[0-9]+)?)")
    close_re = re.compile(r"\[(?P<reason>[a-zA-Z_]+)\]\s+(?P<ticker>[A-Z0-9]+)\s+(?P<pnl>[+\-]?[0-9,]+(?:\.[0-9]+)?)\s+\((?P<pnl_pct>[+\-]?[0-9.]+)%\)")
    sell_fail_re = re.compile(r"sell order failed \[(?P<ticker>[A-Z0-9]+)\]:\s*(?P<reason>.+)$")
    cycle_err_re = re.compile(r"cycle error \[(?P<ticker>[A-Z0-9]+)\]:\s*(?P<reason>.+)$")

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return []

    events = []
    pending_sell_reason = {}
    for line in lines:
        ts_match = ts_re.search(line)
        if not ts_match:
            continue
        ts = ts_match.group("ts").replace(" ", "T")

        m = trailing_re.search(line)
        if m:
            ticker = m.group("ticker").upper()
            if _ticker_market(ticker) == market:
                events.append({
                    "timestamp": ts,
                    "event": "trailing",
                    "market": market,
                    "ticker": ticker,
                    "reason": "trailing",
                    "price": float(m.group("price").replace(",", "")),
                    "mode": "",
                })
            continue

        m = buy_re.search(line)
        if m:
            ticker = m.group("ticker").upper()
            if _ticker_market(ticker) == market:
                events.append({
                    "timestamp": ts,
                    "event": "buy_filled",
                    "market": market,
                    "ticker": ticker,
                    "reason": "paper_buy",
                    "price": float(m.group("price").replace(",", "")),
                    "mode": "",
                })
            continue

        m = sell_re.search(line)
        if m:
            ticker = m.group("ticker").upper()
            if _ticker_market(ticker) == market:
                events.append({
                    "timestamp": ts,
                    "event": "sell_filled",
                    "market": market,
                    "ticker": ticker,
                    "reason": "paper_sell",
                    "price": float(m.group("price").replace(",", "")),
                    "mode": "",
                })
            continue

        m = close_re.search(line)
        if m:
            ticker = m.group("ticker").upper()
            if _ticker_market(ticker) == market:
                pending_sell_reason[ticker] = m.group("reason")
                for ev in reversed(events):
                    if ev.get("ticker") == ticker and ev.get("event") == "sell_filled":
                        ev["reason"] = m.group("reason")
                        break
            continue

        m = sell_fail_re.search(line)
        if m:
            ticker = m.group("ticker").upper()
            if _ticker_market(ticker) == market:
                events.append({
                    "timestamp": ts,
                    "event": "sell_failed",
                    "market": market,
                    "ticker": ticker,
                    "reason": m.group("reason").strip(),
                    "price": 0,
                    "mode": "",
                })
            continue

        m = cycle_err_re.search(line)
        if m:
            ticker = m.group("ticker").upper()
            if _ticker_market(ticker) == market:
                events.append({
                    "timestamp": ts,
                    "event": "cycle_error",
                    "market": market,
                    "ticker": ticker,
                    "reason": m.group("reason").strip(),
                    "price": 0,
                    "mode": "",
                })
            continue

    events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return events[:limit]


def _clean_lesson(text: str) -> str:
    text = (text or "").strip()
    if not text:
        return ""
    placeholders = ("오류로 자동 판정", "자동 판정", "오류로", "자동")
    if any(p in text for p in placeholders):
        return ""
    return text


def load_digest_records_filtered(market: str, period: str, start: str, end: str) -> list:
    if not DIGEST_DIR.exists():
        return []

    records = []
    for path in sorted(DIGEST_DIR.glob(f"*_{market}.json")):
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            if rec.get("market") == market:
                records.append(rec)
        except Exception:
            pass

    if period == "all":
        return records

    today = date.today()
    if period == "week":
        d_start = today - timedelta(days=today.weekday())
        d_end = today
    elif period == "month":
        d_start = today.replace(day=1)
        d_end = today
    elif period == "3month":
        d_start = today - timedelta(days=90)
        d_end = today
    elif period == "custom":
        d_start = _parse_date(start)
        d_end = _parse_date(end) if end else today
    else:
        d_start = today - timedelta(days=30)
        d_end = today

    return [
        r for r in records
        if d_start <= _parse_date(r.get("date", "")) <= d_end
    ]


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

def _load_live_status(market: str) -> dict:
    """trading_bot이 사이클마다 기록하는 라이브 상태 파일 읽기"""
    path = BASE_DIR / "state" / f"live_status_{market}.json"
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _default_claude_control() -> dict:
    return {
        "enabled": True,
        "updated_at": "",
        "updated_by": "default",
        "last_trigger_at": "",
        "last_trigger_market": "",
        "last_trigger_source": "",
        "last_result_at": "",
        "last_result_market": "",
        "last_result_status": "idle",
        "last_error": "",
        "pending_trigger": None,
    }


def _load_claude_control() -> dict:
    data = _default_claude_control()
    if CLAUDE_CONTROL_PATH.exists():
        try:
            saved = json.loads(CLAUDE_CONTROL_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data.update(saved)
        except Exception:
            pass
    return data


def _save_claude_control(data: dict):
    merged = _default_claude_control()
    merged.update(data or {})
    CLAUDE_CONTROL_PATH.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_live_status(market: str) -> dict:
    """Override: sanitize cross-market rows inside live status."""
    path = BASE_DIR / "state" / f"live_status_{market}.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    changed = False
    positions = []
    for pos in data.get("positions", []) or []:
        ticker = str(pos.get("ticker", "") or "")
        if ticker and _ticker_market(ticker) == market:
            positions.append(pos)
        else:
            changed = True
    pending_orders = []
    for order in data.get("pending_orders", []) or []:
        ticker = str(order.get("ticker", "") or "")
        order_market = str(order.get("market", "") or "")
        inferred_market = order_market or _ticker_market(ticker)
        if ticker and inferred_market == market:
            order["market"] = market
            pending_orders.append(order)
        else:
            changed = True
    if data.get("market") != market:
        data["market"] = market
        changed = True
    if positions != (data.get("positions", []) or []):
        data["positions"] = positions
        data["position_count"] = len(positions)
        changed = True
    if pending_orders != (data.get("pending_orders", []) or []):
        data["pending_orders"] = pending_orders
        data["pending_count"] = len(pending_orders)
        changed = True
    if changed:
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return data


def _load_claude_control() -> dict:
    """Override: recover stale running state to error."""
    data = _default_claude_control()
    if CLAUDE_CONTROL_PATH.exists():
        try:
            saved = json.loads(CLAUDE_CONTROL_PATH.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data.update(saved)
        except Exception:
            pass
    status = str(data.get("last_result_status", "idle") or "idle").lower()
    if status == "running":
        ts = data.get("last_result_at") or data.get("last_trigger_at") or ""
        stale = False
        try:
            if ts:
                last_dt = datetime.fromisoformat(ts)
                if last_dt.tzinfo is None:
                    last_dt = KST.localize(last_dt)
                stale = datetime.now(KST) - last_dt > timedelta(minutes=10)
            else:
                stale = True
        except Exception:
            stale = True
        if stale:
            data["last_result_status"] = "error"
            data["last_error"] = data.get("last_error") or "이전 Claude 재판단이 stale 상태로 정리되었습니다."
            try:
                _save_claude_control(data)
            except Exception:
                pass
    return data


def _claude_reinvoke_stats_today(market: str) -> dict:
    path = _log_path_for_date(date.today().isoformat())
    if not path.exists():
        return {
            "count": 0, "changed": 0, "unchanged": 0,
            "wins": 0, "losses": 0, "flats": 0,
            "win_rate": 0.0, "pnl_krw": 0.0,
        }

    start_re = re.compile(r"\[긴급 재판단 시작\]\s+(?P<market>[A-Z]+)\s+\|")
    done_re = re.compile(r"\[긴급 재판단 완료\]\s+(?P<old>[^ ]+)\s+→\s+(?P<new>[^ ]+)\s+size=(?P<size>\d+)%")
    close_re = re.compile(r"\[[a-zA-Z_]+\]\s+(?P<ticker>[A-Z0-9]+)\s+(?P<pnl>[+\-]?[0-9,]+(?:\.[0-9]+)?)\s+\((?P<pnl_pct>[+\-]?[0-9.]+)%\)")
    start_marker = f"[긴급 재판단 시작] {market} |"
    done_marker = "[긴급 재판단 완료]"
    start_marker = f"[긴급 재판단 시작] {market} |"
    done_marker = "[긴급 재판단 완료]"
    start_marker = f"[긴급 재판단 시작] {market} |"
    done_marker = "[긴급 재판단 완료]"

    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        lines = []

    pending_market = ""
    events = []
    for line in lines:
        ts = line[:19]
        m = start_re.search(line)
        if m:
            pending_market = m.group("market")
            continue
        m = done_re.search(line)
        if m:
            ev_market = pending_market or market
            if ev_market == market:
                events.append({
                    "ts": ts,
                    "market": ev_market,
                    "old_mode": m.group("old"),
                    "new_mode": m.group("new"),
                    "changed": m.group("old") != m.group("new"),
                    "pnl_krw": 0.0,
                })
            pending_market = ""

    if not events:
        return {
            "count": 0, "changed": 0, "unchanged": 0,
            "wins": 0, "losses": 0, "flats": 0,
            "win_rate": 0.0, "pnl_krw": 0.0,
        }

    event_idx = 0
    for line in lines:
        ts = line[:19]
        while event_idx + 1 < len(events) and ts >= events[event_idx + 1]["ts"]:
            event_idx += 1
        m = close_re.search(line)
        if not m:
            continue
        ticker = m.group("ticker").upper()
        if _ticker_market(ticker) != market:
            continue
        pnl = float(m.group("pnl").replace(",", ""))
        if ts >= events[event_idx]["ts"]:
            events[event_idx]["pnl_krw"] += pnl

    wins = sum(1 for e in events if e["pnl_krw"] > 0)
    losses = sum(1 for e in events if e["pnl_krw"] < 0)
    flats = sum(1 for e in events if e["pnl_krw"] == 0)
    count = len(events)
    return {
        "count": count,
        "changed": sum(1 for e in events if e["changed"]),
        "unchanged": sum(1 for e in events if not e["changed"]),
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "win_rate": round(wins / count * 100, 1) if count else 0.0,
        "pnl_krw": round(sum(e["pnl_krw"] for e in events), 2),
    }


def _claude_reinvoke_stats_today(market: str) -> dict:
    path = _log_path_for_date(date.today().isoformat())
    empty = {
        "count": 0, "changed": 0, "unchanged": 0,
        "wins": 0, "losses": 0, "flats": 0,
        "win_rate": 0.0, "pnl_krw": 0.0,
    }
    if not path.exists():
        return empty

    close_re = re.compile(r"\[[a-zA-Z_]+\]\s+(?P<ticker>[A-Z0-9]+)\s+(?P<pnl>[+\-]?[0-9,]+(?:\.[0-9]+)?)\s+\((?P<pnl_pct>[+\-]?[0-9.]+)%\)")
    reinvoke_re = re.compile(rf"\[reinvoke {market}\]\s+(?P<old>\S+)\s+.+?\s+(?P<new>\S+)\s+\|")
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return empty

    events = []
    for line in lines:
        m = reinvoke_re.search(line)
        if not m:
            continue
        events.append({
            "ts": line[:19],
            "old_mode": m.group("old"),
            "new_mode": m.group("new"),
            "changed": m.group("old") != m.group("new"),
            "pnl_krw": 0.0,
        })
    if not events:
        return empty

    event_idx = 0
    for line in lines:
        ts = line[:19]
        while event_idx + 1 < len(events) and ts >= events[event_idx + 1]["ts"]:
            event_idx += 1
        m = close_re.search(line)
        if not m:
            continue
        ticker = m.group("ticker").upper()
        if _ticker_market(ticker) != market:
            continue
        events[event_idx]["pnl_krw"] += float(m.group("pnl").replace(",", ""))

    wins = sum(1 for e in events if e["pnl_krw"] > 0)
    losses = sum(1 for e in events if e["pnl_krw"] < 0)
    flats = sum(1 for e in events if e["pnl_krw"] == 0)
    count = len(events)
    return {
        "count": count,
        "changed": sum(1 for e in events if e["changed"]),
        "unchanged": sum(1 for e in events if not e["changed"]),
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "win_rate": round(wins / count * 100, 1) if count else 0.0,
        "pnl_krw": round(sum(e["pnl_krw"] for e in events), 2),
    }


def _load_broker_positions(market: str) -> list:
    """KIS 브로커 잔고에서 현재 보유 포지션을 직접 읽어 대시보드에 표시."""
    try:
        token = get_access_token()
        bal = get_balance(token, market=market)
    except Exception:
        return []

    positions = []
    for stock in bal.get("stocks", []):
        avg_price = float(stock.get("avg_price", 0) or 0)
        current_price = float(stock.get("eval_price", 0) or 0)
        pnl_pct = float(stock.get("profit_rate", 0) or 0)
        if not pnl_pct and avg_price > 0 and current_price > 0:
            pnl_pct = (current_price / avg_price - 1.0) * 100.0
        positions.append({
            "ticker": stock.get("ticker", ""),
            "qty": int(stock.get("qty", 0) or 0),
            "avg_price": avg_price,
            "current_price": current_price,
            "pnl_pct": pnl_pct,
            "strategy": "broker_balance",
            "trailing": False,
            "price_source": "broker_balance",
            "currency": "USD" if market == "US" else "KRW",
        })
    return positions


def _broker_snapshot() -> dict:
    """KIS 브로커 기준 KR/US 통합 스냅샷."""
    try:
        token = get_access_token()
    except Exception:
        return {}
    try:
        kr = get_balance(token, market="KR")
    except Exception:
        kr = {}
    try:
        us = get_balance(token, market="US")
    except Exception:
        us = {}

    usd_krw = 0.0
    try:
        usd_krw = float(get_usd_krw())
    except Exception:
        usd_krw = float(os.getenv("USD_KRW_RATE", "1350") or 1350)

    kr_cash = float(kr.get("cash", 0) or 0)
    kr_eval = float(kr.get("total_eval", 0) or 0)
    us_eval_usd = float(us.get("total_eval", 0) or 0)
    us_eval_krw = us_eval_usd * usd_krw
    cumulative = kr_cash + kr_eval + us_eval_krw

    def _unrealized_krw(stocks: list, market: str) -> float:
        total = 0.0
        for stock in stocks or []:
            qty = float(stock.get("qty", 0) or 0)
            avg_price = float(stock.get("avg_price", 0) or 0)
            current_price = float(stock.get("eval_price", 0) or 0)
            pnl_native = (current_price - avg_price) * qty
            total += pnl_native if market == "KR" else pnl_native * usd_krw
        return total

    return {
        "usd_krw": usd_krw,
        "kr_cash": kr_cash,
        "kr_eval": kr_eval,
        "us_eval_usd": us_eval_usd,
        "us_eval_krw": us_eval_krw,
        "cumulative": cumulative,
        "unrealized_krw": {
            "KR": _unrealized_krw(kr.get("stocks", []), "KR"),
            "US": _unrealized_krw(us.get("stocks", []), "US"),
        },
    }


def _live_asset_fallback(market: str, usd_krw: float) -> dict:
    live = _load_live_status(market)
    if not live or not live.get("positions"):
        return {"asset_krw": 0.0, "unrealized_krw": 0.0}
    asset = 0.0
    unrealized = 0.0
    for pos in live.get("positions", []) or []:
        qty = float(pos.get("qty", 0) or 0)
        avg_price = float(pos.get("avg_price", 0) or 0)
        current_price = float(pos.get("current_price", 0) or 0)
        if qty <= 0 or current_price <= 0:
            continue
        if market == "US":
            asset += qty * current_price * usd_krw
            unrealized += (current_price - avg_price) * qty * usd_krw
        else:
            asset += qty * current_price
            unrealized += (current_price - avg_price) * qty
    return {"asset_krw": asset, "unrealized_krw": unrealized}


def _filter_items_for_market(items: list, market: str) -> list:
    filtered = []
    for item in items or []:
        ticker = (item.get("ticker", "") or "").strip().upper()
        if ticker and _ticker_market(ticker) == market:
            filtered.append(item)
    return filtered


_EXCHANGE_CAL_MAP = {"KR": "XKRX", "US": "XNYS"}
_STRATEGY_KO_MAP = {
    "volatility_breakout": "변동성돌파",
    "gap_pullback": "갭눌림",
    "momentum": "모멘텀",
    "mean_reversion": "평균회귀",
    "broker_sync": "브로커동기화",
    "변동성돌파": "변동성돌파",
    "갭눌림": "갭눌림",
    "모멘텀": "모멘텀",
    "평균회귀": "평균회귀",
    "브로커동기화": "브로커동기화",
}


def _ko_strategy_name(name: str) -> str:
    return _STRATEGY_KO_MAP.get((name or "").strip(), (name or "").strip())


_MODE_KO_MAP = {
    "AGGRESSIVE": "적극매수",
    "MODERATE_BULL": "강한상승",
    "Bull_Confirmed": "상승확인",
    "MILD_BULL": "완만상승",
    "CAUTIOUS": "신중",
    "NEUTRAL": "중립",
    "MILD_BEAR": "완만약세",
    "CAUTIOUS_BEAR": "신중약세",
    "DEFENSIVE": "방어",
    "HALT": "거래중지",
    "SIDEWAYS_BEAR": "횡보약세",
    "SIDEWAYS_BULL": "횡보강세",
}


def _ko_mode_name(name: str) -> str:
    return _MODE_KO_MAP.get((name or "").strip(), (name or "").strip() or "-")


_EVENT_KO_MAP = {
    "waiting": "대기",
    "signal_check": "신호 없음",
    "entry_signal": "진입 신호",
    "entry_skip": "진입 보류",
    "signal_blocked": "신호 차단",
    "buy_filled": "매수 체결",
    "sell_filled": "매도 체결",
    "trailing": "추적중",
    "cycle_error": "처리 오류",
}


def _ko_event_name(name: str) -> str:
    return _EVENT_KO_MAP.get((name or "").strip(), (name or "").strip() or "대기")


def _normalize_percent_value(value: float) -> float:
    value = float(value or 0.0)
    if value != 0 and abs(value) < 0.1:
        return value * 100.0
    return value


def _session_trade_date(market: str, now_dt=None):
    now_dt = now_dt or datetime.now(KST)
    current_date = now_dt.date()
    if market == "US" and now_dt.time() < dt_time(5, 0):
        return current_date - timedelta(days=1)
    return current_date


def _is_trading_day(market: str, check_date=None) -> bool:
    check_date = check_date or _session_trade_date(market)
    exchange = _EXCHANGE_CAL_MAP.get(market, "XKRX")
    try:
        import exchange_calendars as ec
        cal = ec.get_calendar(exchange)
        return bool(cal.is_session(str(check_date)))
    except Exception:
        return check_date.weekday() < 5


def _fallback_select_reason(ticker: str, market: str, mode: str, item: dict) -> str:
    mode_ko = _ko_mode_name(mode)
    held_qty = int(item.get("held_qty", 0) or 0)
    pending_count = int(item.get("pending_count", 0) or 0)
    last_event = item.get("last_event", "") or ""
    if held_qty > 0 and pending_count > 0:
        return f"{mode_ko} 환경에서 보유 종목 추적 중 · 미체결 주문 {pending_count}건 관리"
    if held_qty > 0:
        return f"{mode_ko} 환경에서 보유 종목 추적 관리 중"
    if pending_count > 0:
        return f"{mode_ko} 환경에서 미체결 주문 추적 대상"
    if last_event == "signal_check":
        return f"{mode_ko} 환경에서 스크리너 통과 종목 · 신호 대기"
    return f"{mode_ko} 환경에서 스크리너 통과 종목"


def _format_hhmm(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "--:--"
    try:
        return datetime.fromisoformat(text).strftime("%H:%M")
    except Exception:
        pass
    m = re.search(r"(\d{2}:\d{2})", text)
    return m.group(1) if m else "--:--"


def _session_status(market: str) -> dict:
    now = datetime.now(KST)
    cur = now.time()
    if market == "KR":
        open_t = dt_time(8, 50)
        close_t = dt_time(16, 0)
        active = _is_trading_day("KR", _session_trade_date("KR", now)) and open_t <= cur < close_t
    else:
        open_t = dt_time(22, 20)
        close_t = dt_time(5, 0)
        active = _is_trading_day("US", _session_trade_date("US", now)) and (cur >= open_t or cur < close_t)
    return {
        "market": market,
        "active": active,
        "label": "세션 진행중" if active else "세션 대기",
        "open_time": open_t.strftime("%H:%M"),
        "close_time": close_t.strftime("%H:%M"),
        "checked_at": now.strftime("%H:%M:%S"),
    }


def _claude_reinvoke_stats_today(market: str) -> dict:
    empty = {
        "count": 0, "changed": 0, "unchanged": 0,
        "wins": 0, "losses": 0, "flats": 0,
        "win_rate": 0.0, "pnl_krw": 0.0,
    }
    candidate_dates = [_session_trade_date(market).isoformat()]
    if market == "US":
        prev_date = (_session_trade_date(market) - timedelta(days=1)).isoformat()
        if prev_date not in candidate_dates:
            candidate_dates.append(prev_date)
    lines = []
    for log_date in candidate_dates:
        path = _log_path_for_date(log_date)
        if not path.exists():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            lines = []
        if any(f"[긴급 재판단 시작] {market} |" in line for line in lines):
            break
    if not lines:
        return empty

    start_re = re.compile(rf"\[긴급 재판단 시작\]\s+{market}\s+\|")
    done_re = re.compile(r"\[긴급 재판단 완료\]\s+(?P<old>\S+)\s+→\s+(?P<new>\S+)\s+size=")
    close_re = re.compile(r"\[[a-zA-Z_]+\]\s+(?P<ticker>[A-Z0-9]+)\s+(?P<pnl>[+\-]?[0-9,]+(?:\.[0-9]+)?)\s+\((?P<pnl_pct>[+\-]?[0-9.]+)%\)")
    start_marker = f"[긴급 재판단 시작] {market} |"
    done_marker = "[긴급 재판단 완료]"

    events = []
    pending_ts = None
    for line in lines:
        ts = line[:19]
        if start_re.search(line) or start_marker in line:
            pending_ts = ts
            continue
        m = done_re.search(line)
        if not m and done_marker in line:
            m = re.search(r"\[긴급 재판단 완료\]\s+(?P<old>\S+)\s+→\s+(?P<new>\S+)\s+size=", line)
        if m and pending_ts:
            events.append({
                "ts": pending_ts,
                "old_mode": m.group("old"),
                "new_mode": m.group("new"),
                "changed": m.group("old") != m.group("new"),
                "pnl_krw": 0.0,
            })
            pending_ts = None
    if not events:
        return empty

    event_idx = 0
    for line in lines:
        ts = line[:19]
        while event_idx + 1 < len(events) and ts >= events[event_idx + 1]["ts"]:
            event_idx += 1
        m = close_re.search(line)
        if not m:
            continue
        if _ticker_market(m.group("ticker").upper()) != market:
            continue
        events[event_idx]["pnl_krw"] += float(m.group("pnl").replace(",", ""))

    wins = sum(1 for e in events if e["pnl_krw"] > 0)
    losses = sum(1 for e in events if e["pnl_krw"] < 0)
    flats = sum(1 for e in events if e["pnl_krw"] == 0)
    count = len(events)
    return {
        "count": count,
        "changed": sum(1 for e in events if e["changed"]),
        "unchanged": sum(1 for e in events if not e["changed"]),
        "wins": wins,
        "losses": losses,
        "flats": flats,
        "win_rate": round(wins / count * 100, 1) if count else 0.0,
        "pnl_krw": round(sum(e["pnl_krw"] for e in events), 2),
    }


@app.route("/api/summary")
def api_summary():
    explicit_market = "market" in request.args
    market  = request.args.get("market", best_market_with_data())
    records = load_records(60, market)
    if not records and not explicit_market:
        other = "US" if market == "KR" else "KR"
        records = load_records(60, other)
        if records:
            market = other
    if not records:
        return jsonify({})

    today_rec   = load_today(market)
    result      = today_rec.get("actual_result", {})
    live        = _load_live_status(market)   # 장 중 실시간 상태

    # 장 중이면 라이브 상태 우선, 없으면 일별 기록 사용
    if not _is_fresh_live_status(live, today_rec):
        live = {}

    metrics_today = _record_metrics(today_rec, market)
    realized_pnl_krw = live.get("daily_pnl", metrics_today.get("pnl_krw", result.get("pnl_krw", 0)))
    unrealized_pnl_krw = 0.0
    pnl_krw  = realized_pnl_krw
    pnl_pct  = live.get("daily_pnl_pct", result.get("pnl_pct", 0))
    cum_base = float(result.get("cumulative", 0) or PAPER_CASH)
    cum_asset = float((live.get("total_equity", 0) if live else 0) or cum_base or PAPER_CASH)
    broker_positions = _load_broker_positions(market)
    positions = _filter_items_for_market(broker_positions if broker_positions else live.get("positions", []), market)
    pending_orders = _filter_items_for_market(live.get("pending_orders", []) if live else [], market)
    broker = _broker_snapshot()
    kr_asset = 0.0
    us_asset = 0.0
    usd_krw = float(os.getenv("USD_KRW_RATE", "1350") or 1350)

    if broker:
        usd_krw = float(broker.get("usd_krw", 0) or usd_krw)
        kr_asset = float(broker.get("kr_cash", 0) or 0) + float(broker.get("kr_eval", 0) or 0)
        us_asset = float(broker.get("us_eval_krw", 0) or 0)
        market_unrealized = float(broker.get("unrealized_krw", {}).get(market, 0) or 0)
        if market == "US" and us_asset <= 0:
            fallback = _live_asset_fallback("US", usd_krw or float(os.getenv("USD_KRW_RATE", "1350") or 1350))
            us_asset = float(fallback.get("asset_krw", 0) or 0)
            market_unrealized = float(fallback.get("unrealized_krw", 0) or 0)
        if market == "US" and kr_asset <= 0 and cum_base > us_asset:
            kr_asset = max(float(cum_base) - us_asset, 0.0)
        if market == "KR" and kr_asset <= 0 and cum_base > 0:
            kr_asset = cum_base
        cum_asset = max(float(cum_base or 0), kr_asset + us_asset)
        unrealized_pnl_krw = market_unrealized
        pnl_krw = float(realized_pnl_krw) + unrealized_pnl_krw
        if cum_asset:
            base_equity = float(cum_asset) - float(pnl_krw)
            if base_equity > 0:
                pnl_pct = (float(pnl_krw) / base_equity) * 100.0
    else:
        if market == "KR":
            kr_asset = float(cum_base or 0)
            cum_asset = max(float(cum_base or 0), kr_asset)
        else:
            fallback = _live_asset_fallback("US", usd_krw)
            us_asset = float(fallback.get("asset_krw", 0) or 0)
            if cum_base > us_asset:
                kr_asset = max(float(cum_base) - us_asset, 0.0)
            unrealized_pnl_krw = float(fallback.get("unrealized_krw", 0) or 0)
            pnl_krw = float(realized_pnl_krw) + unrealized_pnl_krw
            cum_asset = max(float(cum_base or 0), us_asset)
            if cum_asset:
                base_equity = float(cum_asset) - float(pnl_krw)
                if base_equity > 0:
                    pnl_pct = (float(pnl_krw) / base_equity) * 100.0

    metrics = [_record_metrics(r, market) for r in records]
    wins      = [m for m in metrics if m.get("win")]
    total_pnl = sum(m.get("pnl_pct", 0) for m in metrics)
    win_rate  = len(wins) / len(records) * 100 if records else 0

    streak = 0
    streak_type = None
    for m in reversed(metrics):
        w = m.get("win")
        if streak_type is None:
            streak_type = "win" if w else "lose"
            streak = 1
        elif (w and streak_type == "win") or (not w and streak_type == "lose"):
            streak += 1
        else:
            break

    summary_date = today_rec.get("date", "")
    expected_date = _session_trade_date(market).isoformat()
    if not summary_date or summary_date[:10] != expected_date:
        summary_date = expected_date

    return jsonify({
        "today": {
            "date":           summary_date,
            "pnl_pct":        round(pnl_pct, 4),
            "pnl_krw":        round(pnl_krw, 0),
            "realized_pnl_krw": round(realized_pnl_krw, 0),
            "unrealized_pnl_krw": round(unrealized_pnl_krw, 0),
            "win":            metrics_today.get("win", result.get("win", False)),
            "trades":         metrics_today.get("trades", result.get("trades", 0)),
            "mode":           live.get("mode") or today_rec.get("consensus", {}).get("mode", "-"),
            "cumulative":     cum_asset,
            "asset_krw_kr":   round(kr_asset, 0),
            "asset_krw_us":   round(us_asset, 0),
            "usd_krw":        usd_krw,
            "positions":      positions,
            "position_count": len(positions),
            "position_limit": MAX_POSITIONS,
            "position_remaining": max(MAX_POSITIONS - len(positions), 0),
            "pyramid_limit": MAX_PYRAMID,
            "pending_orders": pending_orders,
            "pending_count":  len(pending_orders),
            "live_updated":   live.get("updated_at", ""),
            "session":        _session_status(market),
        },
        "period": {
            "days":        len(records),
            "wins":        len(wins),
            "losses":      len(records) - len(wins),
            "win_rate":    round(win_rate, 1),
            "total_pnl":   round(total_pnl, 2),
            "streak":      streak,
            "streak_type": streak_type,
            "basis":       "daily_session",
        }
    })


@app.route("/api/claude/status")
def api_claude_status():
    market = request.args.get("market", best_market_with_data())
    control = _load_claude_control()
    session = _session_status(market)
    live = _load_live_status(market)
    claude = live.get("claude", {}) if isinstance(live, dict) else {}
    stats = _claude_reinvoke_stats_today(market)
    return jsonify({
        "market": market,
        "enabled": bool(control.get("enabled", True)),
        "updated_at": control.get("updated_at", ""),
        "updated_by": control.get("updated_by", ""),
        "last_trigger_at": control.get("last_trigger_at", "") or claude.get("last_trigger_at", ""),
        "last_trigger_market": control.get("last_trigger_market", "") or claude.get("last_trigger_market", ""),
        "last_trigger_source": control.get("last_trigger_source", "") or claude.get("last_trigger_source", ""),
        "last_result_at": control.get("last_result_at", "") or claude.get("last_result_at", ""),
        "last_result_market": control.get("last_result_market", "") or claude.get("last_result_market", ""),
        "last_result_status": control.get("last_result_status", "idle") or claude.get("last_result_status", "idle"),
        "last_error": control.get("last_error", "") or claude.get("last_error", ""),
        "pending_trigger": control.get("pending_trigger"),
        "session": session,
        "stats": stats,
    })


@app.route("/api/claude/toggle", methods=["POST"])
def api_claude_toggle():
    body = request.get_json(silent=True) or {}
    enabled = bool(body.get("enabled", True))
    control = _load_claude_control()
    control["enabled"] = enabled
    control["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    control["updated_by"] = "dashboard"
    if not enabled:
        control["pending_trigger"] = None
    _save_claude_control(control)
    return jsonify({"ok": True, "enabled": enabled})


@app.route("/api/claude/trigger", methods=["POST"])
def api_claude_trigger():
    body = request.get_json(silent=True) or {}
    market = body.get("market", best_market_with_data())
    session = _session_status(market)
    control = _load_claude_control()
    if not bool(control.get("enabled", True)):
        return jsonify({"ok": False, "error": "Claude 재판단 기능이 OFF 상태입니다."}), 400
    if not session.get("active"):
        return jsonify({"ok": False, "error": f"{market} 세션이 현재 비활성 상태입니다."}), 400
    control["pending_trigger"] = {
        "market": market,
        "source": "dashboard_trigger",
        "requested_at": datetime.now(KST).isoformat(timespec="seconds"),
    }
    control["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    control["updated_by"] = "dashboard"
    _save_claude_control(control)
    return jsonify({"ok": True, "queued": True, "market": market})


@app.route("/api/judgments")
def api_judgments():
    market = request.args.get("market", best_market_with_data())
    rec    = load_today(market)
    if not rec:
        return jsonify({})
    judgments  = rec.get("judgments", {})
    postmortem = rec.get("postmortem", {})
    r1 = rec.get("round1_judgments", {})
    changes = rec.get("debate_changes", [])
    return jsonify({
        "date":     rec.get("date", ""),
        "bull":     {**judgments.get("bull", {}),
                     "result": postmortem.get("bull_result", ""),
                     "why":    postmortem.get("bull_why", ""),
                     "r1_stance": r1.get("bull", {}).get("stance", "")},
        "bear":     {**judgments.get("bear", {}),
                     "result": postmortem.get("bear_result", ""),
                     "why":    postmortem.get("bear_why", ""),
                     "r1_stance": r1.get("bear", {}).get("stance", "")},
        "neutral":  {**judgments.get("neutral", {}),
                     "result": postmortem.get("neutral_result", ""),
                     "why":    postmortem.get("neutral_why", ""),
                     "r1_stance": r1.get("neutral", {}).get("stance", "")},
        "consensus":    rec.get("consensus", {}),
        "lesson":       postmortem.get("key_lesson", ""),
        "debate_changes": changes,
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
    brain = load_brain()
    recent_days = list(reversed(brain.get("markets", {}).get(market, {}).get("recent_days", [])))
    records = []
    seen_dates = set()
    if recent_days:
        for day in recent_days:
            d = (day.get("date", "") or "")[:10]
            if not d or d in seen_dates:
                continue
            seen_dates.add(d)
            records.append({
                "date": d,
                "postmortem": {
                    "bull_result": day.get("bull_result"),
                    "bear_result": day.get("bear_result"),
                    "neutral_result": day.get("neutral_result"),
                },
            })
    else:
        raw_records = load_records(30, market)
        dedup = {}
        for r in raw_records:
            d = (r.get("date", "") or "")[:10]
            if d:
                dedup[d] = r
        records = [dedup[k] for k in sorted(dedup.keys())]

    labels  = []
    bull_hits, bear_hits, neut_hits = [], [], []
    window = 7
    for i, r in enumerate(records):
        labels.append((r.get("date", "") or "")[-5:])
        start_i = max(0, i - window + 1)
        wnd = records[start_i:i+1]

        def rate(analyst, _wnd=wnd):
            key = f"{analyst}_result"
            valid = [rec.get("postmortem", {}).get(key) for rec in _wnd if rec.get("postmortem", {}).get(key)]
            if not valid:
                return 0.0
            hits = sum(1 for v in valid if v == "HIT")
            return round(hits / len(valid) * 100, 1)

        bull_hits.append(rate("bull"))
        bear_hits.append(rate("bear"))
        neut_hits.append(rate("neutral"))
    return jsonify({"labels": labels, "bull": bull_hits,
                    "bear": bear_hits, "neutral": neut_hits})


@app.route("/api/chart/market-context")
def api_market_context_chart():
    market = request.args.get("market", best_market_with_data())
    period = request.args.get("period", "3month")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    records = load_digest_records_filtered(market, period, start, end)

    labels = []
    fx = []
    risk = []
    primary = []
    secondary = []
    primary_close = []
    secondary_close = []

    for r in records:
        ctx = r.get("context", {})
        d = r.get("date", "")
        labels.append(d[-5:] if len(d) >= 5 else d)
        fx.append(ctx.get("usd_krw") or None)

        if market == "KR":
            primary_ctx = ctx.get("kospi") or {}
            secondary_ctx = ctx.get("kosdaq") or {}
            primary.append(primary_ctx.get("change_pct"))
            secondary.append(secondary_ctx.get("change_pct"))
            primary_close.append(primary_ctx.get("close"))
            secondary_close.append(secondary_ctx.get("close"))
            risk.append(ctx.get("vkospi") or None)
        else:
            primary_ctx = ctx.get("sp500") or {}
            secondary_ctx = ctx.get("nasdaq") or {}
            primary.append(primary_ctx.get("change_pct"))
            secondary.append(secondary_ctx.get("change_pct"))
            primary_close.append(primary_ctx.get("close"))
            secondary_close.append(secondary_ctx.get("close"))
            risk.append(ctx.get("vix") or None)

    meta = {
        "primary_label": "KOSPI 일간 등락률" if market == "KR" else "S&P500 일간 등락률",
        "secondary_label": "KOSDAQ 일간 등락률" if market == "KR" else "NASDAQ 일간 등락률",
        "primary_close_label": "KOSPI 지수" if market == "KR" else "S&P500 지수",
        "secondary_close_label": "KOSDAQ 지수" if market == "KR" else "NASDAQ 지수",
        "risk_label": "VKOSPI" if market == "KR" else "VIX",
        "fx_label": "USD/KRW",
    }
    return jsonify({
        "labels": labels,
        "primary": primary,
        "secondary": secondary,
        "primary_close": primary_close,
        "secondary_close": secondary_close,
        "fx": fx,
        "risk": risk,
        "meta": meta,
    })


@app.route("/api/patterns")
def api_patterns():
    market  = request.args.get("market", best_market_with_data())
    records = load_records(60, market)
    brain = load_brain()
    brain_modes = (brain.get("markets", {}).get(market, {}) or {}).get("mode_performance", {}) or {}
    lessons = {}
    modes   = {}
    for r in records:
        lesson = _clean_lesson(r.get("postmortem", {}).get("key_lesson", ""))
        if lesson:
            lessons[lesson] = lessons.get(lesson, 0) + 1
        mode   = r.get("consensus", {}).get("mode", "")
        result = r.get("actual_result", {})
        if mode:
            if mode not in modes:
                modes[mode] = {"count": 0, "wins": 0, "total_pnl": 0}
            modes[mode]["count"]     += 1
            modes[mode]["wins"]      += 1 if result.get("win") else 0
            modes[mode]["total_pnl"] += _normalize_percent_value(result.get("pnl_pct", 0))
    for m in modes:
        c = modes[m]["count"]
        modes[m]["win_rate"] = round(modes[m]["wins"] / c * 100, 1) if c else 0
        modes[m]["avg_pnl"]  = round(modes[m]["total_pnl"] / c, 2) if c else 0
    normalized_modes = {}
    for mode, stats in modes.items():
        normalized_modes[mode] = stats
    for mode, perf in brain_modes.items():
        count = int(perf.get("count", 0) or 0)
        if count <= 0:
            continue
        if mode not in normalized_modes or (normalized_modes[mode].get("count", 0) <= 0 or (normalized_modes[mode].get("win_rate", 0) == 0 and abs(float(normalized_modes[mode].get("avg_pnl", 0) or 0)) < 1e-9)):
            normalized_modes[mode] = {
                "count": count,
                "wins": round(float(perf.get("win_rate", 0) or 0) * count),
                "total_pnl": _normalize_percent_value(float(perf.get("avg_pnl", 0) or 0)) * count,
                "win_rate": round(float(perf.get("win_rate", 0) or 0) * 100, 1),
                "avg_pnl": round(_normalize_percent_value(float(perf.get("avg_pnl", 0) or 0)), 2),
            }
    for stats in normalized_modes.values():
        stats["total_pnl"] = round(_normalize_percent_value(stats.get("total_pnl", 0)), 2)
        stats["avg_pnl"] = round(_normalize_percent_value(stats.get("avg_pnl", 0)), 2)
    brain_lessons = ((brain.get("markets", {}).get(market, {}) or {}).get("current_beliefs", {}) or {}).get("learned_lessons", []) or []
    for lesson in brain_lessons:
        cleaned = _clean_lesson(lesson)
        if not cleaned:
            continue
        lessons[cleaned] = max(lessons.get(cleaned, 0), 1)
    top_lessons = sorted(lessons.items(), key=lambda x: x[1], reverse=True)[:10]
    return jsonify({
        "lessons": [{"text": k, "count": v} for k, v in top_lessons],
        "modes":   normalized_modes,
        "unit": "percent",
    })


@app.route("/api/credits")
def api_credits():
    try:
        usd_krw = float(os.getenv("USD_KRW_RATE", "1350"))
        return jsonify(credit_summary(usd_krw))
    except Exception as e:
        return jsonify({"error": str(e)})


@app.route("/api/control/status")
def api_control_status():
    dashboard_info = _read_pid_file(DASHBOARD_PID_PATH)
    bot_info = _read_pid_file(BOT_PID_PATH)
    dashboard_pid = int(dashboard_info.get("pid", os.getpid()) or os.getpid())
    bot_pid = int(bot_info.get("pid", 0) or 0)
    dashboard_alive = _pid_alive(dashboard_pid)
    bot_alive = _pid_alive(bot_pid)
    if dashboard_info and not dashboard_alive and dashboard_pid != os.getpid():
        _clear_pid_file(DASHBOARD_PID_PATH)
    if bot_info and not bot_alive:
        _clear_pid_file(BOT_PID_PATH)
    return jsonify({
        "dashboard": {
            "pid": dashboard_pid,
            "alive": dashboard_alive,
            "started_at": dashboard_info.get("started_at", ""),
        },
        "bot": {
            "pid": bot_pid,
            "alive": bot_alive,
            "started_at": bot_info.get("started_at", ""),
        },
    })


@app.route("/api/control/restart-dashboard", methods=["POST"])
def api_control_restart_dashboard():
    _restart_dashboard_process()
    return jsonify({"ok": True, "message": "대시보드 서버 재시작 요청을 보냈습니다"})


@app.route("/api/control/restart-bot", methods=["POST"])
def api_control_restart_bot():
    ok, message = _restart_bot_process()
    status = 200 if ok else 400
    return jsonify({"ok": ok, "message": message}), status


@app.route("/api/brain")
def api_brain():
    mkt   = request.args.get("market", "KR")
    brain = load_brain()
    market = brain.get("markets", {}).get(mkt, {})
    raw_strategy = market.get("strategy_performance", {}) or {}
    merged_strategy = {}
    for name, perf in raw_strategy.items():
        key = _ko_strategy_name(name)
        if key not in merged_strategy:
            merged_strategy[key] = {"count": 0, "win_weight": 0.0, "pnl_weight": 0.0, "best_k": perf.get("best_k")}
        count = int(perf.get("count", 0) or 0)
        merged_strategy[key]["count"] += count
        merged_strategy[key]["win_weight"] += float(perf.get("win_rate", 0) or 0) * count
        merged_strategy[key]["pnl_weight"] += float(perf.get("avg_pnl", 0) or 0) * count
        if merged_strategy[key].get("best_k") is None and perf.get("best_k") is not None:
            merged_strategy[key]["best_k"] = perf.get("best_k")
    strategy = {}
    for key, perf in merged_strategy.items():
        count = int(perf.get("count", 0) or 0)
        strategy[key] = {
            "count": count,
            "win_rate": round((perf.get("win_weight", 0.0) / count), 4) if count else 0.0,
            "avg_pnl": round(_normalize_percent_value(perf.get("pnl_weight", 0.0) / count), 4) if count else 0.0,
            "best_k": perf.get("best_k"),
        }
    return jsonify({
        "market":       mkt,
        "trained_days": market.get("trained_days", 0),
        "regime":       market.get("current_beliefs", {}).get("market_regime", market.get("current_regime", "unknown")),
        "analyst":      market.get("analyst_performance", {}),
        "beliefs":      market.get("current_beliefs", {}),
        "version":      brain.get("meta", {}).get("version", 0),
        "updated":      brain.get("meta", {}).get("last_updated", ""),
        "strategy":     strategy,
        "strategy_unit": "percent",
    })


@app.route("/api/brain/history")
def api_brain_history():
    mkt   = request.args.get("market", "KR")
    brain = load_brain()
    m     = brain.get("markets", {}).get(mkt, {})
    days  = list(reversed(m.get("recent_days", [])))  # 최신순
    beliefs = m.get("current_beliefs", {})
    return jsonify({
        "market":         mkt,
        "trained_days":   m.get("trained_days", 0),
        "market_regime":  beliefs.get("market_regime", "unknown"),
        "learned_lessons": beliefs.get("learned_lessons", []),
        "correction_guide": brain.get("correction_guide", {}).get(mkt, {}),
        "analyst_performance": m.get("analyst_performance", {}),
        "recent_days":    days,
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

    trades = _trade_rows_for_records(records, market)
    stats = _trade_stats_from_rows(trades, len(records))
    return jsonify(stats)


@app.route("/api/history/monthly")
def api_history_monthly():
    market  = request.args.get("market", best_market_with_data())
    records = load_records(9999, market)
    groups  = group_by_month(records)

    result = []
    for month in sorted(groups.keys(), reverse=True):
        recs  = groups[month]
        n     = len(recs)
        trades = _trade_rows_for_records(recs, market)
        stats = _trade_stats_from_rows(trades, n)
        day_groups = {}
        for t in trades:
            if t.get("side") != "sell":
                continue
            d = t.get("date", "")
            day_groups[d] = day_groups.get(d, 0.0) + float(t.get("pnl_pct", 0) or 0)
        best_day = {"date": "", "pnl": 0}
        worst_day = {"date": "", "pnl": 0}
        if day_groups:
            best_date, best_pnl = max(day_groups.items(), key=lambda x: x[1])
            worst_date, worst_pnl = min(day_groups.items(), key=lambda x: x[1])
            best_day = {"date": best_date, "pnl": round(best_pnl, 2)}
            worst_day = {"date": worst_date, "pnl": round(worst_pnl, 2)}

        result.append({
            "month":      month,
            "days":       stats["days"],
            "wins":       stats["wins"],
            "losses":     stats["losses"],
            "win_rate":   stats["win_rate"],
            "total_pnl":  stats["total_pnl"],
            "avg_pnl":    stats["avg_pnl"],
            "trades":     stats["trades"],
            "best_day":   best_day,
            "worst_day":  worst_day,
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
    metric_rows = []
    for r in records:
        result = _record_metrics(r, market)
        d = r.get("date", "")
        labels.append(d[:10] if len(d) >= 10 else d)
        pnl.append(result.get("pnl_pct", 0))
        wins.append(result.get("win", False))
        modes.append(r.get("consensus", {}).get("mode", ""))
        metric_rows.append(result)
    today_label = date.today().isoformat()
    live = _load_live_status(market)
    broker = _broker_snapshot()
    current_equity = float((broker or {}).get("cumulative", 0) or 0)
    if _is_fresh_live_status(live, load_today(market)):
        if current_equity > 0:
            if not labels or labels[-1] != today_label:
                labels.append(today_label)
                pnl.append(float(live.get("daily_pnl_pct", 0) or 0))
                wins.append(float(live.get("daily_pnl", 0) or 0) > 0)
                modes.append(live.get("mode", ""))
                metric_rows.append({
                    "pnl_krw": float(live.get("daily_pnl", 0) or 0),
                })

    if current_equity > 0 and metric_rows:
        equity = [0.0] * len(metric_rows)
        equity[-1] = current_equity
        for i in range(len(metric_rows) - 2, -1, -1):
            next_pnl = float(metric_rows[i + 1].get("pnl_krw", 0) or 0)
            equity[i] = equity[i + 1] - next_pnl
    else:
        last_equity = None
        for result in metric_rows:
            day_pnl = float(result.get("pnl_krw", 0) or 0)
            raw_cumulative = float(result.get("cumulative", 0) or 0)
            if last_equity is None:
                equity_value = raw_cumulative if raw_cumulative > 0 else PAPER_CASH + day_pnl
            else:
                recomputed = last_equity + day_pnl
                use_raw = False
                if raw_cumulative > 0 and raw_cumulative != PAPER_CASH:
                    tolerance = max(abs(day_pnl) * 3.0, abs(last_equity) * 0.20, 500000.0)
                    if abs(raw_cumulative - recomputed) <= tolerance:
                        use_raw = True
                equity_value = raw_cumulative if use_raw else recomputed
            last_equity = equity_value
            equity.append(equity_value)
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
        for t in _trades_for_record(r, market):
            t_side     = t.get("side", "")
            t_ticker   = t.get("ticker", "")
            t_strategy = t.get("strategy", "")
            if ticker   and ticker   not in t_ticker.upper():
                continue
            if strategy and strategy != t_strategy:
                continue
            if side     and side     != t_side:
                continue
            trades.append(t)

    seen = set()
    deduped = []
    for t in trades:
        key = (
            t.get("date", ""),
            t.get("time", ""),
            t.get("side", ""),
            t.get("ticker", ""),
            t.get("order_no", ""),
            int(t.get("qty", 0) or 0),
        )
        if key in seen:
            continue
        seen.add(key)
        deduped.append(t)
    trades = deduped

    for t in _live_trades(market):
        t_side     = t.get("side", "")
        t_ticker   = t.get("ticker", "")
        t_strategy = t.get("strategy", "")
        if ticker   and ticker   not in t_ticker.upper():
            continue
        if strategy and strategy != t_strategy:
            continue
        if side     and side     != t_side:
            continue
        key = (
            t.get("date", ""),
            t.get("time", ""),
            t.get("side", ""),
            t.get("ticker", ""),
            t.get("order_no", ""),
            int(t.get("qty", 0) or 0),
        )
        if key in seen:
            continue
        seen.add(key)
        trades.append(t)

    trades = [_enrich_trade_row(t, market) for t in trades]

    def _trade_score(row: dict) -> tuple:
        return (
            1 if row.get("source_kind") == "trade_record" else 0,
            1 if row.get("order_no") else 0,
            1 if row.get("time") else 0,
            0 if row.get("reason") == "live_position" else 1,
            0 if row.get("price_source") == "broker_balance" else 1,
        )

    merged = {}
    ordered_keys = []
    for t in trades:
        display_price = float(t.get("display_price", t.get("price", 0)) or 0)
        if t.get("side") == "buy":
            key = (
                t.get("date", ""),
                t.get("side", ""),
                t.get("ticker", ""),
                int(t.get("qty", 0) or 0),
                round(display_price, 4),
            )
        else:
            key = (
                t.get("date", ""),
                t.get("side", ""),
                t.get("ticker", ""),
                int(t.get("qty", 0) or 0),
                t.get("reason", ""),
                round(display_price, 4),
            )
        if key not in merged:
            merged[key] = t
            ordered_keys.append(key)
            continue
        if _trade_score(t) > _trade_score(merged[key]):
            merged[key] = t

    trades = [merged[k] for k in ordered_keys]
    trades.sort(key=lambda x: (x.get("date", ""), x.get("time", ""), x.get("side", "")), reverse=True)
    return jsonify(trades[:limit])


@app.route("/api/signals/recent")
def api_signals_recent():
    """최근 신호 이벤트 목록 (analysis JSONL에서 읽기)"""
    market = request.args.get("market", "KR")
    n      = min(int(request.args.get("n", "60")), 200)
    today  = datetime.now().strftime("%Y%m%d")
    log_path = BASE_DIR / "logs" / "analysis" / f"analysis_{today}.jsonl"
    events: list[dict] = []
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            lines = []
        for line in lines:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            # extra 중첩 구조 처리: {"extra": {"extra": {...}}} 또는 {"extra": {...}}
            extra = rec.get("extra", {})
            if "extra" in extra:
                extra = extra["extra"]
            ev = extra.get("event", "")
            if ev not in ("signal_check", "entry_signal", "entry_skip", "signal_blocked"):
                continue
            ev_market = extra.get("market", "")
            if market and ev_market and ev_market != market:
                continue
            # signal_check에서 signal=none인 경우만 포함 (신호 없음)
            if ev == "signal_check" and extra.get("signal", "") != "none":
                continue
            events.append({
                "timestamp": rec.get("timestamp", ""),
                "event":     ev,
                "market":    ev_market,
                "ticker":    extra.get("ticker", ""),
                "reason":    extra.get("reason", extra.get("signal", "")),
                "price":     extra.get("price", 0),
                "mode":      extra.get("mode", ""),
            })
    # 최신순으로 n개
    events.reverse()
    runtime_events = _parse_runtime_events(market, limit=n)
    merged = events + runtime_events
    merged.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    dedup = []
    seen = set()
    for item in merged:
        key = (
            item.get("timestamp", ""),
            item.get("event", ""),
            item.get("ticker", ""),
            item.get("reason", ""),
        )
        if key in seen:
            continue
        seen.add(key)
        dedup.append(item)
    live = _load_live_status(market) or {}
    broker_positions = _filter_items_for_market(_load_broker_positions(market), market)
    price_map = {}
    for pos in _filter_items_for_market(live.get("positions", []) or [], market):
        ticker = str(pos.get("ticker", "") or "").upper()
        if not ticker:
            continue
        price_map[ticker] = float(pos.get("current_price", pos.get("avg_price", 0)) or 0)
    for pos in broker_positions:
        ticker = str(pos.get("ticker", "") or "").upper()
        if not ticker:
            continue
        price_map[ticker] = float(pos.get("current_price", pos.get("avg_price", 0)) or 0)
    normalized = []
    for item in dedup[:n]:
        row = dict(item)
        ticker = str(row.get("ticker", "") or "").upper()
        price = float(row.get("price", 0) or 0)
        if market == "US":
            if ticker in price_map and (price <= 0 or price >= 500):
                row["price"] = price_map[ticker]
                row["price_source"] = "display_price"
            elif price >= 500:
                row["price"] = 0.0
                row["price_source"] = "normalized_invalid"
        normalized.append(row)
    pinned = []
    pinned_seen = set()
    pinned_position_rows = broker_positions or _filter_items_for_market(live.get("positions", []) or [], market)
    for pos in pinned_position_rows:
        ticker = str(pos.get("ticker", "") or "").upper()
        if not ticker or ticker in pinned_seen:
            continue
        pinned_seen.add(ticker)
        pinned.append({
            "timestamp": live.get("updated_at", ""),
            "event": "live_position",
            "market": market,
            "ticker": ticker,
            "reason": "live_position",
            "price": float(pos.get("current_price", pos.get("avg_price", 0)) or 0),
            "mode": live.get("mode", ""),
            "pinned": True,
        })
    for order in _filter_items_for_market(live.get("pending_orders", []) or [], market):
        ticker = str(order.get("ticker", "") or "").upper()
        if not ticker or ticker in pinned_seen:
            continue
        pinned_seen.add(ticker)
        pinned.append({
            "timestamp": str(order.get("created_at", "") or live.get("updated_at", "")),
            "event": "pending_order",
            "market": market,
            "ticker": ticker,
            "reason": "pending_order",
            "price": float(order.get("raw_price", 0) or 0),
            "mode": live.get("mode", ""),
            "pinned": True,
        })
    rows = pinned + normalized
    rows.sort(key=lambda x: (0 if x.get("pinned") else 1, x.get("timestamp", "")), reverse=False)
    return jsonify(rows[:n])


@app.route("/api/tickers/today")
def api_tickers_today():
    """오늘 Claude가 선택한 모니터링 종목 + 최근 신호 요약"""
    market = request.args.get("market", best_market_with_data())
    rec    = load_today(market)
    tickers  = rec.get("tickers", [])
    universe = rec.get("universe_tickers", [])
    consensus = rec.get("consensus", {})
    live = _load_live_status(market) or {}
    broker_positions = _filter_items_for_market(_load_broker_positions(market), market)
    live_positions = _filter_items_for_market(live.get("positions", []) if live else [], market)
    live_pending = _filter_items_for_market(live.get("pending_orders", []) if live else [], market)

    held_entries: dict[str, int] = {}
    held_qty_map: dict[str, int] = {}
    pending_map: dict[str, int] = {}
    broker_map: dict[str, dict] = {}
    live_pos_map: dict[str, dict] = {}

    for pos in live_positions:
        ticker = (pos.get("ticker", "") or "").strip().upper()
        if not ticker:
            continue
        live_pos_map[ticker] = pos
        held_entries[ticker] = held_entries.get(ticker, 0) + 1
    for pos in broker_positions:
        ticker = (pos.get("ticker", "") or "").strip().upper()
        if not ticker:
            continue
        broker_map[ticker] = pos
        held_qty_map[ticker] = held_qty_map.get(ticker, 0) + int(pos.get("qty", 0) or 0)
        if held_qty_map[ticker] > 0 and held_entries.get(ticker, 0) <= 0:
            held_entries[ticker] = 1
    for order in live_pending:
        ticker = (order.get("ticker", "") or "").strip().upper()
        if not ticker:
            continue
        pending_map[ticker] = pending_map.get(ticker, 0) + 1

    # 오늘 analysis 로그에서 종목별 최근 이벤트 + 선택 이유 집계
    today_str = datetime.now().strftime("%Y%m%d")
    log_path  = BASE_DIR / "logs" / "analysis" / f"analysis_{today_str}.jsonl"
    ticker_last: dict[str, dict] = {}
    ticker_sig_count: dict[str, int] = {}
    ticker_skip_reasons: dict[str, list] = {}   # 미체결 이유 누적
    selection_reasons: dict[str, str] = {}
    candidates_list: list[str] = []
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8").splitlines()
        except Exception:
            lines = []
        for line in lines:
            try:
                r = json.loads(line)
            except Exception:
                continue
            extra = r.get("extra", {})
            if "extra" in extra:
                extra = extra["extra"]
            ev   = extra.get("event", "")
            mkt  = extra.get("market", "")
            if mkt and mkt != market:
                continue
            # 봇 재시작 시점 → 이전 세션 데이터 초기화
            if ev == "session_start":
                ticker_last.clear()
                ticker_sig_count.clear()
                ticker_skip_reasons.clear()
                selection_reasons.clear()
                candidates_list.clear()
                continue
            # 선택 이유 수집 (ticker_selection / ticker_rescreen)
            if ev in ("ticker_selection", "ticker_rescreen"):
                reasons = extra.get("reasons", {})
                if reasons:
                    selection_reasons.update(reasons)
            # 스크리너 후보 수집
            if ev == "screen_candidates":
                candidates_list = extra.get("tickers", [])
            t = extra.get("ticker", "")
            if not t:
                continue
            ticker_last[t] = {"event": ev, "ts": r.get("timestamp", ""),
                               "price": extra.get("price", 0),
                               "reason": extra.get("reason", "")}
            if ev == "entry_signal":
                ticker_sig_count[t] = ticker_sig_count.get(t, 0) + 1
            # 미체결 이유 누적 (entry_skip / signal_blocked / entry_failed)
            if ev in ("entry_skip", "signal_blocked", "entry_failed"):
                reason = extra.get("reason", "") or extra.get("mode", "") or ev
                if reason:
                    reasons_list = ticker_skip_reasons.setdefault(t, [])
                    if reason not in reasons_list:
                        reasons_list.append(reason)

    runtime_events = _parse_runtime_events(market, limit=200)
    for ev in reversed(runtime_events):
        t = ev.get("ticker", "")
        if not t:
            continue
        ticker_last[t] = {
            "event": ev.get("event", ""),
            "ts": ev.get("timestamp", ""),
            "price": ev.get("price", 0),
            "reason": ev.get("reason", ""),
        }

    recent_trade_map: dict[str, dict] = {}
    for trade in _trade_rows_for_records(load_records(90, market), market):
        ticker = str(trade.get("ticker", "") or "").upper()
        if not ticker or ticker in recent_trade_map:
            continue
        recent_trade_map[ticker] = trade

    # 이유 한글 매핑
    _REASON_KO = {
        "already_holding":      "이미 보유중",
        "budget_exhausted":     "예산 소진",
        "max_positions":        "최대 포지션 도달",
        "est_slippage_too_high":"슬리피지 과다",
        "HALT":                 "HALT 모드",
        "DEFENSIVE":            "DEFENSIVE 모드",
        "entry_failed":         "주문 실패",
        "signal_blocked":       "모드 차단",
    }

    result = []
    for t in tickers:
        last = ticker_last.get(t, {})
        raw_reasons = ticker_skip_reasons.get(t, [])
        skip_reasons_ko = [_REASON_KO.get(r, r) for r in raw_reasons]
        result.append({
            "ticker":          t,
            "last_event":      last.get("event", "waiting"),
            "last_ts":         _format_hhmm(last.get("ts", "")),
            "last_price":      last.get("price", 0),
            "last_reason":     last.get("reason", ""),
            "sig_count":       ticker_sig_count.get(t, 0),
            "select_reason":   selection_reasons.get(t, ""),
            "skip_reasons":    skip_reasons_ko,   # 오늘 누적 미체결 이유
        })
    # 선택되지 않은 후보 목록 (축약)
    for item in result:
        ticker = item.get("ticker", "")
        broker = broker_map.get(ticker, {})
        live_pos = live_pos_map.get(ticker, {})
        item["held_entries"] = held_entries.get(ticker, 0)
        item["held_qty"] = held_qty_map.get(ticker, 0)
        item["pending_count"] = pending_map.get(ticker, 0)
        item["pyramid_limit"] = MAX_PYRAMID
        item["pyramid_remaining"] = max(MAX_PYRAMID - held_entries.get(ticker, 0), 0)
        item["avg_price"] = float(broker.get("avg_price", 0) or live_pos.get("avg_price", 0) or 0)
        item["current_price"] = float(broker.get("current_price", 0) or live_pos.get("current_price", 0) or 0)
        item["pnl_pct"] = float(broker.get("pnl_pct", 0) or live_pos.get("pnl_pct", 0) or 0)
        recent_trade = recent_trade_map.get(ticker, {})
        if item["held_qty"] <= 0:
            item["held_qty"] = int(live_pos.get("qty", 0) or 0)
        if item["current_price"] > 0:
            item["display_price"] = item["current_price"]
        elif item["avg_price"] > 0:
            item["display_price"] = item["avg_price"]
        elif float(recent_trade.get("display_price", recent_trade.get("price", 0)) or 0) > 0:
            item["display_price"] = float(recent_trade.get("display_price", recent_trade.get("price", 0)) or 0)
        else:
            item["display_price"] = item.get("last_price", 0)
        last_price = float(item.get("last_price", 0) or 0)
        if market == "US" and item["display_price"] > 0 and (last_price <= 0 or last_price >= 500):
            item["last_price"] = item["display_price"]
        elif market == "US" and last_price >= 500:
            item["last_price"] = 0.0
        if item.get("pending_count", 0) > 0 and item.get("last_event") in ("waiting", "", None):
            item["last_event"] = "pending_order"
            item["last_ts"] = _format_hhmm(next((o.get("created_at", "") for o in live_pending if str(o.get("ticker", "")).strip().upper() == ticker), ""))
            if item.get("last_price", 0) <= 0:
                item["last_price"] = float(next((o.get("raw_price", 0) for o in live_pending if str(o.get("ticker", "")).strip().upper() == ticker), 0) or 0)
        elif item.get("held_qty", 0) > 0 and item.get("last_event") in ("waiting", "", None):
            item["last_event"] = "live_position"
            item["last_ts"] = _format_hhmm(live.get("updated_at", ""))
        elif recent_trade and item.get("last_event") in ("waiting", "", None):
            item["last_event"] = "sell_filled" if recent_trade.get("side") == "sell" else "buy_filled"
            item["last_ts"] = _format_hhmm(recent_trade.get("time", ""))
            if item.get("last_price", 0) <= 0:
                item["last_price"] = float(recent_trade.get("display_price", recent_trade.get("price", 0)) or 0)
        item["last_event_ko"] = _ko_event_name(item.get("last_event", "waiting"))
        if not item.get("select_reason"):
            item["select_reason"] = _fallback_select_reason(ticker, market, consensus.get("mode", ""), item)
    not_selected = [t for t in candidates_list if t not in tickers]
    return jsonify({
        "market":         market,
        "mode":           consensus.get("mode", ""),
        "tickers":        result,
        "universe_count": len(universe) or len(candidates_list),
        "candidates":     candidates_list,
        "not_selected":   not_selected,
    })


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

const MODE_KO = {
  AGGRESSIVE: '적극매수',
  MODERATE_BULL: '강한상승',
  Bull_Confirmed: '상승확인',
  MILD_BULL: '완만상승',
  CAUTIOUS: '신중',
  NEUTRAL: '중립',
  MILD_BEAR: '완만약세',
  CAUTIOUS_BEAR: '신중약세',
  DEFENSIVE: '방어',
  HALT: '거래중지',
};

const STRATEGY_KO = {
  volatility_breakout: '변동성돌파',
  gap_pullback: '갭눌림',
  momentum: '모멘텀',
  mean_reversion: '평균회귀',
  broker_balance: '브로커잔고',
  broker_sync: '브로커동기화',
  변동성돌파: '변동성돌파',
  갭눌림: '갭눌림',
  모멘텀: '모멘텀',
  평균회귀: '평균회귀',
};

const REGIME_KO = {
  SIDEWAYS_BEAR: '횡보약세',
  SIDEWAYS_BULL: '횡보강세',
  BULL: '상승장',
  BEAR: '약세장',
  UNKNOWN: '미확인',
  unknown: '미확인',
};

function koMode(v) { return MODE_KO[v] || v || '-'; }
function koStrategy(v) { return STRATEGY_KO[v] || v || '-'; }
function koRegime(v) { return REGIME_KO[v] || MODE_KO[v] || v || '-'; }

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
    <div class="card-sub"  id="cumulative-pnl">누적 손익: --</div>
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
    <div class="card-sub"  id="credit-remaining" style="margin-top:4px">예산 기준 잔여: --</div>
  </div>
</div>

<!-- 보유 포지션 -->
<div class="section-title" style="margin-top:24px">보유 포지션 <span id="position-count" style="font-size:12px;color:var(--text-dim);font-weight:400;margin-left:8px"></span></div>
<div id="position-board" style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px"></div>

<!-- 미체결 주문 -->
<div class="section-title" style="margin-top:8px">미체결 주문 <span id="pending-count" style="font-size:12px;color:var(--text-dim);font-weight:400;margin-left:8px"></span></div>
<div id="pending-board" style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px"></div>

<div class="section-title" style="margin-top:8px">Claude 재판단 제어 <span id="claude-status-badge" style="font-size:12px;color:var(--text-dim);font-weight:400;margin-left:8px"></span></div>
<div class="card" style="padding:16px;margin-bottom:20px">
  <div style="display:flex;flex-wrap:wrap;gap:16px;align-items:center;justify-content:space-between">
    <div style="display:flex;flex-direction:column;gap:6px;min-width:280px">
      <div id="claude-state-line" style="font-size:14px;font-weight:700;color:var(--text)">상태 확인 중...</div>
      <div id="claude-meta-line" style="font-size:12px;color:var(--text-dim)">--</div>
      <div id="claude-error-line" style="font-size:12px;color:#f87171"></div>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <button id="claude-toggle-btn" class="apply-btn" onclick="toggleClaudeReinvoke()">OFF</button>
      <button id="claude-trigger-btn" class="apply-btn" onclick="triggerClaudeReinvoke()">지금 실행</button>
    </div>
  </div>
</div>

<div class="section-title" style="margin-top:8px">프로세스 제어</div>
<div class="card" style="padding:16px;margin-bottom:20px">
  <div style="display:flex;flex-wrap:wrap;gap:16px;align-items:center;justify-content:space-between">
    <div style="display:flex;flex-direction:column;gap:6px;min-width:280px">
      <div id="control-state-line" style="font-size:14px;font-weight:700;color:var(--text)">프로세스 상태 확인 중...</div>
      <div id="control-meta-line" style="font-size:12px;color:var(--text-dim)">--</div>
      <div id="control-error-line" style="font-size:12px;color:#f87171"></div>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap">
      <button id="restart-dashboard-btn" class="apply-btn" onclick="restartDashboardServer()">대시보드 재시작</button>
      <button id="restart-bot-btn" class="apply-btn" onclick="restartTradingBot()">봇 재시작</button>
    </div>
  </div>
</div>

<div class="section-title" style="margin-top:8px">Claude 판단 타임라인</div>
<div class="card" style="padding:16px;margin-bottom:20px;max-height:440px;overflow:hidden">
  <div id="claude-timeline" style="display:flex;flex-direction:column;gap:10px;max-height:408px;overflow-y:auto;padding-right:6px"></div>
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

<!-- 현재 모니터링 종목 -->
<div class="section-title" style="margin-top:24px">
  오늘 모니터링 종목
  <span id="ticker-mode-badge" style="margin-left:10px"></span>
  <span id="ticker-ts" style="font-size:11px;color:var(--text-dim);margin-left:8px;font-weight:400"></span>
</div>
<div class="grid-2" style="margin-top:20px">
  <div class="card">
    <div class="section-title">대표 지수 레벨</div>
    <div class="chart-container"><canvas id="marketLevelChart"></canvas></div>
  </div>
  <div class="card">
    <div class="section-title">시장 지수 일별 흐름</div>
    <div class="chart-container"><canvas id="marketIndexChart"></canvas></div>
  </div>
</div>

<div class="grid-2" style="margin-top:20px">
  <div class="card">
    <div class="section-title">환율 / 리스크 지표</div>
    <div class="chart-container"><canvas id="macroChart"></canvas></div>
  </div>
</div>

<div id="ticker-board" style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px"></div>

<!-- 신호 실시간 피드 -->
<div class="section-title" style="margin-top:4px">
  실시간 신호 피드
  <span id="signal-ts" style="font-size:11px;color:var(--text-dim);margin-left:12px;font-weight:400"></span>
</div>
<div class="card" style="padding:0;overflow:hidden">
  <div style="display:flex;gap:12px;padding:10px 16px;background:var(--surface2);border-bottom:1px solid var(--border);font-size:11px;color:var(--text-dim);font-family:var(--mono)">
    <span>🟢 진입신호</span><span>⬜ 신호없음</span><span>🔴 가격오류</span><span>🟠 예산/차단</span><span>🔵 보유중</span>
  </div>
  <div id="signal-feed-cards" style="display:flex;flex-wrap:wrap;gap:10px;padding:12px 12px 0 12px"></div>
  <div id="signal-feed" style="max-height:320px;overflow-y:auto;font-family:var(--mono);font-size:12px"></div>
</div>

</main>

<script>

async function loadSummary() {
  const d = await fetch('/api/summary?market=' + MARKET).then(r => r.json()).catch(() => ({}));
  if (!d.today) return;
  const t = d.today, p = d.period;
  window.__todaySummary = t;

  const pnlEl = document.getElementById('today-pnl');
  pnlEl.textContent = fmt.pct(t.pnl_pct);
  pnlEl.className = 'card-value ' + colorClass(t.pnl_pct);
  document.getElementById('today-krw').textContent = `실현 ${fmt.krw(t.realized_pnl_krw || 0)} | 미실현 ${fmt.krw(t.unrealized_pnl_krw || 0)}`;
  document.getElementById('cumulative').textContent = fmt.asset(t.cumulative);
  const cumulativePnl = document.getElementById('cumulative-pnl');
  if (cumulativePnl) cumulativePnl.textContent = `누적 손익: ${fmt.pct(p.total_pnl || 0)}`;
  document.getElementById('today-mode').innerHTML =
    `모드: <span class="mode-badge mode-${t.mode}">${koMode(t.mode)}</span>&nbsp; 거래 ${t.trades}건`;

  if (todayMode) {
    todayMode.innerHTML = `모드: <span class="mode-badge mode-${t.mode}">${koMode(t.mode)}</span> 거래 ${t.trades || 0}건${sessTxt} | KR ${fmt.krw(t.asset_krw_kr || 0)} / US ${fmt.krw(t.asset_krw_us || 0)}`;
  }
  const wrEl = document.getElementById('win-rate');
  wrEl.textContent = p.win_rate + '%';
  wrEl.className = 'card-value ' + (p.win_rate >= 55 ? 'up' : p.win_rate >= 45 ? 'neutral-color' : 'down');
  document.getElementById('win-detail').textContent =
    `${p.wins}승 / ${p.losses}패 (${p.days}일)`;

  const emoji = p.streak_type === 'win' ? '🔥' : '❄️';
  document.getElementById('streak-val').innerHTML =
    `<span style="font-size:20px">${emoji}</span> ${p.streak}연속`;
  document.getElementById('total-pnl').textContent = `누적: ${fmt.pct(p.total_pnl)}`;

  // 보유 포지션 카드
  const positions = t.positions || [];
  const posBoard = document.getElementById('position-board');
  const posCount = document.getElementById('position-count');
  posCount.textContent = positions.length ? `${positions.length}개 보유중` : '보유 없음';
  if (positions.length === 0) {
    posBoard.innerHTML = '<div style="color:var(--text-dim);font-size:13px;padding:8px 0">현재 보유 포지션 없음</div>';
  } else {
    posBoard.innerHTML = positions.map(pos => {
      const pnl = pos.pnl_pct || 0;
      const pnlColor = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text-dim)';
      const entry = MARKET === 'KR' ? fmt.krw(pos.avg_price) : '$' + (pos.avg_price||0).toFixed(2);
      const cur   = MARKET === 'KR' ? fmt.krw(pos.current_price) : '$' + (pos.current_price||0).toFixed(2);
      return `
      <div class="card" style="min-width:180px;flex:1;padding:14px 16px">
        <div style="font-size:15px;font-weight:700;margin-bottom:6px">${pos.ticker}</div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:8px">${pos.strategy || '-'} · ${pos.qty}주</div>
        <div style="font-size:11px;color:var(--text-dim)">매수가</div>
        <div style="font-family:var(--mono);font-size:13px;margin-bottom:4px">${entry}</div>
        <div style="font-size:11px;color:var(--text-dim)">현재가</div>
        <div style="font-family:var(--mono);font-size:13px;margin-bottom:6px">${cur}</div>
        <div style="font-size:10px;color:var(--text-dim);margin-bottom:4px">매수총액 ${buyTotalText}</div>
        <div style="font-size:10px;color:var(--text-dim);margin-bottom:6px">현재평가 ${curTotalText}${!isKRW && usdKrw > 0 ? ' · 원화손익 ' + pnlKrwText : ''}</div>
        <div style="font-size:18px;font-weight:700;color:${pnlColor}">${fmt.pct(pnl)}</div>
      </div>`;
    }).join('');
  }
}

async function loadJudgments() {
  const d = await fetch('/api/judgments?market=' + MARKET).then(r => r.json()).catch(() => ({}));
  if (!d.bull) return;

  function analystCard(info, label, iconClass, stanceClass) {
    const conf = Math.round((info.confidence || 0) * 100);
    const res  = info.result || '';
    const rb   = res === 'HIT' ? 'hit' : res === 'MISS' ? 'miss' : 'partial';
    const barC = iconClass === 'bull' ? 'var(--green)' : iconClass === 'bear' ? 'var(--red)' : 'var(--yellow)';
    // 1라운드→2라운드 의견 변경 표시
    const r1stance = info.r1_stance || '';
    const debateHtml = (r1stance && r1stance !== info.stance)
      ? `<div style="font-size:11px;color:#f59e0b;margin-top:4px">💬 토론 변경: ${r1stance} → <b>${info.stance}</b></div>`
      : (r1stance ? `<div style="font-size:11px;color:#475569;margin-top:4px">💬 토론 유지: ${info.stance}</div>` : '');
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
      ${debateHtml}
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

async function loadMarketContext() {
  const d = await fetch('/api/chart/market-context?market=' + MARKET + '&period=3month').then(r => r.json()).catch(() => ({}));
  if (!d.labels || d.labels.length === 0) return;

  if (charts.marketLevel) charts.marketLevel.destroy();
  charts.marketLevel = new Chart(document.getElementById('marketLevelChart').getContext('2d'), {
    type: 'line',
    data: {
      labels: d.labels,
      datasets: [
        {
          label: d.meta.primary_close_label,
          data: d.primary_close,
          borderColor: 'rgba(6,182,212,0.95)',
          backgroundColor: 'rgba(6,182,212,0.10)',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.28,
        },
        {
          label: d.meta.secondary_close_label,
          data: d.secondary_close,
          borderColor: 'rgba(139,92,246,0.95)',
          backgroundColor: 'rgba(139,92,246,0.08)',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.28,
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#64748b', font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${Math.round(Number(ctx.raw || 0)).toLocaleString()}` } }
      },
      scales: {
        x: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: 'rgba(31,41,55,0.5)' } },
        y: {
          ticks: {
            color: '#64748b',
            font: { size: 10 },
            callback: v => Math.round(Number(v)).toLocaleString(),
          },
          grid: { color: 'rgba(31,41,55,0.3)' }
        },
      }
    }
  });

  if (charts.marketIndex) charts.marketIndex.destroy();
  charts.marketIndex = new Chart(document.getElementById('marketIndexChart').getContext('2d'), {
    type: 'line',
    data: {
      labels: d.labels,
      datasets: [
        {
          label: d.meta.primary_label,
          data: d.primary,
          borderColor: 'rgba(59,130,246,0.95)',
          backgroundColor: 'rgba(59,130,246,0.10)',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.28,
        },
        {
          label: d.meta.secondary_label,
          data: d.secondary,
          borderColor: 'rgba(16,185,129,0.95)',
          backgroundColor: 'rgba(16,185,129,0.08)',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.28,
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: '#64748b', font: { size: 11 } } },
        tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${Number(ctx.raw || 0).toFixed(2)}%` } }
      },
      scales: {
        x: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: 'rgba(31,41,55,0.5)' } },
        y: { ticks: { color: '#64748b', font: { size: 10 }, callback: v => Number(v).toFixed(1) + '%' },
             grid: { color: 'rgba(31,41,55,0.3)' } },
      }
    }
  });

  if (charts.macro) charts.macro.destroy();
  charts.macro = new Chart(document.getElementById('macroChart').getContext('2d'), {
    data: {
      labels: d.labels,
      datasets: [
        {
          type: 'line',
          label: d.meta.fx_label,
          data: d.fx,
          borderColor: 'rgba(245,158,11,0.95)',
          backgroundColor: 'rgba(245,158,11,0.08)',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.28,
          yAxisID: 'y1',
        },
        {
          type: 'line',
          label: d.meta.risk_label,
          data: d.risk,
          borderColor: 'rgba(239,68,68,0.95)',
          backgroundColor: 'rgba(239,68,68,0.08)',
          borderWidth: 2,
          pointRadius: 0,
          tension: 0.28,
          yAxisID: 'y2',
        }
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#64748b', font: { size: 11 } } } },
      scales: {
        x: { ticks: { color: '#64748b', font: { size: 10 } }, grid: { color: 'rgba(31,41,55,0.5)' } },
        y1: {
          position: 'left',
          ticks: {
            color: '#f59e0b',
            font: { size: 10 },
            callback: v => Math.round(v).toLocaleString(),
          },
          grid: { color: 'rgba(31,41,55,0.3)' }
        },
        y2: {
          position: 'right',
          ticks: { color: '#ef4444', font: { size: 10 } },
          grid: { display: false }
        }
      }
    }
  });
}

async function loadCredits() {
  const d = await fetch('/api/credits').then(r => r.json()).catch(() => ({}));
  if (d.error || !d.today) return;

  const td = d.today, tot = d.total, budget = d.budget || {};
  document.getElementById('credit-today').textContent = `$${td.cost_usd.toFixed(3)}`;
  document.getElementById('credit-total').textContent = `누적: $${tot.cost_usd.toFixed(3)}`;
  document.getElementById('credit-calls').textContent = `오늘 호출: ${td.calls}회`;
  const remainingLine = document.getElementById('credit-remaining');
  if (remainingLine) {
    const daily = budget.daily_remaining_usd;
    const monthly = budget.monthly_remaining_usd;
    if (daily != null || monthly != null) {
      remainingLine.style.display = '';
      const parts = [];
      if (daily != null) parts.push(`일 $${Number(daily).toFixed(3)}`);
      if (monthly != null) parts.push(`월 $${Number(monthly).toFixed(3)}`);
      remainingLine.textContent = `예산 기준 잔여: ${parts.join(' / ')}`;
    } else {
      remainingLine.textContent = '';
      remainingLine.style.display = 'none';
    }
  }

  document.getElementById('credit-detail').innerHTML = `
    <div><span style="color:var(--muted)">오늘 입력</span> ${td.input.toLocaleString()} tok</div>
    <div><span style="color:var(--muted)">오늘 출력</span> ${td.output.toLocaleString()} tok</div>
    <div><span style="color:var(--muted)">오늘 비용</span> <span style="color:var(--purple)">$${td.cost_usd.toFixed(4)} ≈ ${td.cost_krw.toLocaleString()}원</span></div>
    <div style="border-top:1px solid var(--border);margin:4px 0"></div>
    <div><span style="color:var(--muted)">누적 비용</span> <span style="color:var(--cyan)">$${tot.cost_usd.toFixed(4)}</span></div>
    <div><span style="color:var(--muted)">누적 입력</span> ${tot.input.toLocaleString()} tok</div>
    <div><span style="color:var(--muted)">누적 출력</span> ${tot.output.toLocaleString()} tok</div>
    <div><span style="color:var(--muted)">예산 잔여</span> ${
      budget.daily_remaining_usd != null || budget.monthly_remaining_usd != null
        ? `${budget.daily_remaining_usd != null ? `일 $${Number(budget.daily_remaining_usd).toFixed(3)}` : ''}${budget.daily_remaining_usd != null && budget.monthly_remaining_usd != null ? ' / ' : ''}${budget.monthly_remaining_usd != null ? `월 $${Number(budget.monthly_remaining_usd).toFixed(3)}` : ''}`
        : '설정 없음'
    }</div>
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

async function loadMonitorTickers() {
  const board = document.getElementById('ticker-board');
  if (!board) return;
  const d = await fetch('/api/tickers/today?market=' + MARKET).then(r => r.json()).catch(() => ({}));
  const tickers = d.tickers || [];
  if (tickers.length === 0) {
    board.innerHTML = '<span style="color:var(--text-dim);font-size:13px">종목 데이터 없음</span>';
    return;
  }

  // 모드 배지
  const modeEl = document.getElementById('ticker-mode-badge');
  if (modeEl && d.mode) {
    modeEl.innerHTML = `<span class="mode-badge mode-${d.mode}">${koMode(d.mode)}</span>`;
  }
  const total = d.universe_count || 0;
  document.getElementById('ticker-ts').textContent =
    `후보 ${total}개 중 ${tickers.length}개 선택 · 갱신: ${new Date().toLocaleTimeString()}`;

  const EVENT_MAP = {
    'entry_signal':  { icon: '🟢', label: '진입신호', color: '#10b981' },
    'entry_failed':  { icon: '❌', label: '주문실패', color: '#ef4444' },
    'signal_blocked':{ icon: '🚫', label: '모드차단', color: '#f59e0b' },
    'entry_skip':    { icon: '🟠', label: '스킵',     color: '#f59e0b' },
    'signal_check':  { icon: '⬜', label: '신호없음', color: '#64748b' },
    'waiting':       { icon: '⏳', label: '대기중',   color: '#64748b' },
  };

  board.innerHTML = tickers.map(t => {
    const ev = EVENT_MAP[t.last_event] || EVENT_MAP['waiting'];
    const displayPrice = Number(t.display_price || 0);
    const avgPrice = Number(t.avg_price || 0);
    const currentPrice = Number(t.current_price || 0);
    const shownPriceStr = displayPrice > 0
      ? (MARKET === 'KR' ? Math.round(displayPrice).toLocaleString() + '원' : '$' + displayPrice.toFixed(2))
      : '--';
    const priceStr = displayPrice > 0
      ? (MARKET === 'KR' ? Math.round(t.last_price).toLocaleString() + '원' : '$' + t.last_price.toFixed(2))
      : '--';
    const sigBadge = t.sig_count > 0
      ? `<span style="background:#10b981;color:#000;border-radius:3px;padding:1px 5px;font-size:10px;margin-left:4px">신호 ${t.sig_count}회</span>`
      : '';
    const skipHtmlFinal = pendingExplain
      ? `<div style="font-size:10px;color:#f87171;margin-top:4px">⚠ ${pendingExplain}</div>`
      : skipHtml;
    const heldEntries = Number(t.held_entries || 0);
    const heldQty = Number(t.held_qty || 0);
    const pendingCount = Number(t.pending_count || 0);
    const pyramidLimit = Number(t.pyramid_limit || 0);
    const statusBits = [];
    if (heldEntries > 0) statusBits.push(`보유 ${heldEntries}회`);
    if (heldQty > 0) statusBits.push(`수량 ${heldQty}`);
    if (pyramidLimit > 0) statusBits.push(`최대 ${pyramidLimit}회`);
    if (pendingCount > 0) statusBits.push(`미체결 ${pendingCount}건`);
    const statusHtml = statusBits.length
      ? `<div style="font-size:10px;color:#94a3b8;margin-top:4px">${statusBits.join(' · ')}</div>`
      : '';
    const priceMetaBits = [];
    if (avgPrice > 0) priceMetaBits.push(`매수가 ${MARKET === 'KR' ? fmt.asset(avgPrice) : '$' + avgPrice.toFixed(2)}`);
    if (currentPrice > 0) priceMetaBits.push(`현재가 ${MARKET === 'KR' ? fmt.asset(currentPrice) : '$' + currentPrice.toFixed(2)}`);
    const priceMetaHtml = priceMetaBits.length
      ? `<div style="font-size:10px;color:#94a3b8;margin-top:4px">${priceMetaBits.join(' · ')}</div>`
      : '';
    const selectReasonHtml = t.select_reason
      ? `<div style="font-size:10px;color:#64748b;margin-top:5px;line-height:1.4;border-top:1px solid var(--border);padding-top:5px">${t.select_reason}</div>`
      : '';
    let pendingExplain = '';
    const reasons = t.skip_reasons || [];
    if (reasons.includes('이미 보유중') && reasons.includes('pending_order')) {
      pendingExplain = `추가 진입 보류: 이미 보유 중, 미체결 주문 ${pendingCount}건 존재`;
    } else if (reasons.includes('이미 보유중')) {
      pendingExplain = '추가 진입 보류: 이미 보유 중';
    } else if (reasons.includes('pending_order')) {
      pendingExplain = `추가 진입 보류: 미체결 주문 ${pendingCount}건 존재`;
    } else if (reasons.length > 0) {
      pendingExplain = reasons.join(' / ');
    }
    const skipHtml = pendingExplain
      ? `<div style="font-size:10px;color:#f87171;margin-top:4px">⚠ 미체결: ${t.skip_reasons.join(' / ')}</div>`
      : '';
    return `<div style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;min-width:160px;max-width:280px;cursor:default">
      <div style="font-family:var(--mono);font-weight:700;font-size:14px;color:#e2e8f0">${t.ticker}${sigBadge}</div>
      <div style="font-size:12px;color:var(--text-dim);margin-top:4px">${shownPriceStr}</div>
      <div style="margin-top:6px;font-size:12px;color:${ev.color}">${ev.icon} ${ev.label}</div>
      <div style="font-size:10px;color:#475569;margin-top:2px">${t.last_ts}${t.last_reason ? ' · ' + t.last_reason : ''}</div>
      ${statusHtml}
      ${priceMetaHtml}
      ${skipHtmlFinal}
      ${selectReasonHtml}
    </div>`;
  }).join('');

  // 선택 안 된 후보 목록 (접힌 형태)
  const notSel = d.not_selected || [];
  if (notSel.length > 0) {
    const notSelDiv = document.createElement('div');
    notSelDiv.style.cssText = 'width:100%;margin-top:8px;font-size:11px;color:#475569;font-family:var(--mono)';
    notSelDiv.innerHTML = `<span style="color:#374151">제외된 후보:</span> ${notSel.join(', ')}`;
    board.appendChild(notSelDiv);
  }
}

async function loadSignalFeed() {
  const feed = document.getElementById('signal-feed');
  const cards = document.getElementById('signal-feed-cards');
  if (!feed) return;
  const [d, tickerData] = await Promise.all([
    fetch('/api/signals/recent?market=' + MARKET + '&n=60').then(r => r.json()).catch(() => ([])),
    fetch('/api/tickers/today?market=' + MARKET).then(r => r.json()).catch(() => ({})),
  ]);
  const summary = window.__todaySummary || {};
  const tickerMap = {};
  const tickerList = tickerData.tickers || [];
  tickerList.forEach(t => { tickerMap[t.ticker] = t; });
  const EVENT_KO = {
    entry_signal: '진입 신호',
    entry_skip: '진입 보류',
    signal_check: '신호 없음',
    signal_blocked: '신호 차단',
    buy_filled: '매수 체결',
    sell_filled: '매도 체결',
    trailing: '추적중',
    waiting: '대기',
    cycle_error: '처리 오류'
  };
  const REASON_KO = {
    already_holding: '이미 보유중',
    pending_order: '미체결 주문',
    invalid_price: '가격 오류',
    none: '신호 없음',
    trailing: '트레일링',
    trail_stop: '추적 손절',
    stop_loss: '손절',
    max_hold: '보유 기간 만료',
    session_close: '장마감 정산',
    live_position: '브로커 잔고 반영',
    us_order_blocked: '미국 주문 차단'
  };
  const koReason = (v) => REASON_KO[v] || v || '-';
  const koEvent = (v) => EVENT_KO[v] || v || '-';
  const pinnedTickers = tickerList.filter(t => Number(t.held_entries || 0) > 0 || Number(t.pending_count || 0) > 0);
  if (cards) {
    if (!pinnedTickers.length) {
      cards.innerHTML = '';
    } else {
      cards.innerHTML = pinnedTickers.map(t => {
        const price = Number(t.display_price || t.current_price || t.avg_price || t.last_price || 0);
        const priceStr = price > 0 ? (MARKET === 'KR' ? Math.round(price).toLocaleString() + '원' : '$' + price.toFixed(2)) : '--';
        const heldEntries = Number(t.held_entries || 0);
        const heldQty = Number(t.held_qty || 0);
        const pendingCount = Number(t.pending_count || 0);
        const label = heldEntries > 0 ? '보유중' : '미체결';
        const statusBits = [];
        if (heldEntries > 0) statusBits.push(`보유 ${heldEntries}회`);
        if (heldQty > 0) statusBits.push(`수량 ${heldQty}`);
        if (pendingCount > 0) statusBits.push(`미체결 ${pendingCount}건`);
        return `<div style="background:var(--surface2);border:1px solid rgba(59,130,246,0.35);border-radius:8px;padding:12px 14px;min-width:170px;max-width:260px;flex:1">
          <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
            <div style="font-family:var(--mono);font-weight:700;font-size:14px;color:#e2e8f0">${t.ticker}</div>
            <div style="font-size:11px;color:#93c5fd">${label}</div>
          </div>
          <div style="font-size:12px;color:var(--text-dim);margin-top:4px">${priceStr}</div>
          <div style="font-size:10px;color:#94a3b8;margin-top:6px">${statusBits.join(' · ') || '상태 없음'}</div>
          ${(t.avg_price || t.current_price) ? `<div style="font-size:10px;color:#64748b;margin-top:6px">매수가 ${t.avg_price ? (MARKET === 'KR' ? Math.round(Number(t.avg_price)).toLocaleString() + '원' : '$' + Number(t.avg_price).toFixed(2)) : '--'} · 현재가 ${t.current_price ? (MARKET === 'KR' ? Math.round(Number(t.current_price)).toLocaleString() + '원' : '$' + Number(t.current_price).toFixed(2)) : '--'}</div>` : ''}
        </div>`;
      }).join('');
    }
  }
  if (!Array.isArray(d) || d.length === 0) {
    if (!tickerList.length) {
      feed.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-dim)">신호 없음 · 선택 종목과 보유 포지션이 아직 없습니다</div>';
      return;
    }
    const fallbackRows = tickerList.map(t => {
      const price = Number(t.display_price || t.current_price || t.avg_price || t.last_price || 0);
      const priceStr = price > 0 ? (MARKET === 'KR' ? Math.round(price).toLocaleString() + '원' : '$' + price.toFixed(2)) : '';
      const heldEntries = Number(t.held_entries || 0);
      const heldQty = Number(t.held_qty || 0);
      const pendingCount = Number(t.pending_count || 0);
      let detail = '신호 없음';
      if (heldEntries > 0 && pendingCount > 0) detail = `보유 ${heldEntries}회 · 수량 ${heldQty} · 미체결 ${pendingCount}건`;
      else if (heldEntries > 0) detail = `보유 ${heldEntries}회 · 수량 ${heldQty} · 최대 ${t.pyramid_limit || 8}회`;
      else if (pendingCount > 0) detail = `미체결 ${pendingCount}건 대기중`;
      return `<div style="display:flex;align-items:center;gap:10px;padding:6px 16px;background:transparent;border-bottom:1px solid rgba(31,41,55,0.4)">
        <span style="color:var(--text-dim);min-width:56px">${t.last_ts || '--:--'}</span>
        <span>⬜</span>
        <span style="min-width:36px;color:#94a3b8">${MARKET}</span>
        <span style="min-width:80px;color:#cbd5e1;font-weight:600">${t.ticker || ''}</span>
        <span style="min-width:72px;color:var(--text-dim)">${priceStr}</span>
        <span style="color:var(--text-dim)">${detail}</span>
      </div>`;
    }).join('');
    const header = `<div style="padding:10px 16px;color:var(--text-dim);border-bottom:1px solid rgba(31,41,55,0.5)">신호 없음 · 보유 ${summary.position_count || 0}개 · 미체결 ${summary.pending_count || 0}건</div>`;
    feed.innerHTML = header + fallbackRows;
    return;
  }
  const pinnedRows = pinnedTickers.map(t => {
    const price = Number(t.display_price || t.current_price || t.avg_price || t.last_price || 0);
    const priceStr = price > 0 ? (MARKET === 'KR' ? Math.round(price).toLocaleString() + '원' : '$' + price.toFixed(2)) : '';
    const heldEntries = Number(t.held_entries || 0);
    const heldQty = Number(t.held_qty || 0);
    const pendingCount = Number(t.pending_count || 0);
    const parts = [];
    if (heldEntries > 0) parts.push(`보유 ${heldEntries}회 · 수량 ${heldQty}`);
    if (pendingCount > 0) parts.push(`미체결 ${pendingCount}건`);
    if (!parts.length) parts.push('상태 없음');
    return `<div style="display:flex;align-items:center;gap:10px;padding:6px 16px;background:rgba(59,130,246,0.08);border-bottom:1px solid rgba(31,41,55,0.4)">
      <span style="color:var(--text-dim);min-width:56px">${t.last_ts || '--:--'}</span>
      <span>📌</span>
      <span style="min-width:36px;color:#94a3b8">${MARKET}</span>
      <span style="min-width:80px;color:#93c5fd;font-weight:700">${t.ticker || ''}</span>
      <span style="min-width:72px;color:var(--text-dim)">${priceStr}</span>
      <span style="color:var(--text-dim)">${parts.join(' · ')}</span>
    </div>`;
  }).join('');
  const rows = d.map(ev => {
    const event  = ev.event  || '';
    const reason = ev.reason || '';
    const ticker = ev.ticker || '';
    const market = ev.market || '';
    const tickerInfo = tickerMap[ticker] || {};
    const price  = Number(tickerInfo.display_price || tickerInfo.current_price || tickerInfo.avg_price || ev.price || 0);
    const mode   = ev.mode   || '';
    const ts     = ev.timestamp ? ev.timestamp.substring(11,19) : '';

    let icon = '⬜', bg = 'transparent', textColor = '#64748b';
    if (event === 'entry_signal') {
      icon = '🟢'; bg = 'rgba(16,185,129,0.08)'; textColor = '#10b981';
    } else if (event === 'entry_skip') {
      if (reason === 'invalid_price') { icon = '🔴'; bg = 'rgba(239,68,68,0.08)'; textColor = '#ef4444'; }
      else if (reason === 'already_holding') { icon = '🔵'; bg = 'rgba(59,130,246,0.08)'; textColor = '#3b82f6'; }
      else { icon = '🟠'; bg = 'rgba(245,158,11,0.08)'; textColor = '#f59e0b'; }
    } else if (event === 'signal_blocked') {
      icon = '🚫'; bg = 'rgba(245,158,11,0.08)'; textColor = '#f59e0b';
    }

    const priceStr = price > 0 ? (market === 'KR' ? Math.round(price).toLocaleString() + '원' : '$' + price.toFixed(2)) : '';
    let detail   = reason ? koReason(reason) : (mode ? koMode(mode) : '');
    if (reason === 'already_holding') {
      detail = `보유 ${tickerInfo.held_entries ?? 0}회 · 최대 ${tickerInfo.pyramid_limit ?? summary.pyramid_limit ?? 8}회 · 미체결 ${tickerInfo.pending_count ?? 0}건`;
    } else if (reason === 'pending_order') {
      detail = `미체결 ${tickerInfo.pending_count ?? 0}건 · 보유 ${tickerInfo.held_entries ?? 0}회 / 최대 ${tickerInfo.pyramid_limit ?? summary.pyramid_limit ?? 8}회`;
    }

    return `<div style="display:flex;align-items:center;gap:10px;padding:6px 16px;background:${bg};border-bottom:1px solid rgba(31,41,55,0.4)">
      <span style="color:var(--text-dim);min-width:56px">${ts}</span>
      <span>${icon}</span>
      <span style="min-width:36px;color:#94a3b8">${market}</span>
      <span style="min-width:96px;color:${textColor};font-weight:600">${ticker} · ${koEvent(event)}</span>
      <span style="min-width:72px;color:var(--text-dim)">${priceStr}</span>
      <span style="color:var(--text-dim)">${detail}</span>
    </div>`;
  }).join('');
  const pinnedHeader = pinnedRows
    ? `<div style="padding:10px 16px;color:#93c5fd;border-bottom:1px solid rgba(31,41,55,0.5)">현재 포지션 / 미체결 상태</div>${pinnedRows}<div style="padding:10px 16px;color:var(--text-dim);border-bottom:1px solid rgba(31,41,55,0.5)">최근 이벤트</div>`
    : '';
  feed.innerHTML = pinnedHeader + rows;
  document.getElementById('signal-ts').textContent =
    `실시간 신호 · 보유 ${summary.position_count || 0}개 · 미체결 ${summary.pending_count || 0}건 · 갱신: ${new Date().toLocaleTimeString()}`;
}

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
    `모드: <span class="mode-badge mode-${t.mode}">${koMode(t.mode)}</span>&nbsp; 거래 ${t.trades}건`;

  const wrEl = document.getElementById('win-rate');
  wrEl.textContent = p.win_rate + '%';
  wrEl.className = 'card-value ' + (p.win_rate >= 55 ? 'up' : p.win_rate >= 45 ? 'neutral-color' : 'down');
  document.getElementById('win-detail').textContent = `${p.wins}승 / ${p.losses}패 (${p.days}일)`;

  const emoji = p.streak_type === 'win' ? '🔥' : '🧊';
  document.getElementById('streak-val').innerHTML =
    `<span style="font-size:20px">${emoji}</span> ${p.streak}연속`;
  document.getElementById('total-pnl').textContent = `누적: ${fmt.pct(p.total_pnl)}`;

  const positions = t.positions || [];
  const posBoard = document.getElementById('position-board');
  const posCount = document.getElementById('position-count');
  if (posCount) {
    posCount.textContent = positions.length
      ? `${positions.length}/${t.position_limit || 0}개 보유중 · 남은 슬롯 ${t.position_remaining || 0}${t.live_updated ? ' · ' + t.live_updated : ''}`
      : `보유 없음 · 남은 슬롯 ${t.position_remaining || 0}${t.live_updated ? ' · ' + t.live_updated : ''}`;
  }
  if (!posBoard) return;
  if (positions.length === 0) {
    posBoard.innerHTML = '<div style="color:var(--text-dim);font-size:13px;padding:8px 0">현재 보유 포지션이 없습니다</div>';
    return;
  }

  posBoard.innerHTML = positions.map(pos => {
    const avgPrice = Number(pos.avg_price || 0);
    const curPrice = Number(pos.current_price || 0);
    const pnl = avgPrice > 0 ? ((curPrice / avgPrice) - 1) * 100 : Number(pos.pnl_pct || 0);
    const pnlColor = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text-dim)';
    const entry = MARKET === 'KR' ? fmt.krw(avgPrice) : '$' + avgPrice.toFixed(2);
    const cur = MARKET === 'KR' ? fmt.krw(curPrice) : '$' + curPrice.toFixed(2);
    return `
    <div class="card" style="min-width:180px;flex:1;padding:14px 16px">
      <div style="font-size:15px;font-weight:700;margin-bottom:6px">${pos.ticker}</div>
      <div style="font-size:11px;color:var(--text-dim);margin-bottom:8px">${pos.strategy || '-'} · ${pos.qty}주</div>
      <div style="font-size:11px;color:var(--text-dim)">매수가</div>
      <div style="font-family:var(--mono);font-size:13px;margin-bottom:4px">${entry}</div>
      <div style="font-size:11px;color:var(--text-dim)">현재가</div>
      <div style="font-family:var(--mono);font-size:13px;margin-bottom:6px">${cur}</div>
      <div style="font-size:18px;font-weight:700;color:${pnlColor}">${fmt.pct(pnl)}</div>
    </div>`;
  }).join('');

  const pendingOrders = t.pending_orders || [];
  const pendingBoard = document.getElementById('pending-board');
  const pendingCount = document.getElementById('pending-count');
  if (pendingCount) {
    pendingCount.textContent = pendingOrders.length
      ? `${pendingOrders.length}건 대기중`
      : '대기 주문 없음';
  }
  if (pendingBoard) {
    if (pendingOrders.length === 0) {
      pendingBoard.innerHTML = '<div style="color:var(--text-dim);font-size:13px;padding:8px 0">현재 미체결 주문이 없습니다</div>';
    } else {
      pendingBoard.innerHTML = pendingOrders.map(order => {
        const rawPrice = Number(order.raw_price || 0);
        const orderPrice = MARKET === 'KR' ? fmt.krw(rawPrice) : '$' + rawPrice.toFixed(4);
        const createdAt = order.created_at ? order.created_at.substring(11, 19) : '--:--:--';
        return `
        <div class="card" style="min-width:180px;flex:1;padding:14px 16px;border-style:dashed">
          <div style="font-size:15px;font-weight:700;margin-bottom:6px">${order.ticker}</div>
          <div style="font-size:11px;color:var(--text-dim);margin-bottom:8px">${order.strategy || '-'} · ${order.qty}주</div>
          <div style="font-size:11px;color:var(--text-dim)">주문가</div>
          <div style="font-family:var(--mono);font-size:13px;margin-bottom:4px">${orderPrice}</div>
          <div style="font-size:11px;color:var(--text-dim)">주문번호</div>
          <div style="font-family:var(--mono);font-size:12px;margin-bottom:6px">${order.order_no || '-'}</div>
          <div style="font-size:11px;color:#f59e0b">체결 대기 · ${createdAt}</div>
        </div>`;
      }).join('');
    }
  }
}

async function loadAll() {
  await Promise.all([loadSummary(), loadJudgments(), loadEquityChart(), loadMarketContext(), loadCredits()]);
  loadMonitorTickers();
  loadSignalFeed();
}

loadAll();
setInterval(loadMonitorTickers, 15000);
setInterval(loadSignalFeed, 10000);
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
  <div id="h-empty-note" style="font-size:12px;color:var(--muted);margin-top:10px"></div>
</div>

</main>

<script>

async function loadPeriodStats() {
  const d = await fetch('/api/stats/period' + marketParam()).then(r => r.json()).catch(() => ({}));

  const cardLabels = document.querySelectorAll('#stat-cards .card-label');
  if (cardLabels.length >= 4) {
    cardLabels[0].textContent = '청산 승률';
    cardLabels[1].textContent = '청산 손익 합계';
    cardLabels[2].textContent = '평균 청산 손익률';
    cardLabels[3].textContent = '청산 거래 수';
  }
  const cardSubs = document.querySelectorAll('#stat-cards .card-sub');
  if (cardSubs.length >= 4) {
    cardSubs[1].textContent = `${d.wins || 0}승 / ${d.losses || 0}패 · 매도 완료 기준`;
    cardSubs[2].textContent = `${d.days || 0} 거래일 · 청산 기준`;
    cardSubs[3].textContent = '매도 완료 1건당 평균';
    cardSubs[4] && (cardSubs[4].textContent = '매도 완료 건수');
  }

  const wrEl = document.getElementById('h-win-rate');
  wrEl.textContent = (d.win_rate || 0) + '%';
  wrEl.className   = 'card-value ' + colorClass(d.win_rate - 50);
  document.getElementById('h-win-detail').textContent = `${d.wins || 0}승 / ${d.losses || 0}패 · 매도 완료 기준`;

  const tpEl = document.getElementById('h-total-pnl');
  tpEl.textContent = fmt.pct(d.total_pnl || 0);
  tpEl.className   = 'card-value ' + colorClass(d.total_pnl || 0);
  document.getElementById('h-days').textContent = `${d.days || 0} 거래일 · 청산 기준`;

  const apEl = document.getElementById('h-avg-pnl');
  apEl.textContent = fmt.pct(d.avg_pnl || 0);
  apEl.className   = 'card-value ' + colorClass(d.avg_pnl || 0);

  document.getElementById('h-trades').textContent = fmt.num(d.trades || 0);
  const note = document.getElementById('h-empty-note');
  if (note) {
    note.textContent = (d.trades || 0) === 0
      ? `현재 선택 시장(${MARKET}) 기준 매도 완료 거래가 없습니다`
      : '기준: 매도 완료 거래만 집계';
  }
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

  const historyTitles = document.querySelectorAll('/**/'.replace('/**/','main .grid-2 .section-title, main .card .section-title'));
  if (historyTitles.length >= 3) {
    historyTitles[1].textContent = '월별 청산 손익';
    historyTitles[2].textContent = '월별 청산 성과 요약';
  }

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
          <th style="width:90px">구분</th>
          <th style="width:90px">종목</th>
          <th style="width:110px">전략</th>
          <th style="width:220px">가격</th>
          <th style="width:80px">수량</th>
          <th style="width:90px">손익%</th>
          <th style="width:110px">손익(원)</th>
          <th style="min-width:320px">사유</th>
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
    tbody.innerHTML = `<tr><td colspan="8" style="text-align:center;color:var(--muted);padding:24px">선택 시장(${MARKET}) 기준 거래 내역이 없습니다</td></tr>`;
    return;
  }

  let html = '';
  let lastDate = null;

  const reasonText = (t) => {
    const reason = String(t.reason || '').trim();
    const strategy = String(t.strategy || '').trim();
    const isSell = (t.side === 'sell' || t.side === '매도');
    const map = {
      paper_buy: '주문 접수 기준 매수',
      pending_order: '미체결 주문 대기',
      live_position: '브로커 잔고 반영',
      broker_sync: '브로커 동기화 반영',
      trail_stop: '트레일링 기준 청산',
      max_hold: '보유 기간 만료 청산',
      stop_loss: '손절 기준 청산',
      take_profit: '익절 기준 청산',
      paper_sell: '주문 접수 기준 매도',
    };
    if (map[reason]) return map[reason];
    if (!isSell && strategy && strategy !== 'broker_sync') return `분석가 선택 + ${koStrategy(strategy)} 로직 진입`;
    if (isSell && strategy && strategy !== 'broker_sync') return `${koStrategy(strategy)} 로직 청산`;
    if (strategy === 'broker_sync') return '브로커 동기화 반영';
    return reason || '-';
  };

  const sourceText = (t) => {
    const ps = String(t.price_source || '').trim();
    if (ps === 'order_fill') return '체결 조회 기준';
    if (ps === 'broker_balance') return '브로커 잔고 기준';
    if (ps === 'pending_order') return '미체결 주문 기준';
    if (ps === 'system_log') return '시스템 로그 복구';
    return '';
  };

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
    const timeLbl = t.time || t.fill_time || '--:--';
    const pnlPct  = isSell ? `<span class="${colorClass(t.pnl_pct)}">${fmt.pct(t.pnl_pct)}</span>` : '<span style="color:var(--muted)">-</span>';
    const pnlKrw  = isSell ? `<span class="${colorClass(t.pnl)}">${fmt.krw(t.pnl)}</span>`      : '<span style="color:var(--muted)">-</span>';
    const priceVal = Number(t.display_price || t.price || 0);
    const currency = t.currency || (MARKET === 'US' ? 'USD' : 'KRW');
    const price   = priceVal > 0
      ? (currency === 'USD' ? '$' + priceVal.toFixed(4) : Math.round(priceVal).toLocaleString() + '원')
      : '-';
    const qty     = t.qty   ? Number(t.qty).toLocaleString()   : '-';
    const tradeTotal = Number(t.trade_total_native || 0);
    const buyPrice = Number(t.buy_price_native || 0);
    const buyTotal = Number(t.buy_total_native || 0);
    const sellTotal = Number(t.sell_total_native || 0);
    const pnlKrwValue = Number(t.pnl_krw ?? t.pnl ?? 0);
    const fmtNative = (v) => {
      if (!(v > 0)) return '-';
      return currency === 'USD' ? ('$' + v.toFixed(2)) : (Math.round(v).toLocaleString() + '원');
    };
    const metaBits = [];
    if (t.price_source === 'broker_balance') metaBits.push('브로커평균단가');
    if (t.price_source === 'order_fill') metaBits.push('체결조회단가');
    if (t.order_no) metaBits.push('주문번호 ' + t.order_no);
    if (t.fill_time) metaBits.push('체결시각 ' + t.fill_time);
    if (isSell) {
      if (buyPrice > 0) metaBits.push('매수가 ' + (currency === 'USD' ? ('$' + buyPrice.toFixed(4)) : (Math.round(buyPrice).toLocaleString() + '원')));
      if (buyTotal > 0) metaBits.push('매수총액 ' + fmtNative(buyTotal));
      if (sellTotal > 0) metaBits.push('매도총액 ' + fmtNative(sellTotal));
      if (Math.abs(pnlKrwValue) > 0) metaBits.push('원화손익 ' + fmt.krw(pnlKrwValue));
    } else if (tradeTotal > 0) {
      metaBits.push('매수총액 ' + fmtNative(tradeTotal));
    }
    let metaLine = '';
    let sourceBadge = '';
    if (metaBits.length) {
      const firstLine = metaBits.slice(0, 2).join(' · ');
      const secondLine = metaBits.slice(2).join(' · ');
      metaLine = `
        <div style="font-size:10px;color:var(--muted);margin-top:4px;line-height:1.45">
          <div>${firstLine}</div>
          ${secondLine ? `<div style="margin-top:2px">${secondLine}</div>` : ''}
        </div>`;
    }
    const reasonLine = [reasonText(t), sourceText(t)].filter(Boolean).join(' · ');

    if (t.source_kind_label) {
      sourceBadge = `<div style="display:inline-block;margin-top:4px;padding:1px 6px;border-radius:999px;background:rgba(148,163,184,0.16);color:#cbd5e1;font-size:10px">${t.source_kind_label}</div>`;
    }

    html += `
    <tr>
      <td class="${sideCls}" style="font-weight:600;white-space:nowrap">${sideLbl}<div style="font-size:10px;color:var(--muted);margin-top:4px">${timeLbl}</div></td>
      <td style="font-weight:600">${t.ticker || '-'}</td>
      <td><span style="color:var(--blue)">${t.strategy || '-'}</span></td>
      <td style="line-height:1.45">
        <div>${price}</div>
        ${metaLine}
      </td>
      <td>${qty}</td>
      <td>${pnlPct}</td>
      <td>${pnlKrw}</td>
      <td style="color:var(--muted);min-width:320px;max-width:520px;white-space:normal;word-break:keep-all;line-height:1.5"
          title="${reasonLine.replace(/"/g,'&quot;')}">${reasonLine || '-'}${sourceBadge}</td>
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
    <option value="momentum">모멘텀</option>
    <option value="mean_reversion">평균회귀</option>
    <option value="gap_pullback">갭눌림</option>
    <option value="volatility_breakout">변동성돌파</option>
  </select>
  <select class="date-input" id="f-side">
    <option value="">구분 전체</option>
    <option value="buy">매수</option>
    <option value="sell">매도</option>
  </select>
  <button class="apply-btn" onclick="loadAll()">적용</button>
"""


# ── 페이지 4: 분석 ────────────────────────────────────────────────────────────

TODAY_SUMMARY_OVERRIDE_JS = """
<script>
async function loadSummary() {
  const d = await fetch('/api/summary?market=' + MARKET).then(r => r.json()).catch(() => ({}));
  if (!d.today) return;
  const t = d.today, p = d.period || {};
  window.__todaySummary = t;

  const pnlEl = document.getElementById('today-pnl');
  if (pnlEl) {
    pnlEl.textContent = fmt.pct(t.pnl_pct || 0);
    pnlEl.className = 'card-value ' + colorClass(t.pnl_pct || 0);
  }
  const todayKrw = document.getElementById('today-krw');
  if (todayKrw) {
    todayKrw.textContent = `실현 ${fmt.krw(t.realized_pnl_krw || 0)} | 미실현 ${fmt.krw(t.unrealized_pnl_krw || 0)}`;
  }
  const cumulative = document.getElementById('cumulative');
  if (cumulative) cumulative.textContent = fmt.asset(t.cumulative || 0);
  const cumulativePnl = document.getElementById('cumulative-pnl');
  if (cumulativePnl) cumulativePnl.textContent = `누적 손익: ${fmt.pct(p.total_pnl || 0)}`;

  const sess = t.session || {};
  const sessTxt = sess.label ? ` · ${sess.label} (${sess.open_time || '--:--'}~${sess.close_time || '--:--'})` : '';
  const todayMode = document.getElementById('today-mode');
  if (todayMode) {
    todayMode.innerHTML = `모드: <span class="mode-badge mode-${t.mode}">${koMode(t.mode)}</span>&nbsp; 거래 ${t.trades || 0}건${sessTxt}`;
  }

  const wrEl = document.getElementById('win-rate');
  if (wrEl) {
    wrEl.textContent = (p.win_rate || 0) + '%';
    wrEl.className = 'card-value ' + ((p.win_rate || 0) >= 55 ? 'up' : (p.win_rate || 0) >= 45 ? 'neutral-color' : 'down');
  }
  const winDetail = document.getElementById('win-detail');
  if (winDetail) winDetail.textContent = `${p.wins || 0}승 / ${p.losses || 0}패 (${p.days || 0}일)`;

  const emoji = p.streak_type === 'win' ? '▲' : '▼';
  const streakVal = document.getElementById('streak-val');
  if (streakVal) streakVal.innerHTML = `<span style="font-size:20px">${emoji}</span> ${p.streak || 0}연속`;
  const totalPnl = document.getElementById('total-pnl');
  if (totalPnl) totalPnl.textContent = `누적: ${fmt.pct(p.total_pnl || 0)}`;

  const positions = t.positions || [];
  const posBoard = document.getElementById('position-board');
  const posCount = document.getElementById('position-count');
  if (posCount) {
    posCount.textContent = positions.length
      ? `${positions.length}개 보유중${t.live_updated ? ' · ' + t.live_updated : ''}`
      : `보유 없음${t.live_updated ? ' · ' + t.live_updated : ''}`;
  }
  if (!posBoard) return;
  if (positions.length === 0) {
    posBoard.innerHTML = '<div style="color:var(--text-dim);font-size:13px;padding:8px 0">현재 보유 포지션이 없습니다</div>';
    return;
  }
  posBoard.innerHTML = positions.map(pos => {
    const pnl = pos.pnl_pct || 0;
    const pnlColor = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text-dim)';
    const isKRW = (pos.currency || (MARKET === 'KR' ? 'KRW' : 'USD')) === 'KRW';
    const qty = Number(pos.qty || 0);
    const avg = Number(pos.avg_price || 0);
    const curPx = Number(pos.current_price || 0);
    const usdKrw = Number(t.usd_krw || 0);
    const buyTotal = avg * qty;
    const curTotal = curPx * qty;
    const pnlNative = curTotal - buyTotal;
    const pnlKrw = !isKRW && usdKrw > 0 ? pnlNative * usdKrw : pnlNative;
    const entry = isKRW ? fmt.krw(pos.avg_price || 0) : '$' + (pos.avg_price || 0).toFixed(2);
    const cur = isKRW ? fmt.krw(pos.current_price || 0) : '$' + (pos.current_price || 0).toFixed(2);
    const buyTotalText = isKRW ? fmt.asset(buyTotal) : '$' + buyTotal.toFixed(2);
    const curTotalText = isKRW ? fmt.asset(curTotal) : '$' + curTotal.toFixed(2);
    const pnlKrwText = fmt.krw(pnlKrw);
    return `
      <div class="card" style="min-width:180px;flex:1;padding:14px 16px">
        <div style="font-size:15px;font-weight:700;margin-bottom:6px">${pos.ticker}</div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:8px">${pos.strategy || '-'} · ${pos.qty || 0}주</div>
        <div style="font-size:11px;color:var(--text-dim)">매수가</div>
        <div style="font-family:var(--mono);font-size:13px;margin-bottom:4px">${entry}</div>
        <div style="font-size:11px;color:var(--text-dim)">현재가</div>
        <div style="font-family:var(--mono);font-size:13px;margin-bottom:6px">${cur}</div>
        <div style="font-size:18px;font-weight:700;color:${pnlColor}">${fmt.pct(pnl)}</div>
      </div>`;
  }).join('');
}
</script>
"""

TODAY_SUMMARY_OVERRIDE_JS_V2 = """
<script>
async function loadSummary() {
  const d = await fetch('/api/summary?market=' + MARKET).then(r => r.json()).catch(() => ({}));
  if (!d.today) return;
  const t = d.today;
  const p = d.period || {};
  window.__todaySummary = t;

  const pnlEl = document.getElementById('today-pnl');
  if (pnlEl) {
    pnlEl.textContent = fmt.pct(t.pnl_pct || 0);
    pnlEl.className = 'card-value ' + colorClass(t.pnl_pct || 0);
  }
  const todayKrw = document.getElementById('today-krw');
  if (todayKrw) todayKrw.textContent = fmt.krw(t.pnl_krw || 0);
  const cumulative = document.getElementById('cumulative');
  if (cumulative) cumulative.textContent = fmt.asset(t.cumulative || 0);
  const cumulativePnl = document.getElementById('cumulative-pnl');
  if (cumulativePnl) cumulativePnl.textContent = `누적 손익: ${fmt.pct(p.total_pnl || 0)}`;

  const sess = t.session || {};
  const sessTxt = sess.label ? ` | ${sess.label} (${sess.open_time || '--:--'}~${sess.close_time || '--:--'})` : '';
  const todayMode = document.getElementById('today-mode');
  if (todayMode) {
    todayMode.innerHTML = `모드: <span class="mode-badge mode-${t.mode}">${koMode(t.mode)}</span> 거래 ${t.trades || 0}건${sessTxt}`;
  }

  const wrEl = document.getElementById('win-rate');
  if (wrEl) {
    wrEl.textContent = (p.win_rate || 0) + '%';
    wrEl.className = 'card-value ' + ((p.win_rate || 0) >= 55 ? 'up' : (p.win_rate || 0) >= 45 ? 'neutral-color' : 'down');
  }
  const winDetail = document.getElementById('win-detail');
  if (winDetail) winDetail.textContent = `${p.wins || 0}승 / ${p.losses || 0}패 (${p.days || 0}일)`;

  const streakVal = document.getElementById('streak-val');
  if (streakVal) streakVal.textContent = `${p.streak || 0}연속`;
  const totalPnl = document.getElementById('total-pnl');
  if (totalPnl) totalPnl.textContent = `누적: ${fmt.pct(p.total_pnl || 0)}`;

  const positions = t.positions || [];
  const posBoard = document.getElementById('position-board');
  const posCount = document.getElementById('position-count');
  if (posCount) {
    posCount.textContent = positions.length
      ? `${positions.length}개 보유중${t.live_updated ? ' | ' + t.live_updated : ''}`
      : `보유 없음${t.live_updated ? ' | ' + t.live_updated : ''}`;
  }
  if (!posBoard) return;
  if (positions.length === 0) {
    posBoard.innerHTML = '<div style="color:var(--text-dim);font-size:13px;padding:8px 0">현재 보유 포지션이 없습니다</div>';
    return;
  }
  posBoard.innerHTML = positions.map(pos => {
    const pnl = pos.pnl_pct || 0;
    const pnlColor = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text-dim)';
    const isKRW = (pos.currency || (MARKET === 'KR' ? 'KRW' : 'USD')) === 'KRW';
    const qty = Number(pos.qty || 0);
    const avg = Number(pos.avg_price || 0);
    const curPx = Number(pos.current_price || 0);
    const usdKrw = Number(t.usd_krw || 0);
    const buyTotal = avg * qty;
    const curTotal = curPx * qty;
    const pnlNative = curTotal - buyTotal;
    const pnlKrw = !isKRW && usdKrw > 0 ? pnlNative * usdKrw : pnlNative;
    const entry = isKRW ? fmt.krw(pos.avg_price || 0) : '$' + (pos.avg_price || 0).toFixed(2);
    const cur = isKRW ? fmt.krw(pos.current_price || 0) : '$' + (pos.current_price || 0).toFixed(2);
    const buyTotalText = isKRW ? fmt.krw(buyTotal) : '$' + buyTotal.toFixed(2);
    const curTotalText = isKRW ? fmt.krw(curTotal) : '$' + curTotal.toFixed(2);
    const pnlKrwText = fmt.krw(pnlKrw);
    return `
      <div class="card" style="min-width:180px;flex:1;padding:14px 16px">
        <div style="font-size:15px;font-weight:700;margin-bottom:6px">${pos.ticker}</div>
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:8px">${pos.strategy || '-'} | ${pos.qty || 0}주</div>
        <div style="font-size:11px;color:var(--text-dim)">매수가</div>
        <div style="font-family:var(--mono);font-size:13px;margin-bottom:4px">${entry}</div>
        <div style="font-size:11px;color:var(--text-dim)">현재가</div>
        <div style="font-family:var(--mono);font-size:13px;margin-bottom:6px">${cur}</div>
        <div style="font-size:10px;color:var(--text-dim);margin-bottom:4px">매수총액 ${buyTotalText}</div>
        <div style="font-size:10px;color:var(--text-dim);margin-bottom:6px">현재평가 ${curTotalText}${!isKRW && usdKrw > 0 ? ' · 원화손익 ' + pnlKrwText : ''}</div>
        <div style="font-size:18px;font-weight:700;color:${pnlColor}">${fmt.pct(pnl)}</div>
      </div>`;
  }).join('');
}

if (typeof loadSummary === 'function') {
  loadSummary();
}
</script>
"""

CLAUDE_CONTROL_JS = """
<script>
async function loadClaudeControl() {
  const d = await fetch('/api/claude/status?market=' + MARKET).then(r => r.json()).catch(() => ({}));
  const badge = document.getElementById('claude-status-badge');
  const stateLine = document.getElementById('claude-state-line');
  const metaLine = document.getElementById('claude-meta-line');
  const errLine = document.getElementById('claude-error-line');
  const toggleBtn = document.getElementById('claude-toggle-btn');
  const triggerBtn = document.getElementById('claude-trigger-btn');
  if (!badge || !stateLine || !metaLine || !errLine || !toggleBtn || !triggerBtn) return;

  const enabled = !!d.enabled;
  const sess = d.session || {};
  const pending = d.pending_trigger || null;
  const status = d.last_result_status || 'idle';
  const stats = d.stats || {};
  badge.textContent = enabled ? 'ON' : 'OFF';
  badge.style.color = enabled ? 'var(--green)' : 'var(--red)';
  stateLine.textContent = enabled
    ? `활성 상태${sess.label ? ' | ' + sess.label : ''}`
    : '비활성 상태';
  metaLine.textContent = [
    d.last_trigger_at ? `마지막 트리거 ${d.last_trigger_at.slice(11, 19)}` : '',
    d.last_result_at ? `마지막 결과 ${status}` : '',
    pending ? `대기 ${pending.market} ${String(pending.requested_at || '').slice(11, 19)}` : '',
    stats.count ? `오늘 승률 ${stats.win_rate}% (${stats.wins}/${stats.count})` : ''
  ].filter(Boolean).join(' | ') || '--';
  const statsLine = stats.count
    ? `오늘 재판단 ${stats.count}회 · 모드변경 ${stats.changed}회 · 유지 ${stats.unchanged}회 · HIT ${stats.wins} / MISS ${stats.losses} / FLAT ${stats.flats} · 누적 ${fmt.krw(stats.pnl_krw || 0)}`
    : '';
  errLine.textContent = [statsLine, d.last_error || ''].filter(Boolean).join(' | ');

  toggleBtn.textContent = enabled ? 'OFF로 전환' : 'ON으로 전환';
  toggleBtn.style.background = enabled ? '#7f1d1d' : '#065f46';
  triggerBtn.disabled = !enabled || !sess.active;
  triggerBtn.style.opacity = triggerBtn.disabled ? '0.5' : '1';
}

async function loadProcessControl() {
  const d = await fetch('/api/control/status').then(r => r.json()).catch(() => ({}));
  const stateLine = document.getElementById('control-state-line');
  const metaLine = document.getElementById('control-meta-line');
  const errLine = document.getElementById('control-error-line');
  const dashBtn = document.getElementById('restart-dashboard-btn');
  const botBtn = document.getElementById('restart-bot-btn');
  if (!stateLine || !metaLine || !errLine || !dashBtn || !botBtn) return;

  const dash = d.dashboard || {};
  const bot = d.bot || {};
  stateLine.textContent = `대시보드 ${dash.alive ? '실행중' : '중지'} | 봇 ${bot.alive ? '실행중' : '중지'}`;
  metaLine.textContent = [
    dash.pid ? `대시보드 PID ${dash.pid}` : '',
    bot.pid ? `봇 PID ${bot.pid}` : '봇 PID 없음',
  ].filter(Boolean).join(' | ') || '--';
  errLine.textContent = bot.alive ? '' : '봇 PID 파일이 없으면 재시작 버튼이 동작하지 않을 수 있습니다';
  botBtn.style.opacity = bot.alive ? '1' : '0.7';
}

async function restartDashboardServer() {
  const errLine = document.getElementById('control-error-line');
  if (errLine) errLine.textContent = '대시보드 서버 재시작 요청 중...';
  fetch('/api/control/restart-dashboard', { method: 'POST' }).catch(() => ({}));
}

async function restartTradingBot() {
  const errLine = document.getElementById('control-error-line');
  const res = await fetch('/api/control/restart-bot', { method: 'POST' })
    .then(async r => ({ ok: r.ok, data: await r.json() }))
    .catch(() => ({ ok: false, data: {} }));
  if (errLine) errLine.textContent = res.data.message || (res.ok ? '봇 재시작 요청 완료' : '봇 재시작 요청 실패');
  await loadProcessControl();
}

async function toggleClaudeReinvoke() {
  const current = await fetch('/api/claude/status?market=' + MARKET).then(r => r.json()).catch(() => ({}));
  const nextEnabled = !current.enabled;
  const res = await fetch('/api/claude/toggle', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ enabled: nextEnabled })
  }).then(r => r.json()).catch(() => ({ ok: false }));
  if (!res.ok) return;
  await loadClaudeControl();
}

async function triggerClaudeReinvoke() {
  const res = await fetch('/api/claude/trigger', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ market: MARKET })
  }).then(async r => ({ ok: r.ok, data: await r.json() })).catch(() => ({ ok: false, data: {} }));
  if (!res.ok) {
    const errLine = document.getElementById('claude-error-line');
    if (errLine) errLine.textContent = res.data.error || 'Claude 재판단 요청 실패';
    return;
  }
  await loadClaudeControl();
}

function timelineRow(kind, title, body, meta = '') {
  const color = kind === 'buy' ? '#10b981'
    : kind === 'sell' ? '#ef4444'
    : kind === 'pick' ? '#3b82f6'
    : kind === 'debate' ? '#f59e0b'
    : '#94a3b8';
  return `<div style="border:1px solid var(--border);border-left:4px solid ${color};border-radius:10px;padding:12px 14px;background:var(--surface2)">
    <div style="display:flex;justify-content:space-between;gap:12px;align-items:flex-start">
      <div style="font-size:13px;font-weight:700;color:var(--text)">${title}</div>
      <div style="font-size:11px;color:var(--text-dim);white-space:nowrap">${meta}</div>
    </div>
    <div style="font-size:12px;color:var(--text-dim);line-height:1.55;margin-top:6px">${body}</div>
  </div>`;
}

async function loadClaudeNarrative() {
  const box = document.getElementById('claude-timeline');
  if (!box) return;
  const [judgments, tickers, signals, trades] = await Promise.all([
    fetch('/api/judgments?market=' + MARKET).then(r => r.json()).catch(() => ({})),
    fetch('/api/tickers/today?market=' + MARKET).then(r => r.json()).catch(() => ({})),
    fetch('/api/signals/recent?market=' + MARKET + '&n=8').then(r => r.json()).catch(() => ([])),
    fetch('/api/trades/list' + marketParam('limit=8')).then(r => r.json()).catch(() => ([])),
  ]);
  const EVENT_KO = {
    entry_signal: '진입 신호',
    entry_skip: '진입 보류',
    signal_check: '신호 없음',
    signal_blocked: '신호 차단',
    buy_filled: '매수 체결',
    sell_filled: '매도 체결',
    trailing: '추적중',
    waiting: '대기',
    cycle_error: '처리 오류'
  };
  const REASON_KO = {
    already_holding: '이미 보유중',
    pending_order: '미체결 주문',
    invalid_price: '가격 오류',
    none: '신호 없음',
    trailing: '트레일링',
    trail_stop: '추적 손절',
    stop_loss: '손절',
    max_hold: '보유 기간 만료',
    session_close: '장마감 정산',
    live_position: '브로커 잔고 반영',
    us_order_blocked: '미국 주문 차단'
  };
  const koEvent = (v) => EVENT_KO[v] || v || '-';
  const koReason = (v) => REASON_KO[v] || v || '-';
  const tickerMap = {};
  (tickers.tickers || []).forEach(t => { tickerMap[t.ticker] = t; });

  const rows = [];
  if (judgments.consensus) {
    const bull = judgments.bull || {};
    const bear = judgments.bear || {};
    const neutral = judgments.neutral || {};
    rows.push(timelineRow(
      'judgment',
      `오늘 합의: ${koMode(judgments.consensus.mode || '-')}`,
      [
        bull.key_reason ? `Bull: ${bull.key_reason}` : '',
        bear.key_reason ? `Bear: ${bear.key_reason}` : '',
        neutral.key_reason ? `Neutral: ${neutral.key_reason}` : '',
      ].filter(Boolean).join('<br>'),
      judgments.date || ''
    ));
  }

  (judgments.debate_changes || []).slice(0, 3).forEach(ch => {
    rows.push(timelineRow(
      'debate',
      `${ch.analyst || 'analyst'} 의견 변경`,
      `${koMode(ch.r1_stance || '-')} → ${koMode(ch.r2_stance || '-')}<br>${ch.reason || ''}`,
      '토론'
    ));
  });

  (tickers.tickers || []).slice(0, 5).forEach(t => {
    rows.push(timelineRow(
      'pick',
      `${t.ticker} 선택`,
      `${t.select_reason || '선택 이유 없음'}${t.last_event_ko ? `<br>현재 상태: ${t.last_event_ko}` : (t.last_event ? `<br>현재 상태: ${t.last_event}` : '')}`,
      tickers.mode || ''
    ));
  });

  (signals || []).slice(0, 6).forEach(s => {
    const info = tickerMap[s.ticker || ''] || {};
    const price = Number(info.display_price || info.current_price || info.avg_price || s.price || 0);
    const priceTxt = price > 0 ? (MARKET === 'KR' ? Math.round(price).toLocaleString() + '원' : '$' + price.toFixed(2)) : '-';
    rows.push(timelineRow(
      s.event === 'entry_signal' ? 'buy' : s.event === 'sell_filled' ? 'sell' : 'judgment',
      `${s.ticker || '-'} · ${koEvent(s.event || '-')}`,
      `${koReason(s.reason || '-') }<br>가격: ${priceTxt}`,
      s.timestamp ? s.timestamp.substring(11, 19) : ''
    ));
  });

  (trades || []).slice(0, 6).forEach(t => {
    const price = Number(t.display_price || t.price || 0);
    const currency = (t.currency || (MARKET === 'KR' ? 'KRW' : 'USD'));
    const isKRW = currency === 'KRW';
    const priceTxt = price > 0 ? (isKRW
      ? Math.round(price).toLocaleString() + '원'
      : '$' + price.toFixed(2)) : '-';
    const buyPrice = Number(t.buy_price_native || 0);
    const buyTotal = Number(t.buy_total_native || 0);
    const sellTotal = Number(t.sell_total_native || 0);
    const pnlKrw = Number(t.pnl_krw || 0);
    let detail = `${koReason(t.reason || '-') }<br>가격: ${priceTxt}`;
    if (t.side === 'sell') {
      const buyPriceTxt = buyPrice > 0 ? (isKRW ? Math.round(buyPrice).toLocaleString() + '원' : '$' + buyPrice.toFixed(4)) : '-';
      const buyTotalTxt = buyTotal > 0 ? (isKRW ? Math.round(buyTotal).toLocaleString() + '원' : '$' + buyTotal.toFixed(2)) : '-';
      const sellTotalTxt = sellTotal > 0 ? (isKRW ? Math.round(sellTotal).toLocaleString() + '원' : '$' + sellTotal.toFixed(2)) : '-';
      const pnlKrwTxt = (pnlKrw >= 0 ? '+' : '') + Math.round(pnlKrw).toLocaleString() + '원';
      detail += `<br>매수가 ${buyPriceTxt} · 매수총액 ${buyTotalTxt}<br>매도총액 ${sellTotalTxt} · 원화손익 ${pnlKrwTxt}`;
    } else if (t.side === 'buy') {
      const tradeTotal = Number(t.trade_total_native || 0);
      if (tradeTotal > 0) {
        const tradeTotalTxt = isKRW ? Math.round(tradeTotal).toLocaleString() + '원' : '$' + tradeTotal.toFixed(2);
        detail += `<br>매수총액 ${tradeTotalTxt}`;
      }
    }
    rows.push(timelineRow(
      t.side === 'buy' ? 'buy' : t.side === 'sell' ? 'sell' : 'judgment',
      `${t.ticker || '-'} ${t.side || '-'} ${t.qty || 0}주`,
      detail,
      t.time || t.fill_time || t.date || ''
    ));
  });

  if (!rows.length) {
    box.innerHTML = '<div style="color:var(--text-dim);font-size:13px">표시할 Claude 판단/실행 내역이 아직 없습니다</div>';
    return;
  }
  box.innerHTML = rows.join('');
}

const __origTodayLoadAll = loadAll;
loadAll = async function() {
  await __origTodayLoadAll();
  await loadClaudeControl();
  await loadProcessControl();
  await loadClaudeNarrative();
};
</script>
"""

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

<!-- Row 4: Brain 일별 학습 이력 -->
<div class="card" style="margin-top:0">
  <div class="section-title" style="display:flex;align-items:center;gap:12px">
    Brain 일별 학습 이력
    <span id="brain-history-meta" style="font-size:11px;color:var(--muted);font-weight:400"></span>
  </div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>날짜</th>
          <th>모드</th>
          <th>PnL%</th>
          <th>시장%</th>
          <th>승패</th>
          <th>Bull</th>
          <th>Bear</th>
          <th>Neutral</th>
          <th>거래수</th>
          <th>교훈</th>
          <th>Best / Worst</th>
        </tr>
      </thead>
      <tbody id="brain-history-tbody"></tbody>
    </table>
  </div>
  <!-- 누적 교훈 + correction_guide -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-top:16px">
    <div>
      <div style="font-size:11px;font-weight:600;letter-spacing:1.2px;color:var(--muted);margin-bottom:8px">누적 교훈</div>
      <div id="brain-lessons-panel"></div>
    </div>
    <div>
      <div style="font-size:11px;font-weight:600;letter-spacing:1.2px;color:var(--muted);margin-bottom:8px">보정 가이드 (correction_guide)</div>
      <div id="brain-correction-panel"></div>
    </div>
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
        { label: '상승', data: d.bull, borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.14)', borderWidth: 3, pointRadius: 3, pointHoverRadius: 5, pointBackgroundColor: '#22c55e', tension: 0.35, fill: false },
        { label: '약세', data: d.bear, borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,0.14)', borderWidth: 3, pointRadius: 3, pointHoverRadius: 5, pointBackgroundColor: '#ef4444', tension: 0.35, fill: false },
        { label: '중립', data: d.neutral, borderColor: '#38bdf8', backgroundColor: 'rgba(56,189,248,0.14)', borderWidth: 3, pointRadius: 3, pointHoverRadius: 5, pointBackgroundColor: '#38bdf8', tension: 0.35, fill: false, borderDash: [6, 4] },
      ]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#cbd5e1', font: { size: 12, weight: '600' }, usePointStyle: true, pointStyle: 'line' } } },
      scales: {
        x: { ticks: { color: '#94a3b8', font: { size: 10 } }, grid: { color: 'rgba(31,41,55,0.5)' } },
        y: { min: 0, max: 100,
             ticks: { color: '#94a3b8', font: { size: 10 }, callback: v => v + '%' },
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
        <td><span class="mode-badge mode-${mode}">${koMode(mode)}</span></td>
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
  const d = await fetch('/api/brain?market=' + MARKET).then(r => r.json()).catch(() => ({}));
  const a = d.analyst || {};

  // Brain 상태 카드
  document.getElementById('brain-status').innerHTML = `
    <div style="font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:12px">
      버전 ${d.version || '-'} &nbsp;|&nbsp; ${d.trained_days || 0}일 학습 &nbsp;|&nbsp; 장세: <span style="color:var(--text)">${koRegime(d.regime)}</span>
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
            <div>HIT <span class="up">${v.hit || v.hits || 0}</span> / MISS <span class="down">${v.miss || v.misses || 0}</span> / PARTIAL <span class="neutral-color">${v.partial || v.partials || Math.max((v.total || 0) - (v.hit || v.hits || 0) - (v.miss || v.misses || 0), 0)}</span></div>
            ${v.recent_streak !== undefined ? `<div>최근 연속 <span style="color:var(--text)">${v.recent_streak}</span></div>` : ''}
            ${v.avg_confidence !== undefined ? `<div>평균 신뢰도 <span style="color:var(--text)">${Math.round((v.avg_confidence||0)*100)}%</span></div>` : ''}
          </div>
        </div>`;
      }).join('')}
    </div>
  `;

  // 전략별 성과 (brain에 strategy 필드 있을 경우)
  const strategy = d.strategy || {};
  const mergedStrategy = {};
  Object.entries(strategy).forEach(([name, v]) => {
    const key = koStrategy(name);
    if (!mergedStrategy[key]) mergedStrategy[key] = { count: 0, win_weighted: 0, pnl_weighted: 0 };
    const count = Number(v.count || 0);
    mergedStrategy[key].count += count;
    mergedStrategy[key].win_weighted += Number(v.win_rate || 0) * count;
    mergedStrategy[key].pnl_weighted += Number(v.avg_pnl || 0) * count;
  });
  const stratRows = Object.entries(mergedStrategy).map(([name, v]) => [name, {
    count: v.count,
    win_rate: v.count > 0 ? v.win_weighted / v.count : 0,
    avg_pnl: v.count > 0 ? v.pnl_weighted / v.count : 0,
  }]);
  ['변동성돌파', '갭눌림', '모멘텀', '평균회귀', '브로커동기화'].forEach(name => {
    if (!stratRows.find(row => row[0] === name)) {
      stratRows.push([name, { count: 0, win_rate: 0, avg_pnl: 0 }]);
    }
  });
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

async function loadBrainHistory() {
  const d = await fetch('/api/brain/history?market=' + MARKET).then(r => r.json()).catch(() => ({}));
  const days = d.recent_days || [];

  // 메타 헤더
  const regime = koRegime(d.market_regime || 'unknown');
  document.getElementById('brain-history-meta').textContent =
    `${d.trained_days || 0}일 학습 | 장세: ${regime}`;

  // 결과 뱃지
  function resultBadge(r) {
    if (!r) return '<span style="color:var(--muted)">-</span>';
    const col = r === 'HIT' ? 'var(--green)' : r === 'MISS' ? 'var(--red)' : 'var(--yellow)';
    return `<span style="color:${col};font-weight:600">${r}</span>`;
  }

  // 테이블 rows
  const tbody = document.getElementById('brain-history-tbody');
  if (!days.length) {
    tbody.innerHTML = '<tr><td colspan="11" style="color:var(--muted);text-align:center;padding:16px">Brain 학습 데이터 없음</td></tr>';
  } else {
    tbody.innerHTML = days.map(day => {
      const pnl    = Number(day.pnl_pct || 0);
      const mktChg = Number(day.market_change || 0);
      const pnlCol = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--muted)';
      const mktCol = mktChg > 0 ? 'var(--green)' : mktChg < 0 ? 'var(--red)' : 'var(--muted)';
      const win    = day.win ? '<span style="color:var(--green)">WIN</span>' : '<span style="color:var(--red)">LOSS</span>';
      const mode   = day.mode || '-';
      const modeCol = mode.includes('BULL') ? 'var(--green)' : mode.includes('BEAR') || mode === 'HALT' ? 'var(--red)' : mode === 'DEFENSIVE' ? 'var(--yellow)' : 'var(--cyan)';
      const lesson  = (day.key_lesson || '').slice(0, 40) || '-';
      const trades  = day.trades != null ? day.trades : '-';
      const best    = day.best_trade  ? `<span style="color:var(--green)">${day.best_trade}</span>`  : '-';
      const worst   = day.worst_trade ? `<span style="color:var(--red)">${day.worst_trade}</span>`   : '-';
      // 분석가 이유 tooltip
      const bullTitle  = `${day.bull_stance||''}: ${day.bull_reason||''}`.replace(/"/g,'&quot;');
      const bearTitle  = `${day.bear_stance||''}: ${day.bear_reason||''}`.replace(/"/g,'&quot;');
      const neutTitle  = `${day.neutral_stance||''}: ${day.neutral_reason||''}`.replace(/"/g,'&quot;');
      return `<tr>
        <td style="font-family:var(--mono);white-space:nowrap">${day.date || '-'}</td>
        <td><span style="color:${modeCol};font-weight:600">${mode}</span></td>
        <td style="font-family:var(--mono);color:${pnlCol}">${pnl >= 0 ? '+' : ''}${pnl.toFixed(2)}%</td>
        <td style="font-family:var(--mono);color:${mktCol}">${mktChg >= 0 ? '+' : ''}${mktChg.toFixed(2)}%</td>
        <td>${win}</td>
        <td title="${bullTitle}">${resultBadge(day.bull_result)}</td>
        <td title="${bearTitle}">${resultBadge(day.bear_result)}</td>
        <td title="${neutTitle}">${resultBadge(day.neutral_result)}</td>
        <td style="text-align:center;color:var(--muted)">${trades}</td>
        <td style="max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--cyan)"
            title="${(day.key_lesson||'').replace(/"/g,'&quot;')}">${lesson}</td>
        <td style="font-family:var(--mono);font-size:11px">${best} / ${worst}</td>
      </tr>`;
    }).join('');
  }

  // 누적 교훈
  const lessons = d.learned_lessons || [];
  document.getElementById('brain-lessons-panel').innerHTML = lessons.length
    ? lessons.map((l, i) => `<div style="display:flex;gap:8px;margin-bottom:6px;font-size:12px">
        <span style="color:var(--muted);min-width:20px">${i+1}.</span>
        <span style="color:var(--text)">${l}</span>
      </div>`).join('')
    : '<div style="color:var(--muted);font-size:12px">없음</div>';

  // correction_guide
  const cg = d.correction_guide || {};
  const cgKeys = Object.keys(cg);
  document.getElementById('brain-correction-panel').innerHTML = cgKeys.length
    ? cgKeys.map(k => `<div style="margin-bottom:8px;font-size:12px">
        <div style="color:var(--muted);font-size:10px;margin-bottom:2px">${k}</div>
        <div style="color:var(--yellow)">${JSON.stringify(cg[k])}</div>
      </div>`).join('')
    : '<div style="color:var(--muted);font-size:12px">없음</div>';
}

async function loadAll() {
  await Promise.all([loadAnalystChart(), loadPatterns(), loadBrain(), loadBrainHistory()]);
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
        + TODAY_SUMMARY_OVERRIDE_JS_V2
        + CLAUDE_CONTROL_JS
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
    _write_pid_file(DASHBOARD_PID_PATH, "dashboard_server", [sys.executable, str(Path(__file__).resolve())])
    atexit.register(lambda: _clear_pid_file(DASHBOARD_PID_PATH))
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
