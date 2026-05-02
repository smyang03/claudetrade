"""
dashboard_server.py
Flask 기반 트레이딩 대시보드 웹서버

페이지:
  /            오늘 현황
  /pathb       B플랜 실시간
  /preopen     장전 후보
  /analytics   분석
  /logs        위험/오류 로그

실행: python dashboard_server.py
접속: http://localhost:5000
"""

from flask import Flask, jsonify, redirect, render_template_string, request
from pathlib import Path
from datetime import datetime, date, timedelta, time as dt_time
import json, sys, os, re, subprocess, threading, atexit, time as _time, sqlite3
from collections import Counter
from contextlib import contextmanager
from typing import Optional

# .env 로드 (trading_bot과 동일한 환경변수 사용)
try:
    from dotenv import load_dotenv, dotenv_values
    load_dotenv(Path(__file__).parent.parent / ".env")
except ImportError:
    dotenv_values = None

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from datetime import timezone, timedelta as _td
    class ZoneInfo:
        def __new__(cls, _): return timezone(_td(hours=9))

KST = ZoneInfo("Asia/Seoul")

sys.path.insert(0, str(Path(__file__).parent.parent))
from runtime_paths import get_runtime_path
from bot.session_date import resolve_session_date
try:
    from interface.v2_ops_summary import build_v2_ops_summary
except Exception:
    build_v2_ops_summary = None
from credit_tracker import summary as credit_summary
import kis_api as _kis_api_module
from kis_api import (
    get_access_token,
    get_balance,
    get_price,
    get_usd_krw,
    inquire_daily_ccld_kr,
    inquire_ccnl_us,
)
try:
    from phase1_trainer.digest_builder import KR_TICKERS as _KR_TICKERS_STATIC
except Exception:
    _KR_TICKERS_STATIC = {}
try:
    from strategy.adaptive_params import adaptive_params as _adaptive_params
    from strategy.adaptive_params import get_perf_stats as _adaptive_perf_stats
    import strategy.gap_pullback as _gap_strategy
    import strategy.mean_reversion as _mr_strategy
    import strategy.momentum as _mom_strategy
    import strategy.volatility_breakout as _vb_strategy
    import strategy.param_tuner as _param_tuner_mod
except Exception:
    _adaptive_params = None
    _adaptive_perf_stats = None
    _gap_strategy = None
    _mr_strategy = None
    _mom_strategy = None
    _vb_strategy = None
    _param_tuner_mod = None

app = Flask(__name__)

_BROKER_REALIZED_CACHE: dict[tuple[str, str], dict] = {}
_KIS_RUNTIME_LOCK = threading.Lock()


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
DASHBOARD_PID_PATH = get_runtime_path("state", "dashboard_server.pid")
RESTART_DIR = get_runtime_path("state", "restart", make_parents=False)
RESTART_DIR.mkdir(parents=True, exist_ok=True)

PAPER_CASH = float(os.getenv("PAPER_CASH", "10000000"))
MAX_POSITIONS = int(os.getenv("MAX_POSITIONS", "10") or 10)
MAX_PYRAMID = int(os.getenv("MAX_PYRAMID", "8") or 8)
SYSTEM_LOG_DIR = get_runtime_path("logs", "system", make_parents=False)
RISK_LOG_DIR = get_runtime_path("logs", "risk", make_parents=False)
_STATE_FILE_ALERTS: dict[str, dict] = {}


def _normalize_mode(mode: Optional[str]) -> str:
    value = str(mode or "live").strip().lower()
    return value if value in ("paper", "live") else "live"


def _request_mode(default: str = "live") -> str:
    mode = request.args.get("mode")
    if mode is None and request.is_json:
        body = request.get_json(silent=True) or {}
        mode = body.get("mode")
    return _normalize_mode(mode or default)


def _record_state_file_alert(path: Path, exc: Exception, *, category: str = "state_json") -> None:
    key = str(path)
    _STATE_FILE_ALERTS[key] = {
        "timestamp": datetime.now(KST).isoformat(timespec="seconds"),
        "level": "ERROR",
        "source": "dashboard",
        "category": category,
        "market": "",
        "ticker": "",
        "message": f"{path.name} JSON load failed: {exc}",
        "path": key,
    }


def _clear_state_file_alert(path: Path) -> None:
    _STATE_FILE_ALERTS.pop(str(path), None)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def _is_live_mode(mode: Optional[str]) -> bool:
    return _normalize_mode(mode) == "live"


def _claude_control_path(mode: str) -> Path:
    return get_runtime_path("state", f"{_normalize_mode(mode)}_claude_control.json")


def _bot_pid_path(mode: str) -> Path:
    return get_runtime_path("state", f"{_normalize_mode(mode)}_trading_bot.pid")


def _open_positions_path(mode: str) -> Path:
    return get_runtime_path("state", f"{_normalize_mode(mode)}_open_positions.json")


def _broker_equity_history_path(mode: str) -> Path:
    return get_runtime_path("state", f"{_normalize_mode(mode)}_broker_equity_history.jsonl")


def _live_status_path(mode: str, market: str) -> Path:
    return get_runtime_path("state", f"{_normalize_mode(mode)}_live_status_{market}.json")


def _decisions_path(mode: str) -> Path:
    return get_runtime_path("state", f"{_normalize_mode(mode)}_decisions.jsonl")


def _judgment_path(market: str, trade_date: str, mode: str) -> Path:
    day = str(trade_date or "").replace("-", "")
    return LOG_DIR / f"{_normalize_mode(mode)}_{day}_{market}.json"


def _judgment_candidates(market: str, mode: str) -> list[Path]:
    runtime_mode = _normalize_mode(mode)
    preferred = sorted(LOG_DIR.glob(f"{runtime_mode}_*_{market}.json"))
    legacy = sorted(LOG_DIR.glob(f"*_{market}.json"))
    seen = set()
    ordered = []
    for path in preferred + legacy:
        if path in seen:
            continue
        seen.add(path)
        ordered.append(path)
    return ordered


def _runtime_env_path(mode: str) -> Path:
    runtime_mode = _normalize_mode(mode)
    candidate = BASE_DIR / f".env.{runtime_mode}"
    return candidate if candidate.exists() else BASE_DIR / ".env"


def _runtime_env(mode: str) -> dict:
    path = _runtime_env_path(mode)
    if dotenv_values is None or not path.exists():
        return {}
    try:
        data = dotenv_values(path)
    except Exception:
        return {}
    return {str(k): v for k, v in (data or {}).items() if v is not None}


def _get_env_int(mode: str, key: str, default: int) -> int:
    env = _runtime_env(mode)
    raw = env.get(key) or os.getenv(key)
    try:
        return int(raw) if raw else default
    except (ValueError, TypeError):
        return default


def _runtime_bool(values: dict, key: str, default: bool) -> bool:
    raw = str(values.get(key, "") or "").strip()
    if not raw:
        return default
    return raw.lower() == "true"


@contextmanager
def _kis_runtime(mode: str):
    runtime_mode = _normalize_mode(mode)
    env = _runtime_env(runtime_mode)
    attrs = [
        "APP_KEY", "APP_SECRET", "ACCOUNT_NO", "IS_PAPER",
        "ACCOUNT_NO_US", "APP_KEY_US", "APP_SECRET_US", "IS_PAPER_US",
        "BASE_URL", "WS_URL", "TOKEN_FILE",
    ]
    with _KIS_RUNTIME_LOCK:
        previous = {name: getattr(_kis_api_module, name) for name in attrs}
        try:
            app_key = str(env.get("KIS_APP_KEY", previous["APP_KEY"]) or "")
            app_secret = str(env.get("KIS_APP_SECRET", previous["APP_SECRET"]) or "")
            account_no = str(env.get("KIS_ACCOUNT_NO", previous["ACCOUNT_NO"]) or "")
            is_paper = _runtime_bool(env, "KIS_IS_PAPER", runtime_mode == "paper")
            account_no_us = str(env.get("KIS_ACCOUNT_NO_US", "") or account_no)
            app_key_us = str(env.get("KIS_APP_KEY_US", "") or app_key)
            app_secret_us = str(env.get("KIS_APP_SECRET_US", "") or app_secret)
            raw_is_paper_us = str(env.get("KIS_IS_PAPER_US", "") or "").strip()
            is_paper_us = (raw_is_paper_us.lower() == "true") if raw_is_paper_us else is_paper
            base_url = str(
                env.get(
                    "KIS_BASE_URL",
                    "https://openapivts.koreainvestment.com:29443" if is_paper else "https://openapi.koreainvestment.com:9443",
                ) or ""
            )
            ws_url = (
                "ws://ops.koreainvestment.com:31000"
                if is_paper
                else "ws://ops.koreainvestment.com:21000"
            )

            _kis_api_module.APP_KEY = app_key
            _kis_api_module.APP_SECRET = app_secret
            _kis_api_module.ACCOUNT_NO = account_no
            _kis_api_module.IS_PAPER = is_paper
            _kis_api_module.ACCOUNT_NO_US = account_no_us
            _kis_api_module.APP_KEY_US = app_key_us
            _kis_api_module.APP_SECRET_US = app_secret_us
            _kis_api_module.IS_PAPER_US = is_paper_us
            _kis_api_module.BASE_URL = base_url
            _kis_api_module.WS_URL = ws_url
            _kis_api_module.TOKEN_FILE = get_runtime_path("state", f"{runtime_mode}_kis_token.json")
            yield
        finally:
            for name, value in previous.items():
                setattr(_kis_api_module, name, value)


# 데이터 로더

def current_market() -> str:
    now = datetime.now(KST).time()
    if now >= dt_time(22, 20) or now < dt_time(5, 0):
        return "US"
    return "KR"


def _market_log_date_str(market: str, now_dt=None) -> str:
    return _session_trade_date(market, now_dt).strftime("%Y%m%d")


def _date_str_prefix(date_str: str) -> str:
    day = str(date_str or "").replace("-", "")
    if len(day) != 8:
        return ""
    return day[:4] + "-" + day[4:6] + "-" + day[6:]


def _preferred_analysis_log_path(date_str: str, mode: str) -> Optional[Path]:
    analysis_dir = BASE_DIR / "logs" / "analysis"
    mode_name = _normalize_mode(mode)
    prefixes = ((mode_name + "_") if mode_name != "paper" else "", "")
    for prefix in prefixes:
        candidate = analysis_dir / f"{prefix}analysis_{date_str}.jsonl"
        if candidate.exists():
            return candidate
    return None


def _load_analysis_records_for_session(market: str, mode: str) -> list[dict]:
    session_date = _market_log_date_str(market)
    system_date = datetime.now(KST).strftime("%Y%m%d")
    day_candidates = [session_date]
    if system_date != session_date:
        day_candidates.append(system_date)
    allowed_prefixes = {
        prefix
        for prefix in (_date_str_prefix(session_date), _date_str_prefix(system_date))
        if prefix
    }

    records: list[dict] = []
    seen_paths: set[str] = set()
    for day_str in day_candidates:
        path = _preferred_analysis_log_path(day_str, mode)
        if not path:
            continue
        path_key = str(path)
        if path_key in seen_paths:
            continue
        seen_paths.add(path_key)
        try:
            raw_lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        for line in raw_lines:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = str(rec.get("timestamp", "") or "")
            if ts and allowed_prefixes and not any(ts.startswith(prefix) for prefix in allowed_prefixes):
                continue
            records.append(rec)
    return records


def best_market_with_data() -> str:
    mode = _normalize_mode(request.args.get("mode")) if request else "paper"
    for mkt in ("US", "KR"):
        today = _market_log_date_str(mkt)
        p = _judgment_path(mkt, today, mode)
        if p.exists():
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("mode") != "historical_sim":
                    return mkt
            except Exception:
                pass
        legacy = LOG_DIR / f"{today}_{mkt}.json"
        if legacy.exists():
            try:
                with open(legacy, encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("mode") != "historical_sim":
                    return mkt
            except Exception:
                pass
    return current_market()


def load_records(days: int = 9999, market: str = "KR") -> list:
    if not LOG_DIR.exists():
        return []
    records = []
    for path in _judgment_candidates(market, _request_mode()):
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
        try:
            with open(source, encoding="utf-8-sig") as f:
                return json.load(f)
        except Exception as e:
            app.logger.warning(f"[dashboard] load_brain failed: {source} ({e})")
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
    mode = _request_mode()
    info = _read_pid_file(_bot_pid_path(mode))
    pid = int(info.get("pid", 0) or 0)
    if not pid or not _pid_alive(pid):
        return False, "봇 PID 파일이 없거나 프로세스가 실행 중이 아닙니다"
    bot_args = [str(BASE_DIR / "trading_bot.py")] + ([] if mode == "paper" else ["--live"])
    helper = (
        "import subprocess,time,sys; "
        f"subprocess.run(['taskkill','/PID','{pid}','/T','/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "time.sleep(1.2); "
        f"subprocess.Popen(['cmd','/c','start','','{sys.executable}',*{repr(bot_args)}], "
        f"cwd={repr(str(BASE_DIR))}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
    )
    _spawn_detached([sys.executable, "-c", helper], BASE_DIR)
    return True, "재시작 요청을 보냈습니다"


def _restart_bot_process() -> tuple[bool, str]:
    mode = _request_mode()
    info = _read_pid_file(_bot_pid_path(mode))
    pid = int(info.get("pid", 0) or 0)
    if not pid or not _pid_alive(pid):
        return False, "봇 PID 파일이 없거나 프로세스가 실행 중이 아닙니다"
    bot_path = BASE_DIR / "trading_bot.py"
    bot_cmd = (
        f'start "" "{sys.executable}" "{bot_path}"'
        if mode == "paper"
        else f'start "" "{sys.executable}" "{bot_path}" --live'
    )
    script_path = _write_launcher(
        RESTART_DIR / f"restart_{mode}_bot.cmd",
        [
            "@echo off",
            "setlocal",
            f"taskkill /PID {pid} /T /F >nul 2>nul",
            "timeout /t 2 /nobreak >nul",
            f'cd /d "{BASE_DIR}"',
            bot_cmd,
            "endlocal",
        ],
    )
    _spawn_detached(["cmd", "/c", str(script_path)], BASE_DIR)
    return True, "재시작 요청을 보냈습니다"


def _is_fresh_live_status(live: dict, today_rec: dict) -> bool:
    if not live or not live.get("session_active"):
        return False

    trading_date = live.get("trading_date", "")
    today_date = today_rec.get("date", "")
    if not trading_date or not today_date:
        return False

    market = live.get("market") or today_rec.get("market") or "KR"
    current_date = _session_trade_date(market).isoformat()
    if trading_date[:10] != today_date[:10] or today_date[:10] != current_date:
        return False
    return _session_status(market).get("active", False)


def load_today(market: str = "KR") -> dict:
    today = _market_log_date_str(market)
    mode = _request_mode()
    path = _judgment_path(market, today, mode)
    if path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                rec = json.load(f)
            if rec.get("mode") != "historical_sim":
                return rec
        except Exception:
            pass
    legacy = LOG_DIR / f"{today}_{market}.json"
    if legacy.exists():
        try:
            with open(legacy, encoding="utf-8") as f:
                rec = json.load(f)
            if rec.get("mode") != "historical_sim":
                return rec
        except Exception:
            pass
    if not LOG_DIR.exists():
        return {}
    for path in reversed(_judgment_candidates(market, mode)):
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


# 로그 파싱 결과 캐시: {(date, market): (ts, result)}
# 오늘 날짜는 30초 TTL, 과거 날짜는 영구 캐시
_log_parse_cache: dict = {}

# USD/KRW 환율 캐시 - request path에서 KIS API 호출 완전 차단
# 백그라운드 스레드가 10분마다 갱신, request는 마지막 값 즉시 반환
_usd_krw_cache: list = [0.0, 0.0]   # [value, monotonic_ts]
_usd_krw_lock  = threading.Lock()

def _usd_krw_bg_refresh():
    """백그라운드에서 10분마다 환율을 갱신한다. request path 블로킹은 없다."""
    while True:
        try:
            val = float(get_usd_krw())
            if val > 0:
                with _usd_krw_lock:
                    _usd_krw_cache[0] = val
                    _usd_krw_cache[1] = _time.monotonic()
        except Exception:
            pass
        _time.sleep(600)

_usd_krw_thread = threading.Thread(target=_usd_krw_bg_refresh, daemon=True)
_usd_krw_thread.start()

def _get_usd_krw_cached() -> float:
    """항상 즉시 반환한다. 캐시가 비어 있으면 환경변수 기본값을 사용한다."""
    with _usd_krw_lock:
        val = _usd_krw_cache[0]
    return val if val > 0 else float(os.getenv("USD_KRW_RATE", "1400") or 1400)

def _parse_trade_log_lines(rec_date: str, market: str) -> list:
    cache_key = (rec_date, market)
    now_ts = _time.monotonic()
    today_str = date.today().strftime("%Y-%m-%d")
    # 과거 날짜는 영구 캐시, 오늘 날짜만 30초 TTL
    if cache_key in _log_parse_cache:
        cached_ts, cached_result = _log_parse_cache[cache_key]
        is_today = rec_date.replace("-", "")[:8] == today_str.replace("-", "")
        ttl = 30 if is_today else float("inf")
        if now_ts - cached_ts < ttl:
            return cached_result

    path = _log_path_for_date(rec_date)
    if not path.exists():
        _log_parse_cache[cache_key] = (now_ts, [])
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
        return None

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
    _log_parse_cache[cache_key] = (_time.monotonic(), result)
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
    execution_contaminated = bool(actual.get("execution_contaminated", False))
    execution_issues = list(actual.get("execution_issues", []) or [])

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
            "execution_contaminated": execution_contaminated,
            "execution_issues": execution_issues,
        }

    if trades:
        return {
            "trades": len(trades),
            "pnl_krw": float(actual.get("pnl_krw", 0) or 0),
            "pnl_pct": float(actual.get("pnl_pct", 0) or 0),
            "win": bool(actual.get("win", False)),
            "cumulative": float(actual.get("cumulative", PAPER_CASH) or PAPER_CASH),
            "execution_contaminated": execution_contaminated,
            "execution_issues": execution_issues,
        }

    return {
        "trades": int(actual.get("trades", 0) or 0),
        "pnl_krw": float(actual.get("pnl_krw", 0) or 0),
        "pnl_pct": float(actual.get("pnl_pct", 0) or 0),
        "win": bool(actual.get("win", False)),
        "cumulative": float(actual.get("cumulative", PAPER_CASH) or PAPER_CASH),
        "execution_contaminated": execution_contaminated,
        "execution_issues": execution_issues,
    }


def _live_trades(market: str) -> list:
    live = _load_live_status(market, mode=_request_mode())
    if not live or not live.get("session_active"):
        return []
    trade_date = live.get("trading_date", "")
    if trade_date[:10] != _session_trade_date(market).isoformat():
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


def _enrich_trade_row(row: dict, market: str, name_map: Optional[dict] = None) -> dict:
    t = dict(row or {})
    name_map = name_map or _ticker_name_map(market, include_broker=False)
    t["source_kind"] = t.get("source_kind", "trade_record")
    t["source_kind_label"] = {
        "trade_record": "체결",
        "pending_order": "미체결",
        "live_position": "라이브",
        "recovered": "복구",
        "broker_fill": "브로커 체결",
    }.get(t["source_kind"], t["source_kind"])
    t["strategy"] = _ko_strategy_name(t.get("strategy", ""))
    t["display_ticker"] = _display_ticker_label(t.get("ticker", ""), t.get("name", ""), name_map)
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
                usdkrw = _get_usd_krw_cached()
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


def _trade_match_price(row: dict) -> float:
    return round(float(row.get("display_price", row.get("price", 0)) or 0), 4)


def _trade_match_key_loose(row: dict) -> tuple:
    return (
        str(row.get("date", "") or ""),
        str(row.get("ticker", "") or "").upper(),
        str(row.get("side", "") or "").lower(),
        int(row.get("qty", 0) or 0),
    )


def _trade_match_key_strict(row: dict) -> tuple:
    return _trade_match_key_loose(row) + (_trade_match_price(row),)


def _annotate_trade_matches(strategy_rows: list[dict], broker_rows: list[dict]) -> tuple[list[dict], list[dict]]:
    strategy = [dict(r) for r in (strategy_rows or [])]
    broker = [dict(r) for r in (broker_rows or [])]

    broker_by_order = {}
    broker_by_strict = {}
    broker_by_loose = {}
    for idx, row in enumerate(broker):
        row["match_status"] = row.get("match_status") or "broker_only"
        row["matched_order_no"] = row.get("matched_order_no") or ""
        row["matched_date"] = row.get("matched_date") or ""
        row["matched_time"] = row.get("matched_time") or ""
        row["_matched"] = False
        order_no = str(row.get("order_no", "") or "").strip()
        if order_no:
            broker_by_order.setdefault(order_no, []).append(idx)
        broker_by_strict.setdefault(_trade_match_key_strict(row), []).append(idx)
        broker_by_loose.setdefault(_trade_match_key_loose(row), []).append(idx)

    def _claim(indices: list[int]) -> Optional[int]:
        for i in indices:
            if not broker[i].get("_matched"):
                broker[i]["_matched"] = True
                return i
        return None

    for row in strategy:
        row["match_status"] = row.get("match_status") or "strategy_only"
        row["matched_order_no"] = row.get("matched_order_no") or ""
        row["matched_date"] = row.get("matched_date") or ""
        row["matched_time"] = row.get("matched_time") or ""
        match_idx = None
        order_no = str(row.get("order_no", "") or "").strip()
        if order_no and order_no in broker_by_order:
            match_idx = _claim(broker_by_order[order_no])
        if match_idx is None:
            match_idx = _claim(broker_by_strict.get(_trade_match_key_strict(row), []))
        if match_idx is None:
            match_idx = _claim(broker_by_loose.get(_trade_match_key_loose(row), []))
        if match_idx is None:
            continue
        b = broker[match_idx]
        row["match_status"] = "matched"
        row["matched_order_no"] = str(b.get("order_no", "") or "")
        row["matched_date"] = str(b.get("date", "") or "")
        row["matched_time"] = str(b.get("time", "") or "")
        b["match_status"] = "matched"
        b["matched_order_no"] = str(row.get("order_no", "") or "")
        b["matched_date"] = str(row.get("date", "") or "")
        b["matched_time"] = str(row.get("time", "") or "")

    for row in broker:
        row.pop("_matched", None)

    return strategy, broker


def _trade_stats_from_rows(trades: list, days: int) -> dict:
    sells = [t for t in trades if t.get("side") == "sell"]
    known_sells = [t for t in sells if bool(t.get("pnl_known", True))]
    unknown_sells = [t for t in sells if not bool(t.get("pnl_known", True))]
    wins = [t for t in known_sells if float(t.get("pnl", 0) or 0) > 0]
    losses = [t for t in known_sells if float(t.get("pnl", 0) or 0) < 0]
    total_pnl = sum(float(t.get("pnl_pct", 0) or 0) for t in known_sells)
    trade_count = len(known_sells)
    return {
        "days": days,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / trade_count * 100, 1) if trade_count else 0,
        "total_pnl": round(total_pnl, 2),
        "avg_pnl": round(total_pnl / trade_count, 2) if trade_count else 0,
        "trades": trade_count,
        "unknown_trades": len(unknown_sells),
        "basis": "closed_trades",
    }


def _parse_runtime_events(market: str, limit: int = 200) -> list:
    path = _log_path_for_date(_session_trade_date(market).isoformat())
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
    placeholders = ("오류로 자동 판정", "API 오류로 자동 판정", "postmortem 응답 실패", "자동 판정", "오류로", "자동", "응답 실패")
    if any(p in text for p in placeholders):
        return ""
    return text


def _execution_issue_label(issue: str) -> str:
    mapping = {
        "broker_position_removed": "브로커 포지션 동기화 오류",
        "quote_invalid": "시세 무효/가격 수신 오류",
    }
    key = str(issue or "").strip()
    return mapping.get(key, key or "-")


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


def _risk_status_label(value: float) -> str:
    if value <= 0:
        return ""
    if value < 15:
        return "저변동"
    if value < 20:
        return "정상"
    if value < 25:
        return "주의"
    if value < 30:
        return "고변동"
    return "위기"


def _current_risk_snapshot(market: str, today_rec: Optional[dict] = None) -> dict:
    label = "VKOSPI" if market == "KR" else "VIX"
    value = 0.0
    ctx = ((today_rec or {}).get("digest_raw") or {}).get("context") or {}
    try:
        value = float(ctx.get("vix") or 0)
    except Exception:
        value = 0.0
    if value <= 0:
        records = load_digest_records_filtered(market, "all", "", "")
        if records:
            ctx = (records[-1].get("context") or {})
            try:
                value = float(ctx.get("vix") or 0)
            except Exception:
                value = 0.0
    return {
        "risk_label": label,
        "risk_value": round(value, 2) if value > 0 else 0.0,
        "risk_status": _risk_status_label(value),
    }


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


def _resolve_period_bounds(period: str, start: str, end: str) -> tuple[date, date]:
    today = date.today()
    if period == "week":
        return today - timedelta(days=today.weekday()), today
    if period == "month":
        return today.replace(day=1), today
    if period == "3month":
        return today - timedelta(days=90), today
    if period == "custom":
        d_start = _parse_date(start)
        d_end = _parse_date(end) if end else today
        return d_start, d_end
    # all
    return date(2026, 1, 1), today


def _load_broker_trade_bundle(market: str, period: str, start: str, end: str, mode: str = "paper") -> dict:
    d_start, d_end = _resolve_period_bounds(period, start, end)
    meta = {
        "ok": False,
        "market": market,
        "query_start": d_start.isoformat(),
        "query_end": d_end.isoformat(),
        "rows": [],
        "count": 0,
        "latest_fill": "",
        "error": "",
    }

    start_s = d_start.strftime("%Y%m%d")
    end_s = d_end.strftime("%Y%m%d")
    rows: list[dict] = []
    try:
        with _kis_runtime(mode):
            token = get_access_token()
            if market == "KR":
                broker_rows = inquire_daily_ccld_kr(
                    token,
                    start_date=start_s,
                    end_date=end_s,
                    filled_code="01",
                )
            else:
                broker_rows = inquire_ccnl_us(
                    token,
                    start_date=start_s,
                    end_date=end_s,
                    filled_code="01",
                )
    except Exception as e:
        meta["error"] = str(e)
        return meta

    for row in broker_rows or []:
        ticker = str(row.get("ticker", "") or "").strip().upper()
        if not ticker:
            continue
        filled_qty = int(row.get("filled_qty", 0) or 0)
        if filled_qty <= 0:
            continue
        raw = row.get("raw", {}) or {}
        order_date_raw = str(raw.get("ord_dt") or raw.get("ORD_DT") or raw.get("ord_date") or "").strip()
        order_date = (
            f"{order_date_raw[:4]}-{order_date_raw[4:6]}-{order_date_raw[6:8]}"
            if len(order_date_raw) == 8 else d_end.isoformat()
        )
        order_time = str(row.get("order_time", "") or "").strip()
        hhmm = f"{order_time[:2]}:{order_time[2:4]}" if len(order_time) >= 4 else ""
        fill_price = float(row.get("fill_price", 0) or 0)
        rows.append({
            "date": order_date,
            "time": hhmm,
            "side": row.get("side", "") or "",
            "ticker": ticker,
            "strategy": "broker_sync",
            "price": fill_price,
            "display_price": fill_price,
            "qty": filled_qty,
            "pnl": 0.0,
            "pnl_pct": 0.0,
            "reason": "broker_fill",
            "order_no": str(row.get("order_no", "") or ""),
            "price_source": "order_fill",
            "currency": "USD" if market == "US" else "KRW",
            "fill_time": hhmm,
            "source_kind": "broker_fill",
        })
    dedup = []
    seen = set()
    for row in rows:
        key = (
            row.get("date", ""),
            row.get("time", ""),
            row.get("side", ""),
            row.get("ticker", ""),
            row.get("order_no", ""),
            int(row.get("qty", 0) or 0),
        )
        if key in seen:
            continue
        seen.add(key)
        dedup.append(_enrich_trade_row(row, market))
    dedup.sort(key=lambda r: ((r.get("date", "") or ""), (r.get("time", "") or "")), reverse=True)
    meta["ok"] = True
    meta["rows"] = dedup
    meta["count"] = len(dedup)
    if dedup:
        latest = dedup[0]
        meta["latest_fill"] = f"{latest.get('date', '')} {latest.get('time', '')}".strip()
    return meta


def _load_broker_trade_rows(market: str, period: str, start: str, end: str, mode: str = "paper") -> list[dict]:
    return _load_broker_trade_bundle(market, period, start, end, mode=mode)["rows"]


def group_by_month(records: list) -> dict:
    groups = {}
    for r in records:
        key = r.get("date", "")[:7]  # YYYY-MM
        groups.setdefault(key, []).append(r)
    return groups


# API 로드/상태

def _load_live_status(market: str, mode: str = "paper") -> dict:
    """trading_bot이 사이클마다 기록하는 라이브 상태 파일을 읽는다."""
    path = _live_status_path(mode, market)
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


def _load_claude_control(mode: str = "paper") -> dict:
    data = _default_claude_control()
    path = _claude_control_path(mode)
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(saved, dict):
                data.update(saved)
        except Exception:
            pass
    return data


def _save_claude_control(data: dict, mode: str = "paper"):
    merged = _default_claude_control()
    merged.update(data or {})
    _claude_control_path(mode).write_text(
        json.dumps(merged, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_live_status(market: str, mode: str = "paper") -> dict:
    """Override: sanitize cross-market rows inside live status."""
    path = _live_status_path(mode, market)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        _clear_state_file_alert(path)
    except Exception as exc:
        _record_state_file_alert(path, exc)
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
        try:
            _atomic_write_text(path, json.dumps(data, ensure_ascii=False))
        except Exception as exc:
            _record_state_file_alert(path, exc, category="state_json_write")
    return data


def _load_claude_control(mode: str = "paper") -> dict:
    """Override: recover stale running state to error."""
    data = _default_claude_control()
    path = _claude_control_path(mode)
    if path.exists():
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
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
            data["last_error"] = data.get("last_error") or "이전 Claude 재판단 결과가 오래되어 상태를 초기화했습니다"
            try:
                _save_claude_control(data, mode=mode)
            except Exception:
                pass
    return data


def _load_broker_positions(market: str, mode: str = "paper"):
    """KIS 브로커 잔고에서 현재 보유 포지션을 직접 읽어 대시보드에 표시한다."""
    try:
        with _kis_runtime(mode):
            token = get_access_token()
            bal = get_balance(token, market=market, force_refresh=True)
    except Exception:
        return None

    positions = []
    for stock in bal.get("stocks", []):
        avg_price = float(stock.get("avg_price", 0) or 0)
        current_price = float(stock.get("eval_price", 0) or 0)
        pnl_pct = float(stock.get("profit_rate", 0) or 0)
        if not pnl_pct and avg_price > 0 and current_price > 0:
            pnl_pct = (current_price / avg_price - 1.0) * 100.0
        positions.append({
            "ticker": stock.get("ticker", ""),
            "name": stock.get("name", "") or stock.get("prdt_name", "") or "",
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


def _saved_positions_for_market(market: str, mode: str = "paper") -> list:
    # open_positions.json + live_status를 함께 사용.
    # 우선순위는 open_positions < live_status 로 두어 수동 재판단/실시간 상태가 이김.
    merged: dict[str, dict] = {}

    for pos in _load_open_positions(mode):
        ticker = str(pos.get("ticker", "") or "").strip().upper()
        if ticker and _ticker_market(ticker) == market:
            merged[ticker] = dict(pos)

    for source in (
        _load_live_status(market, mode=mode) or {},
        _load_live_status("US" if market == "KR" else "KR", mode=mode) or {},
    ):
        for pos in source.get("positions", []) or []:
            ticker = str(pos.get("ticker", "") or "").strip().upper()
            if ticker and _ticker_market(ticker) == market:
                base = merged.get(ticker)
                merged[ticker] = _merge_position_context(base or {}, pos) if base else dict(pos)

    return list(merged.values())


def _live_position_context_for_market(market: str, mode: str = "paper") -> list:
    live = _load_live_status(market, mode=mode) or {}
    return _filter_items_for_market(live.get("positions", []) or [], market)


def _load_open_positions(mode: str = "paper") -> list:
    path = _open_positions_path(mode)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or []
        _clear_state_file_alert(path)
        return data
    except Exception as exc:
        _record_state_file_alert(path, exc)
        return []


def _save_open_positions(items: list, mode: str = "paper") -> None:
    _atomic_write_text(_open_positions_path(mode), json.dumps(items or [], ensure_ascii=False, indent=2))


def _merge_position_context(base: dict, overlay: Optional[dict]) -> dict:
    merged = dict(base or {})
    if not overlay:
        if merged.get("avg_price") in (None, "", 0) and merged.get("entry"):
            merged["avg_price"] = merged.get("entry")
        if merged.get("entry") in (None, "", 0) and merged.get("avg_price"):
            merged["entry"] = merged.get("avg_price")
        if merged.get("display_avg_price") in (None, "", 0) and merged.get("avg_price"):
            merged["display_avg_price"] = merged.get("avg_price")
        if merged.get("display_current_price") in (None, "", 0) and merged.get("current_price"):
            merged["display_current_price"] = merged.get("current_price")
        return merged
    for key in (
        "strategy", "entry", "avg_price", "display_avg_price", "display_current_price",
        "tp", "tp_price", "sl", "tp_triggered", "trailing", "trail_sl", "trail_pct",
        "held_days", "entry_date", "hold_advice", "name", "qty",
    ):
        value = overlay.get(key)
        if value not in (None, "", 0) or key in ("tp_triggered", "trailing"):
            merged[key] = value
    if merged.get("entry") in (None, "", 0) and merged.get("avg_price"):
        merged["entry"] = merged.get("avg_price")
    if merged.get("display_avg_price") in (None, "", 0) and merged.get("avg_price"):
        merged["display_avg_price"] = merged.get("avg_price")
    if merged.get("avg_price") in (None, "", 0) and merged.get("entry"):
        merged["avg_price"] = merged.get("entry")
    if merged.get("current_price") in (None, "", 0) and merged.get("display_current_price"):
        merged["current_price"] = merged.get("display_current_price")
    if merged.get("display_current_price") in (None, "", 0) and merged.get("current_price"):
        merged["display_current_price"] = merged.get("current_price")
    return merged


def _merge_positions_for_display(
    market: str, broker_positions: list, live_positions: list, broker_ok: bool = True
) -> list:
    live_map = {}
    for pos in live_positions or []:
        ticker = str(pos.get("ticker", "") or "").strip().upper()
        if ticker and _ticker_market(ticker) == market:
            live_map[ticker] = pos

    merged = []
    seen = set()
    for pos in broker_positions or []:
        ticker = str(pos.get("ticker", "") or "").strip().upper()
        if not ticker:
            continue
        merged.append(_merge_position_context(pos, live_map.get(ticker)))
        seen.add(ticker)

    # broker API 성공 시 broker를 ground truth로 사용 (보유 0개 포함)
    # broker API 실패(broker_ok=False)일 때만 live_map 폴백
    if not broker_ok:
        for ticker, pos in live_map.items():
            if ticker not in seen:
                merged.append(_merge_position_context(pos, None))

    return merged


def _broker_trade_rows_with_pnl(market: str, period: str, start: str, end: str, mode: str = "paper") -> list[dict]:
    d_start, d_end = _resolve_period_bounds(period, start, end)
    seed_start = date(d_start.year, 1, 1)
    seeded_rows = _load_broker_trade_rows(
        market,
        "custom",
        seed_start.isoformat(),
        d_end.isoformat(),
        mode=mode,
    )
    usd_krw = _get_usd_krw_cached()
    try:
        from collections import defaultdict, deque
    except Exception:
        defaultdict = dict  # type: ignore[assignment]
        deque = list  # type: ignore[assignment]

    queues = defaultdict(deque)
    enriched: list[dict] = []
    for raw in sorted(seeded_rows, key=lambda r: (str(r.get("date", "")), str(r.get("time", "")), str(r.get("order_no", "")))):
        row = dict(raw or {})
        ticker = str(row.get("ticker", "") or "").strip().upper()
        side = str(row.get("side", "") or "").lower()
        qty = int(row.get("qty", 0) or 0)
        price = float(row.get("display_price", row.get("price", 0)) or 0)
        if not ticker or qty <= 0 or price <= 0:
            continue
        row["pnl_known"] = True
        row["buy_price_native"] = 0.0
        row["pnl"] = 0.0
        row["pnl_pct"] = 0.0
        if side == "buy":
            queues[ticker].append([qty, price])
        elif side == "sell":
            remaining = qty
            matched_qty = 0
            total_cost = 0.0
            while remaining > 0 and queues[ticker]:
                buy_qty, buy_px = queues[ticker][0]
                take = min(remaining, buy_qty)
                total_cost += take * buy_px
                matched_qty += take
                buy_qty -= take
                remaining -= take
                if buy_qty <= 0:
                    queues[ticker].popleft()
                else:
                    queues[ticker][0][0] = buy_qty
            if matched_qty > 0:
                avg_buy = total_cost / matched_qty
                pnl_native = price * matched_qty - total_cost
                row["buy_price_native"] = round(avg_buy, 6)
                row["pnl"] = round(pnl_native if market == "KR" else pnl_native * usd_krw, 6)
                if avg_buy > 0:
                    row["pnl_pct"] = round((price / avg_buy - 1.0) * 100.0, 6)
            if matched_qty != qty:
                row["pnl_known"] = False
                row["reason"] = "broker_fill_cost_basis_partial"
        else:
            row["pnl_known"] = False
        trade_day = _parse_date(str(row.get("date", "") or ""))
        if d_start <= trade_day <= d_end:
            enriched.append(_enrich_trade_row(row, market))
    return enriched


def _resolve_review_position(market: str, ticker: str, mode: str = "paper") -> tuple[str, Optional[dict], dict, list]:
    ticker = str(ticker or "").strip().upper()
    markets = [market]
    other = "US" if market == "KR" else "KR"
    if other not in markets:
        markets.append(other)

    for mkt in markets:
        live = _load_live_status(mkt, mode=mode) or {}
        positions = live.get("positions", []) or []
        pos = next((p for p in positions if str(p.get("ticker", "") or "").strip().upper() == ticker), None)
        if pos is not None:
            return mkt, pos, live, positions

    if _is_live_market(market):
        for mkt in markets:
            broker_positions = _load_broker_positions(mkt, mode=mode) or []
            broker_pos = next((p for p in broker_positions if str(p.get("ticker", "") or "").strip().upper() == ticker), None)
            if broker_pos is None:
                continue
            saved = next(
                (p for p in _saved_positions_for_market(mkt, mode=mode)
                 if str(p.get("ticker", "") or "").strip().upper() == ticker),
                None,
            )
            merged = _merge_position_context(broker_pos, saved)
            live = _load_live_status(mkt, mode=mode) or {}
            positions = live.get("positions", []) or []
            return mkt, merged, live, positions

    for mkt in markets:
        saved = next(
            (p for p in _saved_positions_for_market(mkt, mode=mode)
             if str(p.get("ticker", "") or "").strip().upper() == ticker),
            None,
        )
        if saved is not None:
            live = _load_live_status(mkt, mode=mode) or {}
            positions = live.get("positions", []) or []
            return mkt, saved, live, positions

    return market, None, {}, []


def _broker_snapshot(mode: str = "paper") -> dict:
    """KIS 브로커 기준 KR/US 통합 스냅샷."""
    try:
        with _kis_runtime(mode):
            token = get_access_token()
            try:
                kr = get_balance(token, market="KR", force_refresh=True)
            except Exception:
                kr = {}
            try:
                us = get_balance(token, market="US", force_refresh=True)
            except Exception:
                us = {}
    except Exception:
        return {}

    runtime_mode = _normalize_mode(mode)
    usd_krw = _get_usd_krw_cached()

    kr_cash = float(kr.get("cash", 0) or 0)
    kr_orderable = float(kr.get("orderable_cash", kr_cash) or kr_cash)
    kr_eval = float(kr.get("total_eval", 0) or 0)
    us_cash_usd = float(us.get("cash", 0) or 0)
    us_orderable_cash_usd = float(us.get("orderable_cash", us_cash_usd) or us_cash_usd)
    us_eval_usd = float(us.get("total_eval", 0) or 0)
    us_cash_krw = us_cash_usd * usd_krw
    us_orderable_cash_krw = us_orderable_cash_usd * usd_krw
    us_eval_krw = us_eval_usd * usd_krw
    source = "broker"

    # KIS US paper는 달러 현금을 0으로 반환하는 경우가 있어 KR 현금이 과대계상될 수 있다.
    kr_cash_effective = kr_cash
    if runtime_mode == "paper" and us_cash_usd == 0 and (us.get("stocks") or []):
        us_cost_krw = sum(
            float(stock.get("avg_price", 0) or 0) * float(stock.get("qty", 0) or 0) * usd_krw
            for stock in (us.get("stocks") or [])
        )
        if us_cost_krw > 0:
            kr_cash_effective = max(kr_cash - us_cost_krw, 0.0)
            source = "broker+paper_us_cash_estimated"

    cumulative = kr_cash_effective + kr_eval + us_cash_krw + us_eval_krw

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
        "source": source,
        "usd_krw": usd_krw,
        "kr_cash": kr_cash,
        "kr_cash_effective": kr_cash_effective,
        "kr_orderable_cash": kr_orderable,
        "kr_eval": kr_eval,
        "us_cash_usd": us_cash_usd,
        "us_cash_krw": us_cash_krw,
        "us_orderable_cash_usd": us_orderable_cash_usd,
        "us_orderable_cash_krw": us_orderable_cash_krw,
        "us_eval_usd": us_eval_usd,
        "us_eval_krw": us_eval_krw,
        "cumulative": cumulative,
        "unrealized_krw": {
            "KR": _unrealized_krw(kr.get("stocks", []), "KR"),
            "US": _unrealized_krw(us.get("stocks", []), "US"),
        },
    }


def _market_asset_krw_from_broker_snapshot(broker: dict, market: str) -> float:
    broker = broker or {}
    if market == "US":
        return float(broker.get("us_cash_krw", 0) or 0) + float(broker.get("us_eval_krw", 0) or 0)
    return float(broker.get("kr_cash_effective", broker.get("kr_cash", 0)) or 0) + float(broker.get("kr_eval", 0) or 0)


def _persist_broker_equity_snapshot(broker: dict, mode: str = "paper") -> None:
    if not broker:
        return
    path = _broker_equity_history_path(mode)
    today_kr = _session_trade_date("KR").isoformat()
    today_us = _session_trade_date("US").isoformat()
    updates = {
        ("KR", today_kr): {
            "market": "KR",
            "date": today_kr,
            "asset_krw": round(_market_asset_krw_from_broker_snapshot(broker, "KR"), 6),
            "unrealized_krw": round(float((broker.get("unrealized_krw", {}) or {}).get("KR", 0) or 0), 6),
            "source": str(broker.get("source", "broker") or "broker"),
            "captured_at": datetime.now(KST).isoformat(),
        },
        ("US", today_us): {
            "market": "US",
            "date": today_us,
            "asset_krw": round(_market_asset_krw_from_broker_snapshot(broker, "US"), 6),
            "unrealized_krw": round(float((broker.get("unrealized_krw", {}) or {}).get("US", 0) or 0), 6),
            "source": str(broker.get("source", "broker") or "broker"),
            "captured_at": datetime.now(KST).isoformat(),
        },
    }
    rows_by_key: dict[tuple[str, str], dict] = {}
    if path.exists():
        try:
            for raw in path.read_text(encoding="utf-8").splitlines():
                raw = raw.strip()
                if not raw:
                    continue
                item = json.loads(raw)
                key = (str(item.get("market", "") or "").upper(), str(item.get("date", "") or ""))
                if key[0] and key[1]:
                    rows_by_key[key] = item
        except Exception:
            rows_by_key = {}
    rows_by_key.update(updates)
    ordered = sorted(rows_by_key.values(), key=lambda r: (str(r.get("market", "")), str(r.get("date", ""))))
    path.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in ordered) + ("\n" if ordered else ""),
        encoding="utf-8",
    )


def _load_broker_equity_snapshots(market: str, period: str, start: str, end: str, mode: str = "paper") -> list[dict]:
    path = _broker_equity_history_path(mode)
    if not path.exists():
        return []
    d_start, d_end = _resolve_period_bounds(period, start, end)
    rows = []
    try:
        for raw in path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            item = json.loads(raw)
            if str(item.get("market", "") or "").upper() != market:
                continue
            d = _parse_date(str(item.get("date", "") or ""))
            if d_start <= d <= d_end:
                rows.append(item)
    except Exception:
        return []
    rows.sort(key=lambda r: str(r.get("date", "")))
    return rows


def _broker_realized_pnl_krw(market: str, trade_date: str, mode: str = "paper") -> float:
    """브로커 체결 원장 기준 실현손익을 FIFO로 계산한다."""
    cache_key = (_normalize_mode(mode), market, trade_date)
    now_ts = _time.time()
    cached = _BROKER_REALIZED_CACHE.get(cache_key)
    if cached and now_ts - float(cached.get("ts", 0) or 0) < 60:
        return float(cached.get("value", 0) or 0)

    realized = 0.0
    usd_krw = _get_usd_krw_cached()
    try:
        with _kis_runtime(mode):
            token = get_access_token()
            if market == "KR":
                rows = inquire_daily_ccld_kr(token, start_date=trade_date, end_date=trade_date)
            else:
                rows = inquire_ccnl_us(token, start_date=trade_date, end_date=trade_date)
    except Exception:
        rows = []

    try:
        from collections import defaultdict, deque

        queues = defaultdict(deque)
        rows = sorted(rows, key=lambda r: str(r.get("order_time", "") or ""))
        for row in rows:
            ticker = str(row.get("ticker", "") or "").upper() if market == "US" else str(row.get("ticker", "") or "")
            side = str(row.get("side", "") or "").lower()
            qty = int(row.get("filled_qty", 0) or 0)
            px = float(row.get("fill_price", 0) or 0)
            if not ticker or qty <= 0 or px <= 0:
                continue
            if side == "buy":
                queues[ticker].append([qty, px])
                continue
            if side != "sell":
                continue
            remaining = qty
            while remaining > 0 and queues[ticker]:
                buy_qty, buy_px = queues[ticker][0]
                take = min(remaining, buy_qty)
                pnl_native = (px - buy_px) * take
                realized += pnl_native if market == "KR" else pnl_native * usd_krw
                buy_qty -= take
                remaining -= take
                if buy_qty <= 0:
                    queues[ticker].popleft()
                else:
                    queues[ticker][0][0] = buy_qty
    except Exception:
        realized = 0.0

    _BROKER_REALIZED_CACHE[cache_key] = {"ts": now_ts, "value": realized}
    return float(realized)


def _live_asset_fallback(market: str, usd_krw: float) -> dict:
    live = _load_live_status(market, mode=_request_mode())
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


def _ticker_name_map(market: str, include_broker: bool = True, mode: str = "paper") -> dict[str, str]:
    name_map: dict[str, str] = {}
    if market == "KR":
        for t, name in (_KR_TICKERS_STATIC or {}).items():
            t = str(t or "").strip().upper()
            name = str(name or "").strip()
            if t and name and name != t:
                name_map[t] = name
    rec = load_today(market)
    digest_raw = (rec or {}).get("digest_raw") or {}
    technicals = digest_raw.get("technicals") or {}
    for ticker, info in technicals.items():
        t = str(ticker or "").strip().upper()
        name = str((info or {}).get("name", "") or "").strip()
        if t and name and name != t:
            name_map[t] = name
    digest_dirs = [DIGEST_DIR, LOG_DIR]
    suffix = f"_{market}.json"
    for d in digest_dirs:
        try:
            paths = sorted(d.glob(f"*{suffix}"), reverse=True)[:10]
        except Exception:
            paths = []
        for path in paths:
            try:
                rec = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            technicals = ((rec.get("digest_raw") or rec).get("technicals") or {})
            for ticker, info in technicals.items():
                t = str(ticker or "").strip().upper()
                name = str((info or {}).get("name", "") or "").strip()
                if t and name and name != t:
                    name_map[t] = name
    for t, name in _screen_cache_name_map(market).items():
        if t and name and name != t:
            name_map[t] = name
    if include_broker:
        for pos in (_load_broker_positions(market, mode=mode) or []):
            t = str(pos.get("ticker", "") or "").strip().upper()
            name = str(pos.get("name", "") or "").strip()
            if t and name and name != t:
                name_map[t] = name
    live = _load_live_status(market, mode=_request_mode()) or {}
    for bucket in ("positions", "pending_orders"):
        for item in live.get(bucket, []) or []:
            t = str(item.get("ticker", "") or "").strip().upper()
            name = str(item.get("name", "") or "").strip()
            if t and name and name != t:
                name_map[t] = name
    for t, name in _ticker_name_overrides(market).items():
        if t and name and name != t:
            name_map[t] = name
    return name_map


def _screen_cache_name_map(market: str) -> dict[str, str]:
    market_key = str(market or "").upper()
    filename = "us_screen_cache.json" if market_key == "US" else "kr_screen_cache.json"
    path = get_runtime_path("state", filename, make_parents=False)
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    rows = data.get("candidates") if isinstance(data, dict) else []
    if not isinstance(rows, list):
        return {}
    out: dict[str, str] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        ticker = str(row.get("ticker") or "").strip().upper()
        name = _clean_ticker_name(row.get("name"))
        if ticker and name and name.upper() != ticker:
            out[ticker] = name
    return out


def _clean_ticker_name(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        repaired = text.encode("cp949").decode("utf-8")
    except Exception:
        return text
    return repaired if _hangul_score(repaired) > _hangul_score(text) else text


def _hangul_score(value: str) -> int:
    return sum(1 for ch in str(value or "") if "\uac00" <= ch <= "\ud7a3")


def _ticker_name_overrides(market: str) -> dict[str, str]:
    market_key = str(market or "").upper()
    paths = [
        BASE_DIR / "config" / "ticker_name_overrides.json",
        get_runtime_path("state", "ticker_name_overrides.json", make_parents=False),
    ]
    merged: dict[str, str] = {}
    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        scoped = data.get(market_key) if isinstance(data, dict) else {}
        if not isinstance(scoped, dict):
            continue
        for ticker, name in scoped.items():
            t = str(ticker or "").strip().upper()
            n = str(name or "").strip()
            if t and n:
                merged[t] = n
    return merged


def _display_ticker_label(ticker: str, name: str = "", name_map: Optional[dict] = None) -> str:
    raw_ticker = str(ticker or "").strip().upper()
    raw_name = str(name or "").strip()
    mapped = str(((name_map or {}).get(raw_ticker, "") if raw_ticker else "") or "").strip()
    final_name = raw_name or mapped
    if raw_ticker and final_name and final_name != raw_ticker:
        return f"{final_name} ({raw_ticker})"
    return raw_ticker or final_name or "-"


_EXCHANGE_CAL_MAP = {"KR": "XKRX", "US": "XNYS"}
_STRATEGY_KO_MAP = {
    "volatility_breakout": "변동성돌파",
    "opening_range_pullback": "OR눌림",
    "gap_pullback": "갭눌림",
    "momentum": "모멘텀",
    "mean_reversion": "평균회귀",
    "broker_sync": "브로커동기화",
    "변동성돌파": "변동성돌파",
    "OR눌림": "OR눌림",
    "갭눌림": "갭눌림",
    "모멘텀": "모멘텀",
    "평균회귀": "평균회귀",
    "브로커동기화": "브로커동기화",
}


def _ko_strategy_name(name: str) -> str:
    return _STRATEGY_KO_MAP.get((name or "").strip(), (name or "").strip())


_MODE_KO_MAP = {
    "AGGRESSIVE": "공격매수",
    "MODERATE_BULL": "강한상승",
    "Bull_Confirmed": "상승확인",
    "MILD_BULL": "완만상승",
    "CAUTIOUS": "중립경계",
    "NEUTRAL": "중립",
    "MILD_BEAR": "완만하락",
    "CAUTIOUS_BEAR": "신중약세",
    "DEFENSIVE": "방어",
    "HALT": "거래중지",
    "SIDEWAYS_BEAR": "횡보약세",
    "SIDEWAYS_BULL": "횡보강세",
}


def _ko_mode_name(name: str) -> str:
    return _MODE_KO_MAP.get((name or "").strip(), (name or "").strip() or "-")


_EVENT_KO_MAP = {
    "waiting": "대기중",
    "signal_check": "신호없음",
    "entry_signal": "진입신호",
    "entry_skip": "진입보류",
    "signal_blocked": "신호차단",
    "buy_filled": "매수체결",
    "sell_filled": "매도체결",
    "trailing": "추적중",
    "cycle_error": "처리오류",
}


def _ko_event_name(name: str) -> str:
    return _EVENT_KO_MAP.get((name or "").strip(), (name or "").strip() or "대기중")


def _normalize_percent_value(value: float) -> float:
    value = float(value or 0.0)
    if value != 0 and abs(value) < 0.1:
        return value * 100.0
    return value


def _session_trade_date(market: str, now_dt=None):
    return resolve_session_date(market, now_dt)


def _extract_none_reason_tags(detail: str) -> list[str]:
    detail = str(detail or "")
    if not detail:
        return []
    tags = []
    if "거래량부족" in detail or re.search(r"vol=.*✗", detail):
        tags.append("거래량 부족")
    if "RSI부족" in detail or re.search(r"RSI=.*✗", detail):
        tags.append("RSI 부족")
    if "BB부족" in detail or re.search(r"BB=.*✗", detail):
        tags.append("BB 부족")
    if "돌파부족" in detail or re.search(r"돌파=.*✗", detail):
        tags.append("돌파 부족")
    if "MACD부족" in detail:
        tags.append("MACD 부족")
    if "신고가부족" in detail:
        tags.append("신고가 부족")
    if "추세부족" in detail:
        tags.append("추세 부족")
    if "갭부족" in detail or re.search(r"갭=.*✗", detail):
        tags.append("갭 부족")
    return tags[:2]


def _today_signal_digest(market: str, selected_count: int = 0, universe_count: int = 0) -> dict:
    today = _market_log_date_str(market)
    log_path = BASE_DIR / "logs" / "analysis" / f"analysis_{today}.jsonl"
    summary = {
        "candidates": int(universe_count or 0),
        "selected": int(selected_count or 0),
        "buy_signal": 0,
        "no_signal": 0,
        "blocked": 0,
        "failed": 0,
        "top_none": [],
    }
    if not log_path.exists():
        return summary
    none_counter = Counter()
    latest_candidates = 0
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return summary
    for line in lines:
        try:
            rec = json.loads(line)
        except Exception:
            continue
        extra = rec.get("extra", {})
        if "extra" in extra:
            extra = extra["extra"]
        if extra.get("market", "") != market:
            continue
        ev = extra.get("event", "")
        if ev == "screen_candidates":
            latest_candidates = len(extra.get("tickers", []) or [])
        elif ev == "entry_signal":
            summary["buy_signal"] += 1
        elif ev == "signal_check" and extra.get("signal", "") == "none":
            summary["no_signal"] += 1
            for tag in _extract_none_reason_tags(extra.get("detail", "")):
                none_counter[tag] += 1
        elif ev in ("entry_skip", "signal_blocked"):
            summary["blocked"] += 1
        elif ev == "entry_failed":
            summary["failed"] += 1
    if latest_candidates > 0:
        summary["candidates"] = latest_candidates
    summary["top_none"] = [tag for tag, _ in none_counter.most_common(3)]
    return summary


def _count_today_entries(mode: str, market: str) -> int:
    """당일 매수 체결 건수.
    live 모드: 브로커 truth 스냅샷의 today_fills(side=buy) 기준.
              KR = KST 당일, US = KST 06:00 전이면 전날(ET 당일) 세션.
    paper 모드: decisions.jsonl 기준 (session_date 일치).
    """
    mkt = str(market or "").upper()
    if mode == "live":
        try:
            snap_path = get_runtime_path("state", "live_broker_truth_snapshot.json")
            if not snap_path.exists():
                snap_path = get_runtime_path("state", "broker_truth_snapshot.json")
            if snap_path.exists():
                snap = json.loads(snap_path.read_text(encoding="utf-8", errors="replace"))
                fills = (snap.get("markets") or {}).get(mkt, {}).get("today_fills", [])
                return sum(1 for f in fills if str(f.get("side", "")).lower() == "buy")
        except Exception:
            pass
        return 0
    # paper: decisions.jsonl 기준
    session_date = _session_trade_date(mkt).isoformat()
    path = get_runtime_path("state", f"{mode}_decisions.jsonl")
    if not path.exists():
        return 0
    count = 0
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                ev = json.loads(line)
                if ev.get("type") != "entry":
                    continue
                sd = str(ev.get("session_date", "") or ev.get("timestamp", "")[:10])
                if sd != session_date:
                    continue
                if mkt and ev.get("market", "").upper() != mkt:
                    continue
                count += 1
            except Exception:
                continue
    except Exception:
        pass
    return count


def _ml_db_digest(market: str) -> dict:
    db_path = BASE_DIR / "data" / "ml" / "decisions.db"
    digest = {
        "enabled": False,
        "exists": db_path.exists(),
        "total": 0,
        "today": 0,
        "buy_signal": 0,
        "filled": 0,
        "with_outcome": 0,
        "forward_ready": 0,
        "forward_ready_rate": 0.0,
        "last_ts": "",
        "recent_days": [],
    }
    if not db_path.exists():
        return digest
    try:
        import sqlite3
        session_date = _session_trade_date(market).isoformat()
        with sqlite3.connect(db_path) as conn:
            digest["enabled"] = True
            digest["total"] = int(conn.execute("SELECT COUNT(*) FROM decisions WHERE market=?", (market,)).fetchone()[0] or 0)
            digest["today"] = int(conn.execute("SELECT COUNT(*) FROM decisions WHERE market=? AND session_date=?", (market, session_date)).fetchone()[0] or 0)
            digest["buy_signal"] = int(conn.execute("SELECT COUNT(*) FROM decisions WHERE market=? AND session_date=? AND decision='BUY_SIGNAL'", (market, session_date)).fetchone()[0] or 0)
            digest["filled"] = int(conn.execute("SELECT COUNT(*) FROM decisions WHERE market=? AND session_date=? AND filled=1", (market, session_date)).fetchone()[0] or 0)
            digest["with_outcome"] = int(conn.execute("SELECT COUNT(*) FROM decisions WHERE market=? AND pnl_pct IS NOT NULL", (market,)).fetchone()[0] or 0)
            digest["forward_ready"] = int(conn.execute("SELECT COUNT(*) FROM decisions WHERE market=? AND forward_1d IS NOT NULL", (market,)).fetchone()[0] or 0)
            if digest["total"] > 0:
                digest["forward_ready_rate"] = round((digest["forward_ready"] / digest["total"]) * 100.0, 1)
            row = conn.execute("SELECT ts FROM decisions WHERE market=? ORDER BY id DESC LIMIT 1", (market,)).fetchone()
            digest["last_ts"] = str(row[0]) if row and row[0] else ""
            day_rows = conn.execute(
                """
                SELECT session_date, COUNT(*) as n
                FROM decisions
                WHERE market=?
                GROUP BY session_date
                ORDER BY session_date DESC
                LIMIT 7
                """,
                (market,),
            ).fetchall()
            digest["recent_days"] = [
                {"date": str(r[0]), "count": int(r[1] or 0)}
                for r in reversed(day_rows)
            ]
    except Exception:
        pass
    return digest


def _adaptive_param_digest(market: str, mode: str, context: Optional[dict] = None) -> dict:
    digest = {"enabled": False, "rows": []}
    if _adaptive_params is None or _adaptive_perf_stats is None:
        return digest

    context = context or {}
    conf = 0.6
    modules = {
        "gap_pullback": _gap_strategy,
        "mean_reversion": _mr_strategy,
        "momentum": _mom_strategy,
        "volatility_breakout": _vb_strategy,
    }

    def _short(p: dict) -> str:
        if not p:
            return "-"
        keys = (
            "gap_min",
            "vol_mult",
            "rsi_thr",
            "bb_thr",
            "ma60_thr",
            "k",
        )
        parts = []
        for key in keys:
            if key not in p:
                continue
            val = p.get(key)
            label = {
                "gap_min": "gap",
                "vol_mult": "vol",
                "rsi_thr": "rsi",
                "bb_thr": "bb",
                "ma60_thr": "ma60",
                "k": "k",
            }.get(key, key)
            if isinstance(val, float):
                if key == "gap_min":
                    parts.append(f"{label}={val:.3f}")
                elif key == "ma60_thr":
                    parts.append(f"{label}={val:.2f}")
                elif key == "k":
                    parts.append(f"{label}={val:.2f}")
                else:
                    parts.append(f"{label}={val:.1f}")
            else:
                parts.append(f"{label}={val}")
        if p.get("disabled"):
            parts.append("disabled")
        return " ".join(parts) if parts else "-"

    def _regime_reasons(strategy: str, base_p: dict, final_p: dict, ctx: dict) -> list[str]:
        reasons = []
        vix = ctx.get("vix")
        usd_krw = ctx.get("usd_krw")
        if vix is not None:
            try:
                vix_val = float(vix)
                if vix_val > 35:
                    reasons.append(f"VIX {vix_val:.1f} 고변동")
                elif vix_val > 25:
                    reasons.append(f"VIX {vix_val:.1f} 경계")
            except Exception:
                pass
        if market == "KR" and usd_krw is not None:
            try:
                fx = float(usd_krw)
                if fx > 1400 and (
                    base_p.get("gap_min") != final_p.get("gap_min")
                    or base_p.get("vol_mult") != final_p.get("vol_mult")
                ):
                    reasons.append(f"환율 {fx:.0f}원 경계")
            except Exception:
                pass
        return reasons

    def _perf_reason(perf: dict, final_p: dict) -> str:
        source = perf.get("source", "none")
        wr = perf.get("win_rate")
        n = int(perf.get("n", 0) or 0)
        if wr is None:
            return f"{source} 표본 없음"
        base = f"{source} WR {float(wr):.1f}% n={n}"
        if final_p.get("size_hint") == "up":
            return base + " · size up"
        if float(wr) < 35:
            return base + " · 강한 보수화"
        if float(wr) < 43:
            return base + " · 소폭 보수화"
        return base

    # 오늘 Claude 검토 결과 조회 (param_sessions 테이블)
    claude_rows: dict[str, dict] = {}
    try:
        if _param_tuner_mod is not None:
            import sqlite3 as _sq
            _db = _param_tuner_mod._DB_PATH
            if _db.exists():
                with _sq.connect(str(_db), timeout=3) as _conn:
                    _today = __import__("datetime").date.today().isoformat()
                    _rs = _conn.execute(
                        """
                        SELECT strategy, base_params, claude_params, claude_reason,
                               was_adjusted, trigger
                        FROM param_sessions
                        WHERE market = ? AND session_date = ?
                        ORDER BY id DESC
                        """,
                        (market, _today),
                    ).fetchall()
                    # 전략별 최신 1건만 사용
                    _seen: set[str] = set()
                    for _strat, _bp, _cp, _reason, _adj, _trg in _rs:
                        if _strat in _seen:
                            continue
                        _seen.add(_strat)
                        try:
                            claude_rows[_strat] = {
                                "base_p":  json.loads(_bp or "{}"),
                                "claude_p": json.loads(_cp or "{}"),
                                "reason":  _reason or "",
                                "adjusted": bool(_adj),
                                "trigger":  _trg or "",
                            }
                        except Exception:
                            pass
    except Exception:
        pass

    try:
        for strategy, module in modules.items():
            if module is None:
                continue
            if strategy == "volatility_breakout":
                base_p = module.params(mode, conf=conf, market=market)
            else:
                base_p = module.params(mode, conf, market=market)
            final_p = _adaptive_params(strategy, market, mode=mode, conf=conf, context=context)
            perf = _adaptive_perf_stats(strategy, market)
            changed = []
            for key in ("gap_min", "vol_mult", "rsi_thr", "bb_thr", "ma60_thr", "k"):
                if key in base_p and key in final_p and base_p.get(key) != final_p.get(key):
                    changed.append(key)

            # Claude 검토 레이어 데이터
            cr = claude_rows.get(strategy, {})
            claude_p = cr.get("claude_p", {})
            claude_reason = cr.get("reason", "")
            claude_trigger = cr.get("trigger", "")
            claude_adjusted = cr.get("adjusted", False)
            # adaptive→claude 변경된 키
            claude_changed = []
            _cmp_keys = ("gap_min", "vol_mult", "rsi_thr", "bb_thr",
                         "tp_pct", "sl_pct", "size_mult", "max_hold")
            for key in _cmp_keys:
                if key in final_p and key in claude_p:
                    fv = round(float(final_p[key]), 4)
                    cv = round(float(claude_p[key]), 4)
                    if fv != cv:
                        claude_changed.append(key)

            digest["rows"].append({
                "strategy": strategy,
                "base": _short(base_p),
                "final": _short(final_p),
                "changed": changed,
                "regime_reasons": _regime_reasons(strategy, base_p, final_p, context),
                "perf_reason": _perf_reason(perf, final_p),
                "perf_source": perf.get("source", "none"),
                "perf_wr": perf.get("win_rate"),
                "perf_n": perf.get("n", 0),
                "disabled": bool(final_p.get("disabled")),
                # Claude 4th layer
                "claude": _short(claude_p) if claude_p else None,
                "claude_changed": claude_changed,
                "claude_reason": claude_reason,
                "claude_trigger": claude_trigger,
                "claude_adjusted": claude_adjusted,
            })
        digest["enabled"] = bool(digest["rows"])
    except Exception:
        return {"enabled": False, "rows": []}
    return digest


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
    selection_status = str(item.get("selection_status", "") or "").strip().upper()
    if held_qty > 0 and pending_count > 0:
        return f"{mode_ko} 환경에서 보유 종목 추적 중 · 미체결 주문 {pending_count}건 관리"
    if held_qty > 0:
        return f"{mode_ko} 환경에서 보유 종목 추적 관리 중"
    if pending_count > 0:
        return f"{mode_ko} 환경에서 미체결 주문 추적 중"
    if selection_status == "WATCH_ONLY":
        return f"{mode_ko} 환경에서 감시 후보 유지 · 실제 매수 후보 아님"
    if last_event == "signal_check":
        return f"{mode_ko} 환경에서 스크리너 통과 종목 · 신호 대기"
    return f"{mode_ko} 환경에서 스크리너 통과 종목"


def _selection_meta_lookup(values: dict, ticker: str, market: str):
    if not isinstance(values, dict):
        return None
    lookup = str(ticker).upper() if market == "US" else str(ticker)
    if lookup in values:
        return values.get(lookup)
    if market == "US":
        for raw_key, raw_value in values.items():
            if str(raw_key).upper() == lookup:
                return raw_value
    return None


def _selection_status_for_ticker(rec: dict, ticker: str, market: str) -> str:
    meta = rec.get("selection_meta", {}) or {}
    trade_ready = rec.get("trade_ready_tickers") or meta.get("trade_ready") or []
    lookup = str(ticker).upper() if market == "US" else str(ticker)
    trade_ready_set = {
        str(item).upper() if market == "US" else str(item)
        for item in trade_ready
        if str(item).strip()
    }
    return "TRADE_READY" if lookup in trade_ready_set else "WATCH_ONLY"


def _resolve_ticker_select_reason(
    ticker: str,
    market: str,
    mode: str,
    item: dict,
    rec: dict,
    base_reason: str = "",
    watch_only_detail: str = "",
) -> str:
    for candidate in (base_reason, watch_only_detail):
        text = str(candidate or "").strip()
        if text:
            return text

    meta = rec.get("selection_meta", {}) or {}
    runtime_filtered = meta.get("_runtime_filtered_trade_ready") or {}
    runtime_filtered_reason = str(_selection_meta_lookup(runtime_filtered, ticker, market) or "").strip()
    if runtime_filtered_reason:
        slot_name = runtime_filtered_reason.split(":", 1)[1] if ":" in runtime_filtered_reason else ""
        slot_label_map = {
            "momentum": "모멘텀",
            "gap_pullback": "갭눌림",
            "opening_range_pullback": "OR 눌림",
            "mean_reversion": "평균회귀",
            "continuation": "연속진입",
            "__unassigned__": "공통",
        }
        slot_label = slot_label_map.get(slot_name, slot_name or "전략")
        if runtime_filtered_reason == "continuation_shadow_only":
            return "TRADE_READY 제외 · 연속진입은 실전 비활성, shadow 관찰만 유지"
        if runtime_filtered_reason.startswith("slot_disabled:"):
            return f"TRADE_READY 제외 · {slot_label} 슬롯 비활성"
        if runtime_filtered_reason.startswith("slot_cap:"):
            return f"TRADE_READY 제외 · {slot_label} 슬롯 한도 도달"
        if runtime_filtered_reason == "total_cap_reached":
            return "TRADE_READY 제외 · 전체 TRADE_READY 한도 도달"

    veto_reason = str(_selection_meta_lookup(meta.get("veto", {}), ticker, market) or "").strip()
    if veto_reason:
        return f"TRADE_READY 제외 · {veto_reason}"

    trade_ready = rec.get("trade_ready_tickers") or meta.get("trade_ready") or []
    if str(item.get("selection_status", "") or "").upper() == "WATCH_ONLY" and not trade_ready:
        return "trade_ready 비어 있음 · 개별 사유 미기록"

    return _fallback_select_reason(ticker, market, mode, item)


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

    start_re = None
    done_re = None
    close_re = re.compile(r"\[[a-zA-Z_]+\]\s+(?P<ticker>[A-Z0-9]+)\s+(?P<pnl>[+\-]?[0-9,]+(?:\.[0-9]+)?)\s+\((?P<pnl_pct>[+\-]?[0-9.]+)%\)")
    start_marker = f"[긴급 재판단 시작] {market} |"
    done_marker = "[긴급 재판단 완료]"

    events = []
    pending_ts = None
    for line in lines:
        ts = line[:19]
        if start_marker in line:
            pending_ts = ts
            continue
        m = None
        if done_marker in line:
            m = re.search(r"\]\s+(?P<old>\S+)\s+→\s+(?P<new>\S+)\s+size=", line)
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

    for idx, event in enumerate(events):
        start_ts = event["ts"]
        end_ts = events[idx + 1]["ts"] if idx + 1 < len(events) else "9999-99-99 99:99:99"
        event["tickers"] = set()

        day_key = start_ts[:10].replace("-", "")
        rec_path = LOG_DIR / f"{day_key}_{market}.json"
        rec = {}
        if rec_path.exists():
            try:
                rec = json.loads(rec_path.read_text(encoding="utf-8"))
            except Exception:
                rec = {}
        decision_events = list(rec.get("decision_events", []) or [])
        if decision_events:
            for de in decision_events:
                ts = str(de.get("timestamp", "") or "")[:19]
                if not ts or ts < start_ts or ts >= end_ts:
                    continue
                if str(de.get("action", "") or "") != "sell_filled":
                    continue
                strategy = str(de.get("strategy", "") or "")
                reason = str(de.get("reason", "") or "")
                if strategy in ("broker_sync", "broker_balance", ""):
                    continue
                if reason == "session_close":
                    continue
                ticker = str(de.get("ticker", "") or "").strip().upper()
                if ticker and _ticker_market(ticker) != market:
                    continue
                if ticker:
                    event["tickers"].add(ticker)
                event["pnl_krw"] += float(de.get("pnl_krw", 0) or 0)
        else:
            event_idx = idx
            for line in lines:
                ts = line[:19]
                if ts < start_ts or ts >= end_ts:
                    continue
                while event_idx + 1 < len(events) and ts >= events[event_idx + 1]["ts"]:
                    event_idx += 1
                if event_idx != idx:
                    continue
                m = close_re.search(line)
                if not m:
                    continue
                ticker = m.group("ticker").upper()
                if _ticker_market(ticker) != market:
                    continue
                event["tickers"].add(ticker)
                event["pnl_krw"] += float(m.group("pnl").replace(",", ""))

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

    mode        = _request_mode()
    today_rec   = load_today(market)
    result      = today_rec.get("actual_result", {})
    raw_live    = _load_live_status(market, mode=mode)   # 장중/최근 실시간 상태
    live        = raw_live

    # 장중이면 라이브 상태를 우선 사용하고, 없으면 일별 기록을 쓴다
    if not _is_fresh_live_status(live, today_rec):
        live = {}

    metrics_today = _record_metrics(today_rec, market)
    broker_trade_date = _session_trade_date(market).strftime("%Y%m%d")
    broker_realized_pnl_krw = _broker_realized_pnl_krw(market, broker_trade_date, mode=mode)
    realized_pnl_krw = broker_realized_pnl_krw
    if abs(float(realized_pnl_krw or 0)) < 1e-9:
        realized_pnl_krw = live.get("daily_pnl", metrics_today.get("pnl_krw", result.get("pnl_krw", 0)))
    unrealized_pnl_krw = 0.0
    pnl_krw  = realized_pnl_krw
    pnl_pct  = live.get("daily_pnl_pct", result.get("pnl_pct", 0))
    cum_base = float(result.get("cumulative", 0) or PAPER_CASH)
    cum_asset = float((live.get("total_equity", 0) if live else 0) or cum_base or PAPER_CASH)
    _raw_broker = _load_broker_positions(market, mode=mode)
    broker_ok = _raw_broker is not None
    broker_positions = _filter_items_for_market(_raw_broker or [], market)
    position_context = (
        _live_position_context_for_market(market, mode=mode)
        if _is_live_mode(mode)
        else _saved_positions_for_market(market, mode=mode)
    )
    positions = _merge_positions_for_display(market, broker_positions, position_context, broker_ok=broker_ok)
    pending_orders = _filter_items_for_market(live.get("pending_orders", []) if live else [], market)
    name_map = _ticker_name_map(market, mode=mode)
    for pos in positions:
        pos["display_ticker"] = _display_ticker_label(pos.get("ticker", ""), pos.get("name", ""), name_map)
    for order in pending_orders:
        order["display_ticker"] = _display_ticker_label(order.get("ticker", ""), order.get("name", ""), name_map)
    broker = _broker_snapshot(mode=mode)
    if broker:
        _persist_broker_equity_snapshot(broker, mode=mode)
    kr_asset = 0.0
    us_asset = 0.0
    usd_krw = float(os.getenv("USD_KRW_RATE", "1350") or 1350)
    asset_source = "internal_fallback"
    engine_equity_krw = float((live.get("total_equity", 0) if live else 0) or 0)
    engine_cash_krw = float((live.get("cash", 0) if live else 0) or 0)

    if broker:
        usd_krw = float(broker.get("usd_krw", 0) or usd_krw)
        kr_asset = float(broker.get("kr_cash_effective", broker.get("kr_cash", 0)) or 0) + float(broker.get("kr_eval", 0) or 0)
        us_asset = float(broker.get("us_cash_krw", 0) or 0) + float(broker.get("us_eval_krw", 0) or 0)
        market_unrealized = float(broker.get("unrealized_krw", {}).get(market, 0) or 0)
        asset_source = str(broker.get("source", "broker") or "broker")
        if market == "US" and us_asset <= 0:
            fallback = _live_asset_fallback("US", usd_krw or float(os.getenv("USD_KRW_RATE", "1350") or 1350))
            us_asset = float(fallback.get("asset_krw", 0) or 0)
            market_unrealized = float(fallback.get("unrealized_krw", 0) or 0)
            asset_source = asset_source + "+live_fallback"
        cum_asset = float(kr_asset + us_asset)
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
    signal_digest = _today_signal_digest(
        market,
        selected_count=len(today_rec.get("tickers", []) or []),
        universe_count=len(today_rec.get("universe_tickers", []) or []),
    )
    ml_digest = _ml_db_digest(market)
    adaptive_digest = _adaptive_param_digest(
        market,
        live.get("mode") or today_rec.get("consensus", {}).get("mode", "-"),
        context=(today_rec.get("digest_raw", {}) or {}).get("context", {}) or {},
    )
    mode_size_pct = int((live.get("mode_size_pct", 0) if live else 0) or (today_rec.get("consensus", {}).get("size", 0) or 0))
    max_order_krw = float((live.get("max_order_krw", 0) if live else 0) or 500000)
    mode_order_limit_krw = round(max_order_krw * (mode_size_pct / 100.0), 0) if mode_size_pct > 0 else 0

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
    risk = _current_risk_snapshot(market, today_rec)
    broker_status = raw_live.get("broker", {}) if isinstance(raw_live.get("broker", {}), dict) else {}
    state_alerts = [
        alert for alert in _STATE_FILE_ALERTS.values()
        if f"{mode}_" in str(alert.get("path", "")) or not str(alert.get("path", ""))
    ]

    return jsonify({
        "today": {
            "date":           summary_date,
                    "pnl_pct":        round(pnl_pct, 4),
            "pnl_krw":        round(pnl_krw, 0),
            "realized_pnl_krw": round(realized_pnl_krw, 0),
            "unrealized_pnl_krw": round(unrealized_pnl_krw, 0),
            "asset_source":   asset_source,
            "win":            metrics_today.get("win", result.get("win", False)),
            "trades":         metrics_today.get("trades", result.get("trades", 0)),
            "execution_contaminated": bool(metrics_today.get("execution_contaminated", False)),
            "execution_issues": metrics_today.get("execution_issues", []),
            "mode":           live.get("mode") or today_rec.get("consensus", {}).get("mode", "-"),
            "mode_size_pct":  mode_size_pct,
            "max_order_krw":  round(max_order_krw, 0),
            "mode_order_limit_krw": mode_order_limit_krw,
            "cumulative":     cum_asset,
            "asset_krw_kr":   round(kr_asset, 0),
            "asset_krw_us":   round(us_asset, 0),
            "asset_krw_cash": round(float((broker or {}).get("kr_cash_effective", (broker or {}).get("kr_cash", 0)) or 0), 0),
            "asset_krw_eval": round(float((broker or {}).get("kr_eval", 0) or 0), 0),
            "asset_usd_cash": round(float((broker or {}).get("us_cash_usd", 0) or 0), 2),
            "asset_usd_eval": round(float((broker or {}).get("us_eval_usd", 0) or 0), 2),
            "asset_usd_total": round(
                float((broker or {}).get("us_cash_usd", 0) or 0) + float((broker or {}).get("us_eval_usd", 0) or 0),
                2,
            ),
            "broker_cash_krw": round(float((broker or {}).get("kr_cash_effective", (broker or {}).get("kr_cash", 0)) or 0) + float((broker or {}).get("us_cash_krw", 0) or 0), 0),
            "broker_orderable_cash_kr": round(float((broker or {}).get("kr_orderable_cash", 0) or 0), 0),
            "broker_orderable_cash_usd": round(float((broker or {}).get("us_orderable_cash_usd", 0) or 0), 2),
            "broker_orderable_cash_us_krw": round(float((broker or {}).get("us_orderable_cash_krw", 0) or 0), 0),
            "broker_orderable_cash_krw": round(float((broker or {}).get("kr_orderable_cash", 0) or 0) + float((broker or {}).get("us_orderable_cash_krw", 0) or 0), 0),
            "engine_equity_krw": round(engine_equity_krw, 0),
            "engine_cash_krw": round(engine_cash_krw, 0),
            "usd_krw":        usd_krw,
            "positions":      positions,
            "position_count": len(positions),
            "position_limit": _get_env_int(mode, "KR_MAX_POSITIONS" if market == "KR" else "US_MAX_POSITIONS", MAX_POSITIONS),
            "position_remaining": max(_get_env_int(mode, "KR_MAX_POSITIONS" if market == "KR" else "US_MAX_POSITIONS", MAX_POSITIONS) - len(positions), 0),
            "pyramid_limit": MAX_PYRAMID,
            "entries_today": _count_today_entries(mode, market),
            "max_daily_entries": _get_env_int(mode, "V2_MAX_DAILY_ENTRIES", 20),
            "pending_orders": pending_orders,
            "pending_count":  len(pending_orders),
            "live_updated":   live.get("updated_at", ""),
            "state_alerts":   state_alerts,
            "broker_trust":   broker_status.get("trust_level", ""),
            "broker_last_ok_at": broker_status.get("last_ok_at", ""),
            "broker_last_error": broker_status.get("last_error", ""),
            "session":        _session_status(market),
            "risk_label":     risk.get("risk_label", ""),
            "risk_value":     risk.get("risk_value", ""),
            "risk_status":    risk.get("risk_status", "normal"),
            "signal_digest":  signal_digest,
            "ml_db":          ml_digest,
            "adaptive":       adaptive_digest,
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


@app.route("/api/v2/ops")
def api_v2_ops():
    if build_v2_ops_summary is None:
        return jsonify({"ok": False, "error": "V2 ops summary unavailable"}), 500
    market = str(request.args.get("market") or "").strip().upper() or None
    session_date = _session_trade_date(market).isoformat() if market in {"KR", "US"} else None
    mode = _request_mode()
    summary = build_v2_ops_summary(market=market, runtime_mode=mode, session_date=session_date)
    _enrich_bucket_monitor_tickers(summary, market=market, mode=mode)
    return jsonify(summary)


def _tail_lines(path: Path, max_lines: int = 600) -> list[str]:
    if not path.exists():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        return [line.rstrip("\n") for line in lines[-max_lines:]]
    except Exception:
        return []


def _alert_dates_for_market(market: str) -> list[str]:
    dates = [datetime.now(KST).strftime("%Y%m%d")]
    try:
        session_day = _session_trade_date(market).strftime("%Y%m%d")
        if session_day not in dates:
            dates.append(session_day)
    except Exception:
        pass
    return dates


def _parse_jsonl_alerts(path: Path, *, source: str, market: str, limit: int) -> list[dict]:
    alerts = []
    interesting = (
        "ORDER_UNKNOWN", "SAFETY_BLOCKED", "TIMING_EXPIRED", "broker_truth",
        "cycle error", "halt", "risk", "failed", "오류", "실패",
    )
    for raw in _tail_lines(path, limit * 4):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except Exception:
            continue
        level = str(row.get("level", "") or "").upper()
        message = str(row.get("message", "") or "")
        extra = row.get("extra") if isinstance(row.get("extra"), dict) else {}
        ticker = str(extra.get("ticker") or row.get("ticker") or "")
        row_market = str(extra.get("market") or row.get("market") or market or "")
        text = f"{level} {message} {json.dumps(extra, ensure_ascii=False)}"
        if level not in {"ERROR", "WARNING", "CRITICAL"} and not any(k.lower() in text.lower() for k in interesting):
            continue
        alerts.append({
            "timestamp": row.get("timestamp", ""),
            "level": level or "INFO",
            "source": source,
            "category": extra.get("event") or row.get("func") or path.stem,
            "market": row_market,
            "ticker": ticker,
            "message": message,
            "path": str(path),
        })
    return alerts[-limit:]


def _parse_text_log_alerts(path: Path, *, source: str, market: str, limit: int) -> list[dict]:
    alerts = []
    patterns = re.compile(
        r"ERROR|WARNING|CRITICAL|ORDER_UNKNOWN|SAFETY_BLOCKED|TIMING_EXPIRED|broker_truth|cycle error|halt|risk",
        re.IGNORECASE,
    )
    ts_re = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
    ticker_re = re.compile(r"\[(?P<ticker>[A-Z0-9.\-]+)\]")
    for line in _tail_lines(path, limit * 4):
        if not patterns.search(line):
            continue
        ts_match = ts_re.search(line)
        ticker_match = ticker_re.search(line)
        level = "ERROR" if "ERROR" in line.upper() else "WARNING" if "WARNING" in line.upper() else "INFO"
        alerts.append({
            "timestamp": ts_match.group("ts").replace(" ", "T") if ts_match else "",
            "level": level,
            "source": source,
            "category": path.stem,
            "market": market,
            "ticker": ticker_match.group("ticker") if ticker_match else "",
            "message": line[-1200:],
            "path": str(path),
        })
    return alerts[-limit:]


def _pathb_alerts_from_summary(market: str, mode: str, limit: int) -> list[dict]:
    if build_v2_ops_summary is None:
        return []
    try:
        session_date = _session_trade_date(market).isoformat() if market in {"KR", "US"} else None
        summary = build_v2_ops_summary(market=market, runtime_mode=mode, session_date=session_date)
    except Exception as exc:
        return [{
            "timestamp": datetime.now(KST).isoformat(timespec="seconds"),
            "level": "ERROR",
            "source": "pathb",
            "category": "v2_ops_summary",
            "market": market,
            "ticker": "",
            "message": f"V2 ops summary failed: {exc}",
            "path": "",
        }]
    alerts = []
    pathb = summary.get("path_b_live") if isinstance(summary.get("path_b_live"), dict) else {}
    for row in pathb.get("order_unknown", []) or []:
        alerts.append({
            "timestamp": row.get("updated_at") or row.get("created_at") or "",
            "level": "ERROR",
            "source": "pathb",
            "category": "ORDER_UNKNOWN",
            "market": row.get("market", market),
            "ticker": row.get("ticker", ""),
            "message": f"주문상태 불명: {row.get('order_unknown_resolution') or row.get('status') or ''} {row.get('path_run_id') or ''}".strip(),
            "path": "data/v2_event_store.db",
        })
    health = pathb.get("consistency_health") if isinstance(pathb.get("consistency_health"), dict) else {}
    for issue in health.get("issues", []) or []:
        alerts.append({
            "timestamp": datetime.now(KST).isoformat(timespec="seconds"),
            "level": "WARNING",
            "source": "pathb",
            "category": issue.get("code", "consistency"),
            "market": issue.get("market", market),
            "ticker": issue.get("ticker", ""),
            "message": json.dumps(issue, ensure_ascii=False),
            "path": "data/v2_event_store.db",
        })
    broker_truth = summary.get("broker_truth") if isinstance(summary.get("broker_truth"), dict) else {}
    for mkt, data in (broker_truth.get("markets") or {}).items():
        if market and mkt != market:
            continue
        if data.get("missing") or data.get("stale") or data.get("error"):
            alerts.append({
                "timestamp": broker_truth.get("generated_at", ""),
                "level": "WARNING",
                "source": "broker_truth",
                "category": "broker_truth",
                "market": mkt,
                "ticker": "",
                "message": f"계좌 조회 상태: missing={bool(data.get('missing'))}, stale={bool(data.get('stale'))}, error={data.get('error') or '-'}",
                "path": "state/live_broker_truth_snapshot.json",
            })
    return alerts[-limit:]


@app.route("/api/logs/alerts")
def api_logs_alerts():
    mode = _request_mode()
    market = str(request.args.get("market", best_market_with_data()) or "KR").upper()
    limit = min(int(request.args.get("limit", "200") or 200), 500)
    alerts: list[dict] = list(_STATE_FILE_ALERTS.values())
    dates = _alert_dates_for_market(market)
    for day in dates:
        error_jsonl = SYSTEM_LOG_DIR / f"{mode}_error_{day}.jsonl"
        alerts.extend(_parse_jsonl_alerts(error_jsonl, source="system_error", market=market, limit=limit))
        if not error_jsonl.exists():
            alerts.extend(_parse_text_log_alerts(SYSTEM_LOG_DIR / f"{mode}_error_{day}.log", source="system_error", market=market, limit=limit))
        alerts.extend(_parse_jsonl_alerts(SYSTEM_LOG_DIR / f"{mode}_trading_{day}.jsonl", source="trading", market=market, limit=limit))
        risk_jsonl = RISK_LOG_DIR / f"{mode}_risk_{day}.jsonl"
        alerts.extend(_parse_jsonl_alerts(risk_jsonl, source="risk", market=market, limit=limit))
        if not risk_jsonl.exists():
            alerts.extend(_parse_text_log_alerts(RISK_LOG_DIR / f"{mode}_risk_{day}.log", source="risk", market=market, limit=limit))
    alerts.extend(_pathb_alerts_from_summary(market, mode, limit))
    if market in {"KR", "US"}:
        alerts = [
            a for a in alerts
            if not str(a.get("market") or "").strip() or str(a.get("market")).upper() == market
        ]
    severity_rank = {"CRITICAL": 4, "ERROR": 3, "WARNING": 2, "INFO": 1}
    dedup = {}
    for alert in alerts:
        key = (
            str(alert.get("timestamp", "")),
            str(alert.get("source", "")),
            str(alert.get("category", "")),
            str(alert.get("ticker", "")),
            str(alert.get("message", ""))[:220],
        )
        dedup[key] = alert
    rows = sorted(
        dedup.values(),
        key=lambda a: (str(a.get("timestamp", "")), severity_rank.get(str(a.get("level", "")).upper(), 0)),
        reverse=True,
    )
    return jsonify({
        "ok": True,
        "mode": mode,
        "market": market,
        "count": len(rows[:limit]),
        "alerts": rows[:limit],
        "state_file_alerts": list(_STATE_FILE_ALERTS.values()),
    })


@app.route("/api/position/chart")
def api_position_chart():
    mode = _request_mode()
    market = str(request.args.get("market", best_market_with_data()) or "KR").upper()
    ticker = str(request.args.get("ticker", "") or "").strip()
    if market == "US":
        ticker = ticker.upper()
    if not ticker:
        return jsonify({"ok": False, "error": "ticker required", "labels": [], "prices": []}), 400
    session_date = request.args.get("session_date") or _session_trade_date(market).isoformat()
    labels: list[str] = []
    prices: list[float] = []
    stages: list[str] = []
    source = "intraday_strategy_log"
    db_path = BASE_DIR / "data" / "intraday_strategy_log.db"
    if db_path.exists():
        try:
            with sqlite3.connect(str(db_path)) as con:
                def _rows_for_session(session_key: str):
                    return con.execute(
                        """
                        SELECT ts, price, stage
                        FROM intraday_strategy_log
                        WHERE bot_mode=? AND session_date=? AND market=? AND UPPER(ticker)=? AND price IS NOT NULL AND price > 0
                        ORDER BY ts ASC
                        """,
                        (mode, session_key, market, ticker.upper()),
                    ).fetchall()
                rows = _rows_for_session(session_date)
                if not rows:
                    latest = con.execute(
                        """
                        SELECT session_date
                        FROM intraday_strategy_log
                        WHERE bot_mode=? AND market=? AND UPPER(ticker)=? AND price IS NOT NULL AND price > 0
                        ORDER BY session_date DESC
                        LIMIT 1
                        """,
                        (mode, market, ticker.upper()),
                    ).fetchone()
                    if latest and latest[0]:
                        session_date = str(latest[0])
                        rows = _rows_for_session(session_date)
            last_label = None
            for ts, price, stage in rows:
                label = str(ts or "")[11:16] or str(ts or "")[:16]
                if label == last_label and prices:
                    prices[-1] = float(price or 0)
                    stages[-1] = str(stage or "")
                    continue
                labels.append(label)
                prices.append(round(float(price or 0), 6))
                stages.append(str(stage or ""))
                last_label = label
        except Exception as exc:
            _record_state_file_alert(db_path, exc, category="position_chart")
            labels, prices, stages = [], [], []
    if len(prices) < 2:
        source = "position_fallback"
        pos = next(
            (
                p for p in _saved_positions_for_market(market, mode=mode)
                if (str(p.get("ticker", "") or "").upper() if market == "US" else str(p.get("ticker", "") or "")) == ticker
            ),
            {},
        )
        avg_price = float(pos.get("display_avg_price", pos.get("avg_price", pos.get("entry", 0))) or 0)
        cur_price = float(pos.get("display_current_price", pos.get("current_price", avg_price)) or 0)
        if market == "US":
            usd_krw = _get_usd_krw_cached()
            currency = str(pos.get("currency") or pos.get("display_currency") or "").upper()
            if currency != "USD" and usd_krw > 0:
                if avg_price > 5000:
                    avg_price = avg_price / usd_krw
                if cur_price > 5000:
                    cur_price = cur_price / usd_krw
        labels = ["entry", "current"] if avg_price > 0 or cur_price > 0 else []
        prices = [round(avg_price, 6), round(cur_price or avg_price, 6)] if labels else []
        stages = ["entry", "current"] if labels else []
    return jsonify({
        "ok": True,
        "mode": mode,
        "market": market,
        "ticker": ticker,
        "session_date": session_date,
        "source": source,
        "labels": labels,
        "prices": prices,
        "stages": stages,
        "count": len(prices),
    })


@app.route("/api/preopen")
def api_preopen():
    market = str(request.args.get("market", current_market()) or "KR").upper()
    market = "US" if market == "US" else "KR"
    session_date = request.args.get("session_date") or _session_trade_date(market).isoformat()
    limit = int(request.args.get("limit", "50") or 50)
    try:
        from preopen.storage import load_preopen_dashboard
        return jsonify(load_preopen_dashboard(market, session_date=session_date, limit=limit))
    except Exception as exc:
        return jsonify({
            "market": market,
            "session_date": session_date,
            "summary": {
                "collector_status": "unavailable",
                "error": str(exc),
                "candidate_count": 0,
                "rank_diff_count": 0,
                "outcome_count": 0,
            },
            "candidates": [],
            "rank_diff": [],
            "outcome": [],
        })


def _enrich_bucket_monitor_tickers(summary: dict, *, market: Optional[str], mode: str) -> None:
    """Add name+code labels to bucket monitor rows using the dashboard ticker name map."""
    bucket = (summary or {}).get("bucket_monitor")
    if not isinstance(bucket, dict):
        return
    rows = bucket.get("candidates")
    if not isinstance(rows, list):
        return
    requested_market = str(market or "").upper()
    name_maps: dict[str, dict[str, str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_market = requested_market or str(row.get("market") or "").upper()
        if row_market not in {"KR", "US"}:
            row_market = "US" if str(row.get("ticker") or "").strip().upper().isalpha() else "KR"
        if row_market not in name_maps:
            try:
                name_maps[row_market] = _ticker_name_map(row_market, include_broker=True, mode=mode)
            except Exception:
                name_maps[row_market] = {}
        ticker = str(row.get("ticker") or "").strip()
        mapped_name = str(name_maps.get(row_market, {}).get(ticker.upper(), "") or "").strip()
        current_name = str(row.get("name") or "").strip()
        if mapped_name and (not current_name or current_name.upper() == ticker.upper()):
            row["name"] = mapped_name
        row["display_ticker"] = _display_ticker_label(ticker, row.get("name", ""), name_maps.get(row_market, {}))


@app.route("/api/claude/status")
def api_claude_status():
    market = request.args.get("market", best_market_with_data())
    mode = _request_mode()
    control = _load_claude_control(mode=mode)
    session = _session_status(market)
    live = _load_live_status(market, mode=mode)
    claude = live.get("claude", {}) if isinstance(live, dict) else {}
    stats = _claude_reinvoke_stats_today(market)
    return jsonify({
        "market": market,
        "mode": mode,
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
    mode = _normalize_mode(body.get("mode"))
    enabled = bool(body.get("enabled", True))
    control = _load_claude_control(mode=mode)
    control["enabled"] = enabled
    control["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    control["updated_by"] = "dashboard"
    if not enabled:
        control["pending_trigger"] = None
    _save_claude_control(control, mode=mode)
    return jsonify({"ok": True, "enabled": enabled, "mode": mode})


@app.route("/api/claude/trigger", methods=["POST"])
def api_claude_trigger():
    body = request.get_json(silent=True) or {}
    mode = _normalize_mode(body.get("mode"))
    market = body.get("market", best_market_with_data())
    session = _session_status(market)
    control = _load_claude_control(mode=mode)
    if not bool(control.get("enabled", True)):
        return jsonify({"ok": False, "error": "Claude 재판단 기능이 OFF 상태입니다"}), 400
    if not session.get("active"):
        return jsonify({"ok": False, "error": f"{market} 세션이 현재 비활성 상태입니다"}), 400
    control["pending_trigger"] = {
        "market": market,
        "source": "dashboard_trigger",
        "requested_at": datetime.now(KST).isoformat(timespec="seconds"),
    }
    control["updated_at"] = datetime.now(KST).isoformat(timespec="seconds")
    control["updated_by"] = "dashboard"
    _save_claude_control(control, mode=mode)
    return jsonify({"ok": True, "queued": True, "market": market, "mode": mode})


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
        "lesson":       _clean_lesson(postmortem.get("key_lesson", "")),
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
    recent_days = brain.get("markets", {}).get(market, {}).get("recent_days", [])
    records = []
    if recent_days:
        dedup = {}
        for day in recent_days:
            d = (day.get("date", "") or "")[:10]
            if not d:
                continue
            dedup[d] = {
                "date": d,
                "postmortem": {
                    "bull_result": day.get("bull_result"),
                    "bear_result": day.get("bear_result"),
                    "neutral_result": day.get("neutral_result"),
                },
            }
        records = [dedup[k] for k in sorted(dedup.keys())]
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
        "primary_label": "KOSPI 일간 수익률" if market == "KR" else "S&P500 일간 수익률",
        "secondary_label": "KOSDAQ 일간 수익률" if market == "KR" else "NASDAQ 일간 수익률",
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
    dashboard_pid = int(dashboard_info.get("pid", os.getpid()) or os.getpid())
    dashboard_alive = _pid_alive(dashboard_pid)
    if dashboard_info and not dashboard_alive and dashboard_pid != os.getpid():
        _clear_pid_file(DASHBOARD_PID_PATH)
    mode = _request_mode()
    runtimes = {}
    for runtime_mode in ("paper", "live"):
        bot_path = _bot_pid_path(runtime_mode)
        bot_info = _read_pid_file(bot_path)
        bot_pid = int(bot_info.get("pid", 0) or 0)
        bot_alive = _pid_alive(bot_pid)
        if bot_info and not bot_alive:
            _clear_pid_file(bot_path)
        runtimes[runtime_mode] = {
            "pid": bot_pid,
            "alive": bot_alive,
            "started_at": bot_info.get("started_at", ""),
        }
    return jsonify({
        "mode": mode,
        "dashboard": {
            "pid": dashboard_pid,
            "alive": dashboard_alive,
            "started_at": dashboard_info.get("started_at", ""),
        },
        "bot": runtimes.get(mode, {}),
        "bots": runtimes,
    })


@app.route("/api/control/restart-dashboard", methods=["POST"])
def api_control_restart_dashboard():
    _restart_dashboard_process()
    return jsonify({"ok": True, "message": "대시보드 재시작 요청을 보냈습니다"})


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
    sync_strategy = {}
    for name, perf in raw_strategy.items():
        if (name or "").strip() in ("broker_sync", "broker_balance"):
            sync_strategy[_ko_strategy_name(name)] = {
                "count": int(perf.get("count", 0) or 0),
                "win_rate": round(float(perf.get("win_rate", 0) or 0), 4),
                "avg_pnl": round(_normalize_percent_value(float(perf.get("avg_pnl", 0) or 0)), 4),
                "best_k": perf.get("best_k"),
            }
            continue
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
        "sync_strategy": sync_strategy,
        "strategy_unit": "percent",
    })


@app.route("/api/brain/history")
def api_brain_history():
    mkt   = request.args.get("market", "KR")
    brain = load_brain()
    m     = brain.get("markets", {}).get(mkt, {})
    days  = list(reversed(m.get("recent_days", [])))  # 최신순
    record_map = {}
    for rec in load_records(90, mkt):
        d = (rec.get("date", "") or "")[:10]
        if d:
            record_map[d] = rec
    enriched_days = []
    for day in days:
        item = dict(day)
        rec = record_map.get((day.get("date", "") or "")[:10], {}) or {}
        actual = rec.get("actual_result", {}) or {}
        execution_issues = list(actual.get("execution_issues", []) or [])
        execution_contaminated = bool(actual.get("execution_contaminated", False))
        item["execution_contaminated"] = execution_contaminated
        item["execution_issues"] = execution_issues
        lesson = str(item.get("key_lesson", "") or "").strip()
        if execution_contaminated and ("오류로 자동 판정" in lesson or not lesson):
            labels = [_execution_issue_label(x) for x in execution_issues[:3]]
            item["key_lesson"] = "실행오염: " + (", ".join(labels) if labels else "자동 판정")
        enriched_days.append(item)
    beliefs = m.get("current_beliefs", {})
    return jsonify({
        "market":         mkt,
        "trained_days":   m.get("trained_days", 0),
        "market_regime":  beliefs.get("market_regime", "unknown"),
        "learned_lessons": beliefs.get("learned_lessons", []),
        "correction_guide": brain.get("correction_guide", {}).get(mkt, {}),
        "analyst_performance": m.get("analyst_performance", {}),
        "recent_days":    enriched_days,
    })


# ?? ?좉퇋 API ?붾뱶?ъ씤??????????????????????????????????????????????????????????

@app.route("/api/stats/period")
def api_stats_period():
    market = request.args.get("market", best_market_with_data())
    period = request.args.get("period", "month")
    start  = request.args.get("start", "")
    end    = request.args.get("end", "")
    mode = _request_mode()
    if _is_live_mode(mode):
        trades = _broker_trade_rows_with_pnl(market, period, start, end, mode=mode)
        active_days = len({str(t.get("date", "") or "")[:10] for t in trades})
        stats = _trade_stats_from_rows(trades, active_days)
        stats["basis"] = "broker_closed_trades"
        return jsonify(stats)

    records = load_records_filtered(market, period, start, end)
    if not records:
        return jsonify({"days": 0, "wins": 0, "losses": 0,
                        "win_rate": 0, "total_pnl": 0, "avg_pnl": 0, "trades": 0, "unknown_trades": 0})

    trades = _trade_rows_for_records(records, market)
    stats = _trade_stats_from_rows(trades, len(records))
    return jsonify(stats)


@app.route("/api/history/monthly")
def api_history_monthly():
    market  = request.args.get("market", best_market_with_data())
    mode = _request_mode()
    if _is_live_mode(mode):
        trades = _broker_trade_rows_with_pnl(market, "all", "", "", mode=mode)
        month_groups: dict[str, list[dict]] = {}
        for trade in trades:
            month = str(trade.get("date", "") or "")[:7]
            if month:
                month_groups.setdefault(month, []).append(trade)
        result = []
        for month in sorted(month_groups.keys(), reverse=True):
            month_trades = month_groups[month]
            active_days = len({str(t.get("date", "") or "")[:10] for t in month_trades})
            stats = _trade_stats_from_rows(month_trades, active_days)
            day_groups = {}
            for t in month_trades:
                if t.get("side") != "sell" or not bool(t.get("pnl_known", True)):
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
                "month": month,
                "days": stats["days"],
                "wins": stats["wins"],
                "losses": stats["losses"],
                "win_rate": stats["win_rate"],
                "total_pnl": stats["total_pnl"],
                "avg_pnl": stats["avg_pnl"],
                "trades": stats["trades"],
                "unknown_trades": stats.get("unknown_trades", 0),
                "basis": "broker_closed_trades",
                "best_day": best_day,
                "worst_day": worst_day,
            })
        return jsonify(result)

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
            "unknown_trades": stats.get("unknown_trades", 0),
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
    mode = _request_mode()
    if _is_live_mode(mode):
        broker = _broker_snapshot(mode=mode)
        if broker:
            _persist_broker_equity_snapshot(broker, mode=mode)
        current_asset = _market_asset_krw_from_broker_snapshot(broker, market)
        broker_rows = _broker_trade_rows_with_pnl(market, period, start, end, mode=mode)
        pnl_by_date: dict[str, float] = {}
        pct_by_date: dict[str, float] = {}
        for row in broker_rows:
            if row.get("side") != "sell" or not bool(row.get("pnl_known", True)):
                continue
            trade_date = str(row.get("date", "") or "")[:10]
            pnl_by_date[trade_date] = pnl_by_date.get(trade_date, 0.0) + float(row.get("pnl", 0) or 0)
            pct_by_date[trade_date] = pct_by_date.get(trade_date, 0.0) + float(row.get("pnl_pct", 0) or 0)
        snapshot_rows = _load_broker_equity_snapshots(market, period, start, end, mode=mode)
        snapshot_map = {
            str(item.get("date", "") or "")[:10]: float(item.get("asset_krw", 0) or 0)
            for item in snapshot_rows
        }
        labels = sorted(set(list(pnl_by_date.keys()) + list(snapshot_map.keys())))
        session_label = _session_trade_date(market).isoformat()
        if current_asset > 0 and session_label not in labels:
            labels.append(session_label)
        labels = sorted(set(labels))
        if not labels and current_asset > 0:
            labels = [session_label]
        equity = [0.0] * len(labels)
        pnl = [round(pct_by_date.get(label, 0.0), 4) for label in labels]
        wins = [pnl_by_date.get(label, 0.0) > 0 for label in labels]
        modes = ["" for _ in labels]
        if labels:
            if labels[-1] in snapshot_map and snapshot_map[labels[-1]] > 0:
                equity[-1] = snapshot_map[labels[-1]]
            elif labels[-1] == session_label and current_asset > 0:
                equity[-1] = current_asset
            else:
                equity[-1] = current_asset
            for i in range(len(labels) - 2, -1, -1):
                next_label = labels[i + 1]
                if labels[i] in snapshot_map and snapshot_map[labels[i]] > 0:
                    equity[i] = snapshot_map[labels[i]]
                else:
                    equity[i] = equity[i + 1] - float(pnl_by_date.get(next_label, 0.0) or 0.0)
        return jsonify({
            "labels": labels,
            "equity": equity,
            "pnl": pnl,
            "wins": wins,
            "modes": modes,
            "basis": "broker_asset_reconstructed",
        })

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
    today_label = _session_trade_date(market).isoformat()
    live = _load_live_status(market, mode=mode)
    broker = _broker_snapshot(mode=mode)
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
    mode         = _request_mode()
    market       = request.args.get("market", best_market_with_data())
    period       = request.args.get("period", "all")
    start        = request.args.get("start", "")
    end          = request.args.get("end", "")
    ticker       = request.args.get("ticker", "").upper()
    strategy     = request.args.get("strategy", "")
    side         = request.args.get("side", "")
    match_status = request.args.get("match_status", "")
    limit        = int(request.args.get("limit", "100"))
    include_live = request.args.get("include_live", "true").lower() != "false"

    def _trade_dedupe_key(t: dict) -> tuple:
        return (
            t.get("date", ""),
            t.get("time", ""),
            t.get("side", ""),
            t.get("ticker", ""),
            t.get("order_no", ""),
            int(t.get("qty", 0) or 0),
        )

    records = load_records_filtered(market, period, start, end)
    broker_rows = _broker_trade_rows_with_pnl(market, period, start, end, mode=mode)

    if _is_live_mode(mode):
        strategy_rows = []
        max_records = max(30, limit * 2)
        records_to_process = records[-max_records:] if len(records) > max_records else records
        for r in records_to_process:
            strategy_rows.extend(_trades_for_record(r, market))
        _, trades = _annotate_trade_matches(strategy_rows, broker_rows)
    else:
        max_records = max(30, limit * 2)
        records_to_process = records[-max_records:] if len(records) > max_records else records
        trades = []
        for r in records_to_process:
            for t in _trades_for_record(r, market):
                trades.append(t)

    seen = set()
    deduped = []
    for t in trades:
        key = _trade_dedupe_key(t)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(t)
    trades = deduped

    if include_live:
        live_rows = _live_trades(market)
        if _is_live_mode(mode):
            live_rows = [row for row in live_rows if row.get("source_kind") == "pending_order"]
        for t in live_rows:
            t_side     = t.get("side", "")
            t_ticker   = t.get("ticker", "")
            t_strategy = t.get("strategy", "")
            if ticker   and ticker   not in t_ticker.upper():
                continue
            if strategy and strategy != t_strategy:
                continue
            if side     and side     != t_side:
                continue
            key = _trade_dedupe_key(t)
            if key in seen:
                continue
            seen.add(key)
            trades.append(t)

    filtered = []
    for t in trades:
        t_side     = t.get("side", "")
        t_ticker   = t.get("ticker", "")
        t_strategy = t.get("strategy", "")
        if ticker   and ticker   not in t_ticker.upper():
            continue
        if strategy and strategy != t_strategy:
            continue
        if side     and side     != t_side:
            continue
        filtered.append(t)
    trades = filtered

    name_map = _ticker_name_map(market, include_broker=False, mode=mode)
    trades = [_enrich_trade_row(t, market, name_map=name_map) for t in trades]

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
    trades, _ = _annotate_trade_matches(trades, broker_rows)
    if match_status:
        trades = [t for t in trades if str(t.get("match_status", "") or "") == match_status]
    trades.sort(key=lambda x: (x.get("date", ""), x.get("time", ""), x.get("side", "")), reverse=True)
    return jsonify(trades[:limit])


@app.route("/api/trades/broker")
def api_trades_broker():
    mode = _request_mode()
    market = request.args.get("market", best_market_with_data())
    period = request.args.get("period", "all")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    ticker = request.args.get("ticker", "").upper()
    side = request.args.get("side", "")
    match_status = request.args.get("match_status", "")
    limit = int(request.args.get("limit", "100"))

    trades = _broker_trade_rows_with_pnl(market, period, start, end, mode=mode)
    strategy_rows = []
    records = load_records_filtered(market, period, start, end)
    max_records = max(30, limit * 2)
    records_to_process = records[-max_records:] if len(records) > max_records else records
    for r in records_to_process:
        strategy_rows.extend(_trades_for_record(r, market))
    _, trades = _annotate_trade_matches(strategy_rows, trades)
    filtered = []
    for t in trades:
        t_side = t.get("side", "")
        t_ticker = t.get("ticker", "")
        if ticker and ticker not in t_ticker.upper():
            continue
        if side and side != t_side:
            continue
        if match_status and str(t.get("match_status", "") or "") != match_status:
            continue
        filtered.append(t)
    return jsonify(filtered[:limit])


@app.route("/api/trades/broker/status")
def api_trades_broker_status():
    mode = _request_mode()
    market = request.args.get("market", best_market_with_data())
    period = request.args.get("period", "all")
    start = request.args.get("start", "")
    end = request.args.get("end", "")
    bundle = _load_broker_trade_bundle(market, period, start, end, mode=mode)
    return jsonify({
        "ok": bool(bundle.get("ok")),
        "market": market,
        "query_start": bundle.get("query_start", ""),
        "query_end": bundle.get("query_end", ""),
        "count": int(bundle.get("count", 0) or 0),
        "latest_fill": bundle.get("latest_fill", ""),
        "error": bundle.get("error", ""),
    })


@app.route("/api/signals/recent")
def api_signals_recent():
    """최근 신호 이벤트 목록을 analysis JSONL에서 읽는다."""
    market = request.args.get("market", "KR")
    n      = min(int(request.args.get("n", "60")), 200)
    mode   = _request_mode()
    events: list[dict] = []
    all_recs = _load_analysis_records_for_session(market, mode)

    for rec in all_recs:
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
        # signal_check에서는 signal=none인 경우만 포함한다 (신호 없음)
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
            "detail":    extra.get("detail", ""),
        })
    # 최신순으로 뒤집는다
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
    mode = _request_mode()
    live = _load_live_status(market, mode=mode) or {}
    broker_positions = _filter_items_for_market(_load_broker_positions(market, mode=mode), market)
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


@app.route("/api/decisions")
def api_decisions():
    """Claude 매수/매도/홀드 판단 이력 (state/decisions.jsonl)"""
    mode = _request_mode()
    market = request.args.get("market", "")
    action = request.args.get("action", "")
    ticker = request.args.get("ticker", "")
    limit  = int(request.args.get("limit", "100"))

    decisions_path = _decisions_path(mode)
    rows = []
    if decisions_path.exists():
        try:
            lines = decisions_path.read_text(encoding="utf-8").strip().splitlines()
            for line in lines:
                try:
                    ev = json.loads(line)
                    if market and ev.get("market") != market:
                        continue
                    if action and ev.get("action") != action:
                        continue
                    if ticker and ev.get("ticker") != ticker:
                        continue
                    rows.append(ev)
                except Exception:
                    continue
        except Exception:
            pass
    rows.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
    return jsonify(rows[:limit])


def _is_live_market(market: str) -> bool:
    """현재 시각이 해당 market 장 시간인지 판별 (KST 기준)"""
    from datetime import time as _t, timezone, timedelta
    KST = timezone(timedelta(hours=9))
    now = datetime.now(KST).time()
    if market == "KR":
        return _t(9, 0) <= now < _t(15, 35)
    else:  # US: 22:30 ~ 05:00(익일)
        return now >= _t(22, 30) or now < _t(5, 5)


@app.route("/api/refresh_prices", methods=["POST"])
def api_refresh_prices():
    """포지션 현재가 갱신: 장 중이면 KIS API 실시간, 장 외이면 저장 데이터 반환"""
    body   = request.get_json(silent=True) or {}
    mode   = _normalize_mode(body.get("mode"))
    market = body.get("market", "KR").upper()

    live = _load_live_status(market, mode=mode) or {}
    positions = live.get("positions", [])
    if not positions:
        return jsonify({"ok": True, "market": market, "live": False, "positions": []})

    is_live = _is_live_market(market)
    updated = False

    if is_live:
        try:
            with _kis_runtime(mode):
                token = get_access_token()
                for pos in positions:
                    ticker = pos.get("ticker", "")
                    if not ticker:
                        continue
                    try:
                        px_data = get_price(ticker, token, market=market)
                        cp = float(px_data.get("current_price") or px_data.get("price") or 0)
                        if cp > 0:
                            pos["current_price"] = cp
                            # display 필드도 갱신
                            if "display_current_price" in pos:
                                pos["display_current_price"] = cp
                            updated = True
                    except Exception:
                        pass  # 개별 종목 실패 시 기존값 유지
                if updated:
                    live["positions"] = positions
                    live_path = _live_status_path(mode, market)
                    live_path.write_text(json.dumps(live, ensure_ascii=False), encoding="utf-8")
        except Exception:
            is_live = False  # 토큰 오류 등 → 저장 데이터 반환

    return jsonify({
        "ok": True,
        "market": market,
        "live": is_live and updated,
        "positions": [
            {
                "ticker":        p.get("ticker"),
                "current_price": p.get("current_price", p.get("avg_price", 0)),
                "avg_price":     p.get("avg_price", 0),
                "qty":           p.get("qty", 0),
                "pnl_pct":       (
                    (float(p.get("current_price") or p.get("avg_price") or 0) /
                     float(p.get("avg_price") or 1) - 1) * 100
                    if p.get("avg_price") else p.get("pnl_pct", 0)
                ),
            }
            for p in positions
        ],
    })


@app.route("/api/review_position", methods=["POST"])
def api_review_position():
    """종목별 Claude 즉시 재판단 (대시보드 버튼 → hold_advisor 호출)"""
    body   = request.get_json(silent=True) or {}
    mode   = _normalize_mode(body.get("mode"))
    market = body.get("market", "KR").upper()
    ticker = body.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400

    market, pos, live, positions = _resolve_review_position(market, ticker, mode=mode)
    if pos is None:
        return jsonify({"error": f"{ticker} 포지션을 찾을 수 없습니다"}), 404

    # live_status position은 entry=None/0일 수 있음 → open_positions.json의 KRW entry로 보완
    if float(pos.get("entry", 0) or 0) <= 0:
        saved = next(
            (p for p in _load_open_positions(mode)
             if str(p.get("ticker", "") or "").strip().upper() == ticker),
            None,
        )
        if saved and float(saved.get("entry", 0) or 0) > 0:
            pos = _merge_position_context(saved, pos)

    try:
        from minority_report.hold_advisor import ask as advisor_ask
        digest = ""
        # 오늘 digest_prompt 가져오기 시도
        try:
            today_rec = load_today(market)
            digest = today_rec.get("digest_prompt", "")
        except Exception:
            pass

        advice = advisor_ask(pos, market, digest, decision_stage="MANUAL_REVIEW")
        now_iso = datetime.now(KST).isoformat(timespec="seconds")

        # live_status 업데이트
        updated = False
        for p2 in positions:
            if str(p2.get("ticker", "") or "").strip().upper() == ticker:
                p2["hold_advice"] = advice
                p2["last_hold_update"] = now_iso[:10]
                updated = True
        if not updated:
            pos = dict(pos)
            pos["hold_advice"] = advice
            pos["last_hold_update"] = now_iso[:10]
            positions.append(pos)
        live["positions"] = positions
        live["position_count"] = len(positions)
        live_path = _live_status_path(mode, market)
        live_path.write_text(json.dumps(live, ensure_ascii=False), encoding="utf-8")

        # open_positions.json도 같이 갱신해 재시작/오프마켓 후에도 유지
        saved_positions = _load_open_positions(mode)
        saved_updated = False
        for p2 in saved_positions:
            if str(p2.get("ticker", "") or "").strip().upper() == ticker:
                p2["hold_advice"] = advice
                p2["last_hold_update"] = now_iso[:10]
                saved_updated = True
                break
        if not saved_updated:
            saved_positions.append({
                **dict(pos),
                "market": market,
                "hold_advice": advice,
                "last_hold_update": now_iso[:10],
            })
        _save_open_positions(saved_positions, mode=mode)

        queued_sell = False
        if advice.get("action") == "SELL":
            control = _load_claude_control(mode=mode)
            control["pending_position_review"] = {
                "market": market,
                "ticker": ticker,
                "source": "dashboard_review",
                "requested_at": now_iso,
            }
            control["updated_at"] = now_iso
            control["updated_by"] = "dashboard_review"
            _save_claude_control(control, mode=mode)
            queued_sell = True

        # decisions.jsonl 기록
        from datetime import datetime as _dt
        decisions_path = _decisions_path(mode)
        event = {
            "timestamp": now_iso,
            "mode":      mode,
            "market":    market,
            "ticker":    ticker,
            "action":    "HOLD_REVIEW",
            "hold_action": advice.get("action"),
            "trail_pct": advice.get("trail_pct"),
            "votes":     advice.get("votes", {}),
            "source":    "dashboard_review",
            "queued_sell": queued_sell,
        }
        with open(decisions_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

        return jsonify({"ok": True, "ticker": ticker, "market": market,
                        "action": advice.get("action"), "trail_pct": advice.get("trail_pct"),
                        "votes": advice.get("votes", {}), "queued_sell": queued_sell})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/immediate_sell", methods=["POST"])
def api_immediate_sell():
    """즉시 매도: Claude에게 적정 매도가 물어보고 pending_sell에 기록"""
    import anthropic as _anthropic
    body   = request.get_json(silent=True) or {}
    mode   = _normalize_mode(body.get("mode"))
    market = body.get("market", "KR").upper()
    ticker = body.get("ticker", "").strip().upper()
    if not ticker:
        return jsonify({"error": "ticker required"}), 400

    market, pos, live, positions = _resolve_review_position(market, ticker, mode=mode)
    if pos is None:
        return jsonify({"error": f"{ticker} 포지션을 찾을 수 없습니다"}), 404

    is_us = (market == "US")
    cur_px_display = float(pos.get("display_current_price") or 0)
    cur_px_raw     = float(pos.get("current_price") or 0)
    avg_px_display = float(pos.get("display_avg_price") or pos.get("avg_price") or pos.get("entry") or 0)
    currency       = "USD" if is_us else "KRW"

    # 현재가: display 우선, 없으면 raw
    cur_px = cur_px_display if cur_px_display > 0 else cur_px_raw
    if cur_px <= 0:
        return jsonify({"error": "현재가 조회 실패"}), 400

    # Claude에게 적정 매도가 질의
    try:
        client = _anthropic.Anthropic()
        pnl_pct = ((cur_px / avg_px_display) - 1) * 100 if avg_px_display > 0 else 0
        prompt = (
            f"포지션 정보:\n"
            f"- 종목: {ticker} ({market})\n"
            f"- 전략: {pos.get('strategy','')}\n"
            f"- 매수가: {avg_px_display:.4f} {currency}\n"
            f"- 현재가: {cur_px:.4f} {currency}\n"
            f"- 수익률: {pnl_pct:+.2f}%\n"
            f"- 보유일: {pos.get('held_days', 0)}일\n\n"
            f"사용자가 이 포지션을 즉시 매도하려 합니다. "
            f"현재 시장 상황에서 즉시 체결되면서도 최대한 유리한 매도 호가를 {currency}로 추천해주세요. "
            f"숫자만 답하세요 (예: 120.45 또는 85200)."
        )
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=20,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = msg.content[0].text.strip().replace(",", "").replace("$", "").replace("원", "")
        sell_price = float(raw)
    except Exception as e:
        # Claude 실패 시 현재가 그대로 사용
        sell_price = cur_px

    # pending_sell 기록
    try:
        ctrl_path = _claude_control_path(mode)
        ctrl = {}
        if ctrl_path.exists():
            with open(ctrl_path, encoding="utf-8") as f:
                ctrl = json.load(f)
        ctrl["pending_sell"] = {
            "mode": mode,
            "market": market,
            "ticker": ticker,
            "sell_price": sell_price,
            "requested_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        with open(ctrl_path, "w", encoding="utf-8") as f:
            json.dump(ctrl, f, ensure_ascii=False, indent=2)
    except Exception as e:
        return jsonify({"error": f"pending_sell 기록 실패: {e}"}), 500

    return jsonify({"ok": True, "ticker": ticker, "market": market, "sell_price": sell_price, "currency": currency})


@app.route("/api/tickers/today")
def api_tickers_today():
    """오늘 Claude가 선택한 모니터링 종목과 최근 신호를 요약한다."""
    market = request.args.get("market", best_market_with_data())
    rec    = load_today(market)
    tickers  = rec.get("tickers", [])
    universe = rec.get("universe_tickers", [])
    consensus = rec.get("consensus", {})
    mode = _request_mode()
    live = _load_live_status(market, mode=mode) or {}
    broker_positions = _filter_items_for_market(_load_broker_positions(market, mode=mode), market)
    live_positions = _filter_items_for_market(live.get("positions", []) if live else [], market)
    live_pending = _filter_items_for_market(live.get("pending_orders", []) if live else [], market)
    name_map = _ticker_name_map(market, mode=mode)

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

    # 오늘 analysis 로그에서 종목별 최근 이벤트와 선택 사유를 집계한다
    ticker_last: dict[str, dict] = {}
    ticker_sig_count: dict[str, int] = {}
    ticker_skip_reasons: dict[str, list] = {}
    selection_reasons: dict[str, str] = {}
    watch_only_details: dict[str, str] = {}
    candidates_list: list[str] = []
    def _prefer_ticker_event(prev: dict, cand: dict) -> dict:
        if not prev:
            return cand
        prev_ts = str(prev.get("ts", "") or "")
        cand_ts = str(cand.get("ts", "") or "")
        if cand_ts > prev_ts:
            return cand
        if cand_ts < prev_ts:
            return prev
        prev_price = float(prev.get("price", 0) or 0)
        cand_price = float(cand.get("price", 0) or 0)
        prev_detail = str(prev.get("detail", "") or "")
        cand_detail = str(cand.get("detail", "") or "")
        prev_score = (1 if prev_price > 0 else 0) + (1 if prev_detail else 0)
        cand_score = (1 if cand_price > 0 else 0) + (1 if cand_detail else 0)
        return cand if cand_score >= prev_score else prev
    for r in _load_analysis_records_for_session(market, mode):
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
                watch_only_details.clear()
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
            ticker_last[t] = _prefer_ticker_event(
                ticker_last.get(t, {}),
                {"event": ev, "ts": r.get("timestamp", ""),
                 "price": extra.get("price", 0),
                 "reason": extra.get("reason", ""),
                 "detail": extra.get("detail", "")}
            )
            if ev == "entry_signal":
                ticker_sig_count[t] = ticker_sig_count.get(t, 0) + 1
            # 미체결/차단 사유 추적 (entry_skip / signal_blocked / entry_failed)
            if ev in ("entry_skip", "signal_blocked", "entry_failed"):
                reason = extra.get("reason", "") or extra.get("mode", "") or ev
                if reason:
                    reasons_list = ticker_skip_reasons.setdefault(t, [])
                    if reason not in reasons_list:
                        reasons_list.append(reason)
                if reason == "watch_only":
                    watch_detail = str(
                        extra.get("detail", "") or extra.get("select_reason", "") or ""
                    ).strip()
                    if watch_detail:
                        watch_only_details[t] = watch_detail
                        if not selection_reasons.get(t):
                            selection_reasons[t] = watch_detail

    runtime_events = _parse_runtime_events(market, limit=200)
    for ev in reversed(runtime_events):
        t = ev.get("ticker", "")
        if not t:
            continue
        ticker_last[t] = _prefer_ticker_event(
            ticker_last.get(t, {}),
            {
                "event": ev.get("event", ""),
                "ts": ev.get("timestamp", ""),
                "price": ev.get("price", 0),
                "reason": ev.get("reason", ""),
                "detail": ev.get("detail", ""),
            }
        )

    recent_trade_map: dict[str, dict] = {}
    for trade in _trade_rows_for_records(load_records(90, market), market):
        ticker = str(trade.get("ticker", "") or "").upper()
        if not ticker or ticker in recent_trade_map:
            continue
        recent_trade_map[ticker] = trade

    # 사유 한글 매핑
    _REASON_KO = {
        "already_holding":      "이미 보유중",
        "budget_exhausted":     "예산 소진",
        "max_positions":        "최대 포지션 도달",
        "est_slippage_too_high":"예상 슬리피지 과다",
        "HALT":                 "거래중지 모드",
        "DEFENSIVE":            "방어 모드",
        "entry_failed":         "주문 실패",
        "signal_blocked":       "모드 차단",
        "pending_order":        "미체결 주문",
        "watch_only":           "TRADE_READY 제외",
    }

    def _today_watchlist_history(_market: str) -> dict:
        day_str = _market_log_date_str(_market)
        sys_path = BASE_DIR / "logs" / "system" / f"trading_{day_str}.log"
        # US 자정 세션: session date = 어제, 시스템 날짜 = 오늘 → 오늘 로그도 함께 읽기
        _now = datetime.now(KST)
        sysdate_str = _now.strftime("%Y%m%d")
        sysdate_path = BASE_DIR / "logs" / "system" / f"trading_{sysdate_str}.log"
        initial: list[str] = []
        history: list[dict] = []
        current: list[str] = []
        # 읽을 로그 파일 목록 (오래된 것 먼저, 최신 것 나중에)
        paths_to_read = []
        if sys_path.exists():
            paths_to_read.append(sys_path)
        if sysdate_str != day_str and sysdate_path.exists():
            paths_to_read.append(sysdate_path)
        if not paths_to_read:
            return {"initial": initial, "current": current, "history": history}
        lines = []
        for p in paths_to_read:
            try:
                lines.extend(p.read_text(encoding="utf-8").splitlines())
            except Exception:
                pass
        if not lines:
            return {"initial": initial, "current": current, "history": history}
        pat_initial = re.compile(rf"\[(?:종목선택 확정|종목 재선택 완료)\]\s+{_market}:\s+\[(.*)\]")
        pat_manual = re.compile(rf"\[수동 종목 재선택 완료\]\s+{_market}:\s+\[(.*)\]")
        pat_sched = re.compile(rf"\[{_market} 정시 재스크리닝\]\s+\[(.*)\]")
        pat_partial = re.compile(rf"\[부분교체\]\s+{_market}:\s+\[(.*)\]\s+→\s+\[(.*)\]")
        for line in lines:
            ts = line[:19]
            m = pat_initial.search(line)
            if m:
                tickers0 = [x.strip().strip("'") for x in m.group(1).split(",") if x.strip()]
                if tickers0:
                    if not initial:
                        initial = tickers0
                        current = tickers0[:]
                        history.append({"ts": ts, "kind": "initial", "tickers": tickers0})
                    elif current:
                        out_list = [t for t in current if t not in tickers0]
                        in_list = [t for t in tickers0 if t not in current]
                        if out_list or in_list:
                            history.append({"ts": ts, "kind": "partial", "out": out_list, "in": in_list})
                        current = tickers0[:]
                continue
            m = pat_manual.search(line)
            if m:
                tickers1 = [x.strip().strip("'") for x in m.group(1).split(",") if x.strip()]
                if tickers1:
                    if current:
                        out_list = [t for t in current if t not in tickers1]
                        in_list = [t for t in tickers1 if t not in current]
                        if out_list or in_list:
                            history.append({"ts": ts, "kind": "partial", "out": out_list, "in": in_list})
                    current = tickers1[:]
                continue
            m = pat_sched.search(line)
            if m:
                tickers2 = [x.strip().strip("'") for x in m.group(1).split(",") if x.strip()]
                if tickers2:
                    if current:
                        out_list = [t for t in current if t not in tickers2]
                        in_list = [t for t in tickers2 if t not in current]
                        if out_list or in_list:
                            history.append({"ts": ts, "kind": "partial", "out": out_list, "in": in_list})
                    current = tickers2[:]
                continue
            m = pat_partial.search(line)
            if m:
                out_list = [x.strip().strip("'") for x in m.group(1).split(",") if x.strip()]
                in_list = [x.strip().strip("'") for x in m.group(2).split(",") if x.strip()]
                history.append({"ts": ts, "kind": "partial", "out": out_list, "in": in_list})
                if current:
                    current = [t for t in current if t not in set(out_list)] + [t for t in in_list if t not in current]
        compact: list[dict] = []
        prev_key = None
        for item in history:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key == prev_key:
                continue
            compact.append(item)
            prev_key = key
        return {"initial": initial, "current": current, "history": compact[-12:]}

    watch_hist = _today_watchlist_history(market)
    tickers = watch_hist.get("current") or tickers

    result = []
    for t in tickers:
        last = ticker_last.get(t, {})
        raw_reasons = ticker_skip_reasons.get(t, [])
        skip_reasons_ko = [_REASON_KO.get(r, r) for r in raw_reasons]
        selection_status = _selection_status_for_ticker(rec, t, market)
        select_reason = selection_reasons.get(t, "") or rec.get("ticker_reasons", {}).get(t, "")
        result.append({
            "ticker":          t,
            "display_ticker":  _display_ticker_label(t, "", name_map),
            "last_event":      last.get("event", "waiting"),
            "last_ts":         _format_hhmm(last.get("ts", "")),
            "last_price":      last.get("price", 0),
            "last_reason":     last.get("reason", ""),
            "none_reason":     last.get("detail", ""),
            "sig_count":       ticker_sig_count.get(t, 0),
            "select_reason":   select_reason,
            "selection_status": selection_status,
            "selection_status_ko": "매수 가능" if selection_status == "TRADE_READY" else "감시 전용",
            "watch_only_detail": watch_only_details.get(t, ""),
            "skip_reasons":    skip_reasons_ko,   # 오늘 누적 미체결 사유
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
        item["select_reason"] = _resolve_ticker_select_reason(
            ticker,
            market,
            consensus.get("mode", ""),
            item,
            rec,
            base_reason=item.get("select_reason", ""),
            watch_only_detail=item.get("watch_only_detail", ""),
        )
    not_selected = [t for t in candidates_list if t not in tickers]
    return jsonify({
        "market":         market,
        "mode":           consensus.get("mode", ""),
        "tickers":        result,
        "current_tickers": tickers,
        "initial_tickers": watch_hist.get("initial", []),
        "watchlist_history": watch_hist.get("history", []),
        "universe_count": len(universe) or len(candidates_list),
        "candidates":     candidates_list,
        "not_selected":   not_selected,
    })


# ?? HTML ?ы띁 ?????????????????????????????????????????????????????????????????

def _head(title: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} · TRADINGBRAIN</title>
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

/* ?? ?ㅻ뜑 ?? */
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

/* 기간 필터 바 */
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

/* 레이아웃 */
main {{ padding: 20px 24px; max-width: 1600px; margin: 0 auto; }}

.grid-5 {{ display: grid; grid-template-columns: repeat(5,1fr); gap: 16px; margin-bottom: 20px; }}
.grid-4 {{ display: grid; grid-template-columns: repeat(4,1fr); gap: 16px; margin-bottom: 20px; }}
.grid-3 {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 16px; margin-bottom: 20px; }}
.grid-2 {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }}

/* 카드 */
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

/* 섹션 타이틀 */
.section-title {{
  font-size: 11px; font-weight: 600; letter-spacing: 2px; color: var(--muted);
  text-transform: uppercase; margin-bottom: 14px;
  display: flex; align-items: center; gap: 8px;
}}
.section-title::after {{ content: ''; flex: 1; height: 1px; background: var(--border); }}

/* 차트 */
.chart-container {{ position: relative; height: 220px; }}

/* 테이블 */
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

/* 배지 */
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

/* 분석가 카드 */
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

/* 교훈 */
.lesson-item {{ display: flex; align-items: flex-start; gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--border); }}
.lesson-count {{
  background: rgba(59,130,246,0.2); color: var(--blue); border-radius: 4px;
  padding: 2px 8px; font-family: var(--mono); font-size: 12px; font-weight: 700;
  white-space: nowrap; min-width: 40px; text-align: center;
}}
.lesson-text {{ font-size: 13px; line-height: 1.5; }}

/* 진행 바 */
.mini-bar-wrap {{ background: var(--border); border-radius: 2px; height: 4px; width: 80px; display: inline-block; vertical-align: middle; overflow: hidden; }}
.mini-bar-fill  {{ height: 100%; border-radius: 2px; }}

/* 거래 원장 날짜 그룹 헤더 */
.date-group-row td {{
  background: var(--surface2); color: var(--muted); font-size: 11px;
  letter-spacing: 2px; padding: 6px 12px; border-bottom: 1px solid var(--border);
}}

/* ?? Brain ?곹깭 ?? */
.brain-stats {{ display: grid; grid-template-columns: repeat(3,1fr); gap: 10px; margin-top: 12px; }}
.brain-stat {{
  background: rgba(255,255,255,0.03); border-radius: 8px;
  padding: 10px; text-align: center;
}}
.brain-stat-val {{ font-family: var(--mono); font-size: 20px; font-weight: 700; margin-bottom: 4px; }}
.brain-stat-label {{ font-size: 11px; color: var(--muted); }}

/* 반응형 레이아웃 */
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
        ("/pathb",     "B플랜 실시간"),
        ("/preopen",   "장전 후보"),
        ("/analytics", "분석"),
        ("/logs",      "로그"),
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
    <button class="mkt-btn" data-group="market" id="btn-kr" onclick="setMarket('KR')">한국장 KR</button>
    <button class="mkt-btn" data-group="market" id="btn-us" onclick="setMarket('US')">미국장 US</button>
    <button class="mkt-btn" data-group="mode" id="btn-paper" onclick="setMode('paper')">모의</button>
    <button class="mkt-btn" data-group="mode" id="btn-live" onclick="setMode('live')">실전</button>
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
// 공통 상태
let MARKET = localStorage.getItem('market') || 'KR';
let MODE   = localStorage.getItem('runtime_mode') || 'live';
let PERIOD = localStorage.getItem('period') || 'month';
let DATE_START = localStorage.getItem('date_start') || '';
let DATE_END   = localStorage.getItem('date_end')   || '';

let charts = {};

function setMarket(m) {
  MARKET = m;
  localStorage.setItem('market', m);
  document.querySelectorAll('.mkt-btn[data-group="market"]').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('btn-' + m.toLowerCase());
  if (btn) btn.classList.add('active');
  if (typeof loadAll === 'function') loadAll();
}

function setMode(mode) {
  MODE = (mode === 'live') ? 'live' : 'paper';
  localStorage.setItem('runtime_mode', MODE);
  document.querySelectorAll('.mkt-btn[data-group="mode"]').forEach(b => b.classList.remove('active'));
  const btn = document.getElementById('btn-' + MODE);
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
  p.set('mode', MODE);
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

function apiGet(path, extra = '') {
  const sep = path.includes('?') ? '&' : '?';
  const suffix = extra ? `${extra}&mode=${encodeURIComponent(MODE)}` : `mode=${encodeURIComponent(MODE)}`;
  return fetch(path + sep + suffix);
}

function apiPost(path, body = {}) {
  return fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({...body, mode: MODE}),
  });
}

document.addEventListener('DOMContentLoaded', () => {
  const marketBtn = document.getElementById('btn-' + MARKET.toLowerCase());
  if (marketBtn) marketBtn.classList.add('active');
  const modeBtn = document.getElementById('btn-' + MODE);
  if (modeBtn) modeBtn.classList.add('active');
});

// ?? ?щ㎎???????????????????????????????????????????????????????????????????????
const fmt = {
  pct:   v => (v >= 0 ? '+' : '') + Number(v).toFixed(2) + '%',
  krw:   v => (v >= 0 ? '+' : '') + Math.round(v).toLocaleString() + '원',
  price: v => Math.round(v).toLocaleString() + '원',   // 가격 표시 (부호 없음)
  asset: v => Math.round(v).toLocaleString() + '원',
  num:   v => Number(v).toLocaleString(),
};

function fmtUsd(v) {
  return '$' + Number(v || 0).toFixed(2);
}

function formatUsdKrwSplit(usd, krw, label = 'US') {
  const usdNum = Number(usd || 0);
  const krwNum = Number(krw || 0);
  if (usdNum > 0) return `${label} ${fmtUsd(usdNum)} (≈${fmt.asset(krwNum)})`;
  if (krwNum > 0) return `${label} ${fmt.asset(krwNum)}`;
  return '';
}

function formatAssetBreakdown(today) {
  const parts = [];
  const krAsset = Number(today.asset_krw_kr || 0);
  const usText = formatUsdKrwSplit(today.asset_usd_total, today.asset_krw_us);
  if (krAsset > 0) parts.push(`KR ${fmt.asset(krAsset)}`);
  if (usText) parts.push(usText);
  return parts.join(' | ');
}

function formatOrderableBreakdown(today) {
  const parts = [];
  const krOrderable = Number(today.broker_orderable_cash_kr || 0);
  const usText = formatUsdKrwSplit(today.broker_orderable_cash_usd, today.broker_orderable_cash_us_krw, 'US');
  if (krOrderable > 0) parts.push(`KR ${fmt.asset(krOrderable)}`);
  if (usText) parts.push(usText);
  return parts.join(' | ');
}

const MODE_KO = {
  AGGRESSIVE: '공격매수',
  MODERATE_BULL: '강한상승',
  Bull_Confirmed: '상승확인',
  MILD_BULL: '완만상승',
  CAUTIOUS: '중립경계',
  NEUTRAL: '중립',
  MILD_BEAR: '완만하락',
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
  BULL: '상승',
  BEAR: '약세',
  UNKNOWN: '미확인',
  unknown: '미확인',
};

const MODE_DESC = {
  AGGRESSIVE: '강한 리스크온, 진입 적극',
  MODERATE_BULL: '상승 우위, 공격성 높음',
  MILD_BULL: '완만한 상승 우위',
  CAUTIOUS: '상승 쪽이지만 변동성 경계',
  NEUTRAL: '방향성 혼조, 기본 모드',
  MILD_BEAR: '약한 하락 우위',
  CAUTIOUS_BEAR: '하락·변동성 확대 경계',
  DEFENSIVE: '방어 중심, 신규 진입 제한',
  HALT: '사실상 신규 진입 중단',
};

function koMode(v) { return MODE_KO[v] || v || '-'; }
function modeDescription(v) { return MODE_DESC[v] || ''; }
function koStrategy(v) { return STRATEGY_KO[v] || v || '-'; }
function koRegime(v) { return REGIME_KO[v] || MODE_KO[v] || v || '-'; }

function renderAdaptive(t, titleId, metaId, breakdownId) {
  const adaptive = t.adaptive || {};
  const titleEl = document.getElementById(titleId);
  const metaEl  = document.getElementById(metaId);
  const bdEl    = document.getElementById(breakdownId);
  if (titleEl) {
    const claudeAdjCount = (adaptive.rows || []).filter(r => r.claude_adjusted).length;
    const claudeBadge = claudeAdjCount > 0 ? ` · Claude 조정 ${claudeAdjCount}개` : '';
    titleEl.textContent = adaptive.enabled
      ? `현재 모드 ${koMode(t.mode || '-')} · 전략 ${(adaptive.rows || []).length}개${claudeBadge}`
      : 'Adaptive 파라미터 비활성';
  }
  if (metaEl) {
    const changedCount  = (adaptive.rows || []).filter(r => (r.changed || []).length > 0).length;
    const perfSources   = Array.from(new Set((adaptive.rows || []).map(r => r.perf_source || 'none'))).join(', ');
    const claudeRows    = (adaptive.rows || []).filter(r => r.claude);
    const claudeInfo    = claudeRows.length
      ? ` · Claude 검토 ${claudeRows.length}개 (${claudeRows.filter(r => r.claude_adjusted).length}개 조정)`
      : '';
    metaEl.textContent = adaptive.enabled
      ? `적응형 조정 ${changedCount}개 · 성과 소스 ${perfSources || 'none'}${claudeInfo}`
      : '--';
  }
  if (bdEl) {
    bdEl.innerHTML = adaptive.enabled
      ? (adaptive.rows || []).map(r => {
          const stratKo   = koStrategy(r.strategy || '') || r.strategy || '-';
          const state     = r.disabled ? ' <span style="color:#ef4444">비활성</span>' : '';
          const adaptChg  = (r.changed || []).length
            ? `<span style="color:#fbbf24"> △${r.changed.join(',')}</span>` : '';
          let claudePart  = '';
          if (r.claude) {
            if (r.claude_adjusted && (r.claude_changed || []).length > 0) {
              claudePart = ` <span style="color:#94a3b8">→ Claude</span> ${r.claude}`
                + `<span style="color:#a78bfa"> ✏${r.claude_changed.join(',')}</span>`;
              if (r.claude_reason) {
                claudePart += ` <span style="color:#64748b;font-size:10px">(${r.claude_reason})</span>`;
              }
            } else {
              claudePart = ` <span style="color:#64748b">→ Claude 유지</span>`;
            }
          }
          const reasons   = [].concat(r.regime_reasons || []).concat(r.perf_reason ? [r.perf_reason] : []);
          const reasonTxt = reasons.length
            ? ` <span style="color:#64748b;font-size:10px">[${reasons.join(' / ')}]</span>` : '';
          return `<div style="margin-bottom:3px">`
            + `<span style="color:#e2e8f0;font-weight:600">${stratKo}</span>${state} `
            + `<span style="color:#64748b">기본</span> ${r.base || '-'}${adaptChg}`
            + ` <span style="color:#64748b">→ 적응형</span> ${r.final || '-'}`
            + `${claudePart}${reasonTxt}`
            + `</div>`;
        }).join('')
      : '--';
  }
}

function colorClass(v) {
  return v > 0 ? 'up' : v < 0 ? 'down' : 'neutral-color';
}
function colorVar(v) {
  return v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : 'var(--muted)';
}

// ?? ?쒓퀎 ???????????????????????????????????????????????????????????????????????
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

// 초기 상태 복원
(function initState() {
  // 마켓 버튼
  const btn = document.getElementById('btn-' + MARKET.toLowerCase());
  if (btn) btn.classList.add('active');

  // 기간 버튼
  document.querySelectorAll('.period-btn').forEach(b => {
    b.classList.toggle('active', b.dataset.p === PERIOD);
  });

  // 날짜 입력 복원
  const ds = document.getElementById('date-start');
  const de = document.getElementById('date-end');
  if (ds && DATE_START) ds.value = DATE_START;
  if (de && DATE_END)   de.value = DATE_END;
})();

// 자동 새로고침
setTimeout(() => {
  if (typeof loadAll === 'function') setInterval(loadAll, 30000);
}, 1000);
</script>
"""


# 페이지 1: 오늘 현황

PAGE_TODAY_HTML = """
<main>

<!-- 5 요약 카드 -->
<div class="grid-5">
  <div class="card cyan">
    <div class="card-label">오늘 손익</div>
    <div class="card-value" id="today-pnl">--</div>
    <div class="card-sub"  id="today-krw">--</div>
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
    <div class="card-sub"  id="win-detail">--</div>
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
    <div class="card-sub"  id="credit-calls" style="margin-top:4px">호출: --</div>
    <div class="card-sub"  id="credit-remaining" style="margin-top:4px">예산 기준 잔여: --</div>
  </div>
</div>

<!-- 보유 포지션 -->
<div class="section-title" style="margin-top:24px">보유 포지션<span id="position-count" style="font-size:12px;color:var(--text-dim);font-weight:400;margin-left:8px"></span></div>
<div class="section-title" style="margin-top:18px">ML DB 수집 현황</div>
<div class="card" style="padding:16px;margin-bottom:20px">
  <div id="ml-db-title" style="font-size:14px;font-weight:700;color:var(--text)">확인 중..</div>
  <div id="ml-db-meta" style="font-size:12px;color:var(--text-dim);margin-top:8px">--</div>
  <div id="ml-db-breakdown" style="font-size:11px;color:#94a3b8;margin-top:6px;line-height:1.6">--</div>
</div>
<div class="section-title" style="margin-top:-4px">Adaptive 파라미터</div>
<div class="card" style="padding:16px;margin-bottom:20px">
  <div id="adaptive-title" style="font-size:14px;font-weight:700;color:var(--text)">확인 중..</div>
  <div id="adaptive-meta" style="font-size:12px;color:var(--text-dim);margin-top:8px">--</div>
  <div id="adaptive-breakdown" style="font-size:11px;color:#94a3b8;margin-top:6px;line-height:1.7">--</div>
</div>
<div id="holdings-entry-bar" style="display:flex;align-items:center;gap:16px;padding:10px 14px;margin-bottom:12px;background:var(--card);border-radius:8px;border:1px solid var(--border);font-size:13px">
  <span style="color:var(--text-dim)">보유</span>
  <span id="bar-position-count" style="font-weight:700;color:var(--text)">--</span>
  <span style="color:var(--border)">|</span>
  <span style="color:var(--text-dim)">오늘 진입</span>
  <span id="bar-entry-count" style="font-weight:700;color:var(--text)">--</span>
</div>
<div id="position-board" style="display:flex;flex-wrap:nowrap;gap:10px;margin-bottom:20px;overflow-x:auto;overflow-y:hidden;padding-bottom:6px;scrollbar-width:thin"></div>

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

<!-- 오늘 모니터링 종목 -->
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

<div class="section-title" style="margin-top:8px">
  오늘 종목
  <span id="ticker-meta" style="font-size:11px;color:var(--text-dim);margin-left:12px;font-weight:400"></span>
</div>
<div id="ticker-quick-board" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px"></div>
<div id="ticker-initial-board" style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:10px"></div>
<div id="ticker-history-board" style="display:flex;flex-direction:column;gap:8px;margin-bottom:12px"></div>
<div id="ticker-board" style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:20px"></div>

<!-- 실시간 신호 피드 -->
<div class="section-title" style="margin-top:4px">
  실시간 신호 피드
  <span id="signal-ts" style="font-size:11px;color:var(--text-dim);margin-left:12px;font-weight:400"></span>
</div>
<div class="card" style="padding:0;overflow:hidden">
  <div style="display:flex;gap:12px;padding:10px 16px;background:var(--surface2);border-bottom:1px solid var(--border);font-size:11px;color:var(--text-dim);font-family:var(--mono)">
    <span>진입신호</span><span>신호없음</span><span>가격오류</span><span>예산/차단</span><span>보유중</span>
  </div>
  <div id="signal-feed-cards" style="display:flex;flex-wrap:wrap;gap:10px;padding:12px 12px 0 12px"></div>
  <div id="signal-feed" style="max-height:320px;overflow-y:auto;font-family:var(--mono);font-size:12px"></div>
</div>

</main>

<script>

async function loadSummary() {
  return;
}

async function loadJudgments() {
  const d = await apiGet('/api/judgments', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({}));
  if (!d.bull) return;

  function analystCard(info, label, iconClass, stanceClass) {
    const conf = Math.round((info.confidence || 0) * 100);
    const res  = info.result || '';
    const rb   = res === 'HIT' ? 'hit' : res === 'MISS' ? 'miss' : 'partial';
    const barC = iconClass === 'bull' ? 'var(--green)' : iconClass === 'bear' ? 'var(--red)' : 'var(--yellow)';
    // 1라운드와 토론 후 스탠스 변경 여부 표시
    const r1stance = info.r1_stance || '';
    const debateHtml = (r1stance && r1stance !== info.stance)
      ? `<div style="font-size:11px;color:#f59e0b;margin-top:4px">토론 후 변경: ${r1stance} → <b>${info.stance}</b></div>`
      : (r1stance ? `<div style="font-size:11px;color:#475569;margin-top:4px">토론 후 유지: ${info.stance}</div>` : '');
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
      <div class="analyst-reason">핵심 ${info.key_reason || '-'}</div>
      ${debateHtml}
      ${info.why ? `<div class="postmortem">사후판정 ${info.why}</div>` : ''}
    </div>`;
  }

  const sec = document.getElementById('analyst-section');
  sec.innerHTML =
    analystCard(d.bull,    '상승 분석가', 'bull', 'stance-bull') +
    analystCard(d.bear,    '하락 분석가', 'bear', 'stance-bear') +
    analystCard(d.neutral, '중립 분석가', 'neut', 'stance-neut');

  if (d.lesson) {
    sec.innerHTML += `<div class="lesson-box" style="grid-column:1/-1">오늘의 교훈: ${d.lesson}</div>`;
  }
}

async function loadEquityChart() {
  const d = await apiGet('/api/chart/equity', 'market=' + encodeURIComponent(MARKET) + '&period=3month').then(r => r.json()).catch(() => ({}));
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
          type: 'bar', label: '일간 손익%', data: d.pnl,
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
  const d = await apiGet('/api/chart/market-context', 'market=' + encodeURIComponent(MARKET) + '&period=3month').then(r => r.json()).catch(() => ({}));
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
  document.getElementById('credit-calls').textContent = `호출: ${td.calls}회`;
  const remainingLine = document.getElementById('credit-remaining');
  if (remainingLine) {
    const daily = budget.daily_remaining_usd;
    const monthly = budget.monthly_remaining_usd;
    if (daily != null || monthly != null) {
      remainingLine.style.display = '';
      const parts = [];
      if (daily != null) parts.push(`일간 $${Number(daily).toFixed(3)}`);
      if (monthly != null) parts.push(`월간 $${Number(monthly).toFixed(3)}`);
      remainingLine.textContent = `예산 기준 잔여: ${parts.join(' / ')}`;
    } else {
      remainingLine.textContent = '';
      remainingLine.style.display = 'none';
    }
  }

  document.getElementById('credit-detail').innerHTML = `
    <div><span style="color:var(--muted)">오늘 입력</span> ${td.input.toLocaleString()} tok</div>
    <div><span style="color:var(--muted)">오늘 출력</span> ${td.output.toLocaleString()} tok</div>
    <div><span style="color:var(--muted)">오늘 비용</span> <span style="color:var(--purple)">$${td.cost_usd.toFixed(4)} · ${td.cost_krw.toLocaleString()}원</span></div>
    <div style="border-top:1px solid var(--border);margin:4px 0"></div>
    <div><span style="color:var(--muted)">누적 비용</span> <span style="color:var(--cyan)">$${tot.cost_usd.toFixed(4)}</span></div>
    <div><span style="color:var(--muted)">누적 입력</span> ${tot.input.toLocaleString()} tok</div>
    <div><span style="color:var(--muted)">누적 출력</span> ${tot.output.toLocaleString()} tok</div>
    <div><span style="color:var(--muted)">예산 잔여</span> ${
      budget.daily_remaining_usd != null || budget.monthly_remaining_usd != null
        ? `${budget.daily_remaining_usd != null ? `일간 $${Number(budget.daily_remaining_usd).toFixed(3)}` : ''}${budget.daily_remaining_usd != null && budget.monthly_remaining_usd != null ? ' / ' : ''}${budget.monthly_remaining_usd != null ? `월간 $${Number(budget.monthly_remaining_usd).toFixed(3)}` : ''}`
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
  const quickBoard = document.getElementById('ticker-quick-board');
  const initialBoard = document.getElementById('ticker-initial-board');
  const historyBoard = document.getElementById('ticker-history-board');
  const meta = document.getElementById('ticker-meta');
  if (!board) return;
  const fmtNoneReason = (txt) => String(txt || '')
    .replace(/✓/g, '<span style="color:#22c55e;font-weight:700">✓</span>')
    .replace(/✗/g, '<span style="color:#ef4444;font-weight:700">✗</span>');
  const d = await apiGet('/api/tickers/today', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({}));
  const tickers = d.tickers || [];
  if (meta) {
    meta.textContent = tickers.length
      ? `${tickers.length}개 · 모드 ${koMode(d.mode || '-')}`
      : '종목 데이터 없음';
  }
  if (tickers.length === 0) {
    board.innerHTML = '<span style="color:var(--text-dim);font-size:13px">종목 데이터 없음</span>';
    return;
  }

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
    'entry_skip':    { icon: '⏸', label: '진입보류', color: '#f59e0b' },
    'signal_check':  { icon: '⬜', label: '신호없음', color: '#64748b' },
    'waiting':       { icon: '⏳', label: '대기중',   color: '#cbd5e1' },
    'cycle_error':   { icon: '⚠', label: '처리오류', color: '#ef4444' },
  };

  if (quickBoard) {
    quickBoard.innerHTML = tickers.map(t => {
      const ev = EVENT_MAP[t.last_event] || EVENT_MAP['waiting'];
      const displayPrice = Number(t.display_price || 0);
      const priceText = displayPrice > 0
        ? (MARKET === 'KR' ? Math.round(displayPrice).toLocaleString() + '원' : '$' + displayPrice.toFixed(2))
        : '--';
      const pickStatus = t.selection_status_ko ? ` · ${t.selection_status_ko}` : '';
      const statusText = Number(t.sig_count || 0) > 0 ? `신호 ${t.sig_count}` : `${ev.label}${pickStatus}`;
      return `
        <div class="card" style="padding:10px 12px;min-width:132px;flex:0 0 auto;border-color:${ev.color}33">
          <div style="display:flex;align-items:center;gap:6px;margin-bottom:4px">
            <span style="font-size:12px">${ev.icon}</span>
            <span style="font-size:12px;font-weight:700;color:var(--text)">${t.display_ticker || t.ticker}</span>
          </div>
          <div style="font-family:var(--mono);font-size:12px;color:var(--text);margin-bottom:3px">${priceText}</div>
          <div style="font-size:10px;color:${ev.color}">${statusText}</div>
        </div>`;
    }).join('');
  }

  const initialTickers = d.initial_tickers || [];
  if (initialBoard) {
    if (!initialTickers.length) {
      initialBoard.innerHTML = '';
    } else {
      initialBoard.innerHTML = `
        <div style="width:100%;font-size:11px;color:var(--text-dim);margin-bottom:2px">세션 시작 종목</div>
        ${initialTickers.map(t => `
          <div class="card" style="padding:8px 10px;min-width:120px;flex:0 0 auto;background:rgba(148,163,184,0.08)">
            <div style="font-size:11px;font-weight:700;color:var(--text)">${t}</div>
          </div>
        `).join('')}
      `;
    }
  }

  const hist = d.watchlist_history || [];
  if (historyBoard) {
    if (!hist.length) {
      historyBoard.innerHTML = '';
    } else {
      const lastHist = hist.slice(-6).reverse();
      const labelMap = {
        initial: '초기선정',
        rescreen: '수동 재선정',
        scheduled: '정시 재스크리닝',
        partial: '부분교체',
      };
      historyBoard.innerHTML = `
        <div style="font-size:11px;color:var(--text-dim);margin-bottom:2px">오늘 변경 이력</div>
        ${lastHist.map(h => {
          const ts = (h.ts || '').slice(11, 16);
          if (h.kind === 'partial') {
            const outText = (h.out || []).join(', ') || '-';
            const inText = (h.in || []).join(', ') || '-';
            return `<div class="card" style="padding:10px 12px;background:rgba(15,23,42,0.55)">
              <div style="font-size:11px;color:#94a3b8;margin-bottom:4px">${ts} · ${labelMap[h.kind] || h.kind}</div>
              <div style="font-size:12px;color:#fca5a5">OUT: ${outText}</div>
              <div style="font-size:12px;color:#86efac">IN: ${inText}</div>
            </div>`;
          }
          const tickText = (h.tickers || []).join(', ');
          return `<div class="card" style="padding:10px 12px;background:rgba(15,23,42,0.55)">
            <div style="font-size:11px;color:#94a3b8;margin-bottom:4px">${ts} · ${labelMap[h.kind] || h.kind}</div>
            <div style="font-size:12px;color:var(--text)">${tickText}</div>
          </div>`;
        }).join('')}
      `;
    }
  }

  board.innerHTML = tickers.map(t => {
    const ev = EVENT_MAP[t.last_event] || EVENT_MAP['waiting'];
    const displayPrice = Number(t.display_price || 0);
    const avgPrice = Number(t.avg_price || 0);
    const currentPrice = Number(t.current_price || 0);
    let pendingExplain = '';
    const reasons = t.skip_reasons || [];
    const shownPriceStr = displayPrice > 0
      ? (MARKET === 'KR' ? Math.round(displayPrice).toLocaleString() + '원' : '$' + displayPrice.toFixed(2))
      : '--';
    const priceStr = displayPrice > 0
      ? (MARKET === 'KR' ? Math.round(t.last_price).toLocaleString() + '원' : '$' + t.last_price.toFixed(2))
      : '--';
    const sigBadge = t.sig_count > 0
      ? `<span style="background:#10b981;color:#000;border-radius:3px;padding:1px 5px;font-size:10px;margin-left:4px">신호 ${t.sig_count}</span>`
      : '';
    const selectBadge = t.selection_status
      ? `<span style="margin-left:6px;background:${t.selection_status === 'TRADE_READY' ? 'rgba(16,185,129,0.18)' : 'rgba(245,158,11,0.18)'};color:${t.selection_status === 'TRADE_READY' ? '#34d399' : '#fbbf24'};border-radius:999px;padding:1px 6px;font-size:10px;font-weight:700">${t.selection_status_ko || t.selection_status}</span>`
      : '';
    const heldEntries = Number(t.held_entries || 0);
    const heldQty = Number(t.held_qty || 0);
    const pendingCount = Number(t.pending_count || 0);
    const pyramidLimit = Number(t.pyramid_limit || 0);
    const statusBits = [];
    if (heldEntries > 0) statusBits.push(`보유 ${heldEntries}건`);
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
    const noneReasonHtml = (t.last_event === 'signal_check' && t.none_reason)
      ? `<div style="font-size:10px;color:#94a3b8;margin-top:4px;line-height:1.4">${fmtNoneReason(t.none_reason)}</div>`
      : '';
    if (reasons.includes('already_holding') && reasons.includes('pending_order')) {
      pendingExplain = `추가 진입 보류: 이미 보유 중 · 미체결 주문 ${pendingCount}건`;
    } else if (reasons.includes('already_holding')) {
      pendingExplain = '추가 진입 보류: 이미 보유 중';
    } else if (reasons.includes('pending_order')) {
      pendingExplain = `추가 진입 보류: 미체결 주문 ${pendingCount}건 존재`;
    } else if (reasons.length > 0) {
      pendingExplain = reasons.join(' / ');
    }
    const skipHtml = pendingExplain
      ? `<div style="font-size:10px;color:#f87171;margin-top:4px">${pendingExplain}</div>`
      : '';
    const skipHtmlFinal = skipHtml;
    return `<div style="background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:12px 16px;min-width:160px;max-width:280px;cursor:default">
      <div style="font-family:var(--mono);font-weight:700;font-size:14px;color:#e2e8f0">${t.display_ticker || t.ticker}${sigBadge}${selectBadge}</div>
      <div style="font-size:12px;color:var(--text-dim);margin-top:4px">${shownPriceStr}</div>
      <div style="margin-top:6px;font-size:12px;color:${ev.color}">${ev.icon} ${ev.label}</div>
      <div style="font-size:10px;color:#475569;margin-top:2px">${t.last_ts}${t.last_reason ? ' · ' + t.last_reason : ''}</div>
      ${statusHtml}
      ${priceMetaHtml}
      ${skipHtmlFinal}
      ${noneReasonHtml}
      ${selectReasonHtml}
    </div>`;
  }).join('');

  // ?좏깮 ?????꾨낫 紐⑸줉 (?묓엺 ?뺥깭)
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
    apiGet('/api/signals/recent', 'market=' + encodeURIComponent(MARKET) + '&n=60').then(r => r.json()).catch(() => ([])),
    apiGet('/api/tickers/today', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({})),
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
    trailing: '추적 중',
    waiting: '대기중',
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
    session_close: '세션 종료 청산',
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
        if (heldEntries > 0) statusBits.push(`보유 ${heldEntries}건`);
        if (heldQty > 0) statusBits.push(`수량 ${heldQty}`);
        if (pendingCount > 0) statusBits.push(`미체결 ${pendingCount}건`);
        return `<div style="background:var(--surface2);border:1px solid rgba(59,130,246,0.35);border-radius:8px;padding:12px 14px;min-width:170px;max-width:260px;flex:1">
          <div style="display:flex;justify-content:space-between;gap:8px;align-items:center">
            <div style="font-family:var(--mono);font-weight:700;font-size:14px;color:#e2e8f0">${t.display_ticker || t.ticker}</div>
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
      feed.innerHTML = '<div style="padding:20px;text-align:center;color:var(--text-dim)">신호 이력도 없고 선택 종목과 보유 상태도 아직 없습니다</div>';
      return;
    }
    const fallbackRows = tickerList.map(t => {
      const price = Number(t.display_price || t.current_price || t.avg_price || t.last_price || 0);
      const priceStr = price > 0 ? (MARKET === 'KR' ? Math.round(price).toLocaleString() + '원' : '$' + price.toFixed(2)) : '';
      const heldEntries = Number(t.held_entries || 0);
      const heldQty = Number(t.held_qty || 0);
      const pendingCount = Number(t.pending_count || 0);
      let detail = '신호 없음';
      if (heldEntries > 0 && pendingCount > 0) detail = `보유 ${heldEntries}건 · 수량 ${heldQty} · 미체결 ${pendingCount}건`;
      else if (heldEntries > 0) detail = `보유 ${heldEntries}건 · 수량 ${heldQty} · 최대 ${t.pyramid_limit || 8}회`;
      else if (pendingCount > 0) detail = `미체결 ${pendingCount}건 대기중`;
      return `<div style="display:flex;align-items:center;gap:10px;padding:6px 16px;background:transparent;border-bottom:1px solid rgba(31,41,55,0.4)">
        <span style="color:var(--text-dim);min-width:56px">${t.last_ts || '--:--'}</span>
        <span>⬜</span>
        <span style="min-width:36px;color:#94a3b8">${MARKET}</span>
        <span style="min-width:80px;color:#cbd5e1;font-weight:600">${t.display_ticker || t.ticker || ''}</span>
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
    if (heldEntries > 0) parts.push(`보유 ${heldEntries}건 · 수량 ${heldQty}`);
    if (pendingCount > 0) parts.push(`미체결 ${pendingCount}건`);
    if (!parts.length) parts.push('상태 없음');
    return `<div style="display:flex;align-items:center;gap:10px;padding:6px 16px;background:rgba(59,130,246,0.08);border-bottom:1px solid rgba(31,41,55,0.4)">
      <span style="color:var(--text-dim);min-width:56px">${t.last_ts || '--:--'}</span>
      <span>🔵</span>
      <span style="min-width:36px;color:#94a3b8">${MARKET}</span>
      <span style="min-width:80px;color:#93c5fd;font-weight:700">${t.display_ticker || t.ticker || ''}</span>
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
      if (reason === 'invalid_price') { icon = '❌'; bg = 'rgba(239,68,68,0.08)'; textColor = '#ef4444'; }
      else if (reason === 'already_holding') { icon = '🔵'; bg = 'rgba(59,130,246,0.08)'; textColor = '#3b82f6'; }
      else { icon = '⏸'; bg = 'rgba(245,158,11,0.08)'; textColor = '#f59e0b'; }
    } else if (event === 'signal_blocked') {
      icon = '🚫'; bg = 'rgba(245,158,11,0.08)'; textColor = '#f59e0b';
    }

    const priceStr = price > 0 ? (market === 'KR' ? Math.round(price).toLocaleString() + '원' : '$' + price.toFixed(2)) : '';
    let detail   = reason ? koReason(reason) : (mode ? koMode(mode) : '');
    const eventNoneReason = (event === 'signal_check')
      ? String(ev.detail || tickerInfo.none_reason || '')
      : '';
    const noneReasonHtml = eventNoneReason
      ? `<span style="color:#94a3b8"> · ${eventNoneReason}</span>`
      : '';
    const noneReasonHtmlColored = noneReasonHtml
      .replace(/✓/g, '<span style="color:#22c55e;font-weight:700">✓</span>')
      .replace(/✗/g, '<span style="color:#ef4444;font-weight:700">✗</span>');
    if (reason === 'already_holding') {
      detail = `보유 ${tickerInfo.held_entries ?? 0}건 · 최대 ${tickerInfo.pyramid_limit ?? summary.pyramid_limit ?? 8}회 · 미체결 ${tickerInfo.pending_count ?? 0}건`;
    } else if (reason === 'pending_order') {
      detail = `미체결 ${tickerInfo.pending_count ?? 0}건 · 보유 ${tickerInfo.held_entries ?? 0}건 / 최대 ${tickerInfo.pyramid_limit ?? summary.pyramid_limit ?? 8}회`;
    }

    return `<div style="display:flex;align-items:center;gap:10px;padding:6px 16px;background:${bg};border-bottom:1px solid rgba(31,41,55,0.4)">
      <span style="color:var(--text-dim);min-width:56px">${ts}</span>
      <span>${icon}</span>
      <span style="min-width:36px;color:#94a3b8">${market}</span>
      <span style="min-width:96px;color:${textColor};font-weight:600">${ticker} · ${koEvent(event)}</span>
      <span style="min-width:72px;color:var(--text-dim)">${priceStr}</span>
      <span style="color:var(--text-dim)">${detail}${noneReasonHtmlColored}</span>
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
  const d = await apiGet('/api/summary', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({}));
  if (!d.today) return;
  const t = d.today, p = d.period;
  const rawAssetSource = String(t.asset_source || '');
  const assetSourceLabel = (MODE === 'live' && rawAssetSource.includes('paper'))
    ? '브로커 기준'
    : ({
    broker: '브로커 기준',
    'broker+paper_us_cash_estimated': '브로커 기준 · US 모의현금 추정',
    'broker+paper_us_cash_estimated+live_fallback': '브로커 기준 · US 모의보정',
    'broker+live_fallback': '브로커 기준 · 라이브 보정',
    internal_fallback: '엔진 추정',
  }[rawAssetSource] || (rawAssetSource || '기준 미상'));

  const pnlEl = document.getElementById('today-pnl');
  pnlEl.textContent = fmt.pct(t.pnl_pct);
  pnlEl.className = 'card-value ' + colorClass(t.pnl_pct);
  document.getElementById('today-krw').textContent = fmt.krw(t.pnl_krw);
  document.getElementById('cumulative').textContent = fmt.asset(t.cumulative);
  const cumulativePnl = document.getElementById('cumulative-pnl');
  if (cumulativePnl) {
    const assetBreakdown = formatAssetBreakdown(t);
    cumulativePnl.textContent =
      `실현 ${fmt.krw(t.realized_pnl_krw || 0)} · 평가 ${fmt.krw(t.unrealized_pnl_krw || 0)}${assetBreakdown ? ` · ${assetBreakdown}` : ''} · 자산 ${assetSourceLabel}`;
  }
  const orderBreakdown = formatOrderableBreakdown(t);
  document.getElementById('today-mode').innerHTML =
    `모드: <span class="mode-badge mode-${t.mode}">${koMode(t.mode)}</span>&nbsp; 거래 ${t.trades}건${t.mode_order_limit_krw ? ` | 최대매수 ${fmt.asset(t.mode_order_limit_krw)}` : ''}${orderBreakdown ? ` | 주문가능 ${orderBreakdown}` : ''}${t.execution_contaminated ? ` <span style="color:#f59e0b">| 실행오염 ${((t.execution_issues||[]).slice(0,2)).join(', ')}</span>` : ''}`;

  const wrEl = document.getElementById('win-rate');
  wrEl.textContent = p.win_rate + '%';
  wrEl.className = 'card-value ' + (p.win_rate >= 55 ? 'up' : p.win_rate >= 45 ? 'neutral-color' : 'down');
  document.getElementById('win-detail').textContent = `${p.wins}승 / ${p.losses}패 (${p.days}일)`;

  const emoji = p.streak_type === 'win' ? '🟢' : '🔴';
  document.getElementById('streak-val').innerHTML =
    `<span style="font-size:20px">${emoji}</span> ${p.streak}연속`;
  document.getElementById('total-pnl').textContent = `누적: ${fmt.pct(p.total_pnl)}`;

  const positions = t.positions || [];
  const posBoard = document.getElementById('position-board');
  const posCount = document.getElementById('position-count');
  if (posCount) {
    posCount.textContent = positions.length
      ? `${positions.length}/${t.position_limit || 0}개 보유중 · 추가 여력 ${t.position_remaining || 0}${t.live_updated ? ' · ' + t.live_updated : ''}`
      : `보유 없음 · 추가 여력 ${t.position_remaining || 0}${t.live_updated ? ' · ' + t.live_updated : ''}`;
  }
  const ml = t.ml_db || {};
  const mlTitle = document.getElementById('ml-db-title');
  const mlMeta = document.getElementById('ml-db-meta');
  const mlBreakdown = document.getElementById('ml-db-breakdown');
  if (mlTitle) {
    mlTitle.textContent = ml.enabled
      ? `누적 ${ml.total || 0}건 · 오늘 ${ml.today || 0}건`
      : 'ML DB 비활성';
  }
  if (mlMeta) {
    mlMeta.textContent = ml.enabled
      ? `BUY_SIGNAL ${ml.buy_signal || 0}건 · 체결 ${ml.filled || 0}건 · 결과기록 ${ml.with_outcome || 0}건 · 마지막 ${ml.last_ts || '--'}`
      : '--';
  }
  if (mlBreakdown) {
    const recentDays = (ml.recent_days || []).map(x => `${String(x.date || '').slice(5)} ${x.count || 0}`).join(' · ');
    mlBreakdown.textContent = ml.enabled
      ? `forward 준비 ${ml.forward_ready || 0}건 (${Number(ml.forward_ready_rate || 0).toFixed(1)}%)${recentDays ? ' · 최근7일 ' + recentDays : ''}`
      : '--';
  }
  renderAdaptive(t, 'adaptive-title', 'adaptive-meta', 'adaptive-breakdown');

  // 보유/진입 상태 바
  const barPos = document.getElementById('bar-position-count');
  const barEnt = document.getElementById('bar-entry-count');
  if (barPos) barPos.textContent = `${t.position_count ?? positions.length}/${t.position_limit || 0}개`;
  if (barEnt) barEnt.textContent = `${t.entries_today ?? 0}/${t.max_daily_entries || 0}회`;

  if (!posBoard) return;
  if (positions.length === 0) {
    posBoard.innerHTML = '<div style="color:var(--text-dim);font-size:13px;padding:8px 0">현재 보유 포지션이 없습니다</div>';
  } else {
  posBoard.innerHTML = positions.map(pos => {
    const isKR    = MARKET === 'KR';
    const usdKrw  = Number(t.usd_krw || t.usdKrw || 0);
    const storageCurrency = String(pos.currency || pos.display_currency || '').toUpperCase();
    const nativePrice = (displayValue, rawValue, fallbackValue = 0) => {
      const display = Number(displayValue || 0);
      if (display > 0) return display;
      const raw = Number(rawValue || fallbackValue || 0);
      if (!(raw > 0)) return 0;
      if (!isKR && storageCurrency !== 'USD' && usdKrw > 0) return raw / usdKrw;
      return raw;
    };
    const avgPrice = nativePrice(pos.display_avg_price, pos.avg_price, pos.entry);
    const curPrice = nativePrice(pos.display_current_price, pos.current_price, pos.avg_price || pos.entry);
    const qty      = Number(pos.qty || 0);
    const pnl      = avgPrice > 0 ? ((curPrice / avgPrice) - 1) * 100 : Number(pos.pnl_pct || 0);
    const pnlColor = pnl > 0 ? 'var(--green)' : pnl < 0 ? 'var(--red)' : 'var(--text-dim)';
    const fmtEntryPx = v => {
      if (!(v > 0)) return '--';
      if (isKR) return fmt.price(v);
      const krw = usdKrw > 0 ? ` (약 ${fmt.price(v * usdKrw)})` : '';
      return '$' + v.toFixed(2) + krw;
    };
    const entry   = fmtEntryPx(avgPrice);
    const cur     = fmtEntryPx(curPrice);
    // 수수료 차감 실손익
    const feeBuy  = avgPrice * qty * 0.00015;
    const feeSell = curPrice * qty * (isKR ? 0.00195 : 0.00015);
    const netPnl  = (curPrice - avgPrice) * qty - feeBuy - feeSell;
    const netStr  = isKR
      ? (netPnl >= 0 ? '+' : '') + Math.round(netPnl).toLocaleString() + '원'
      : (netPnl >= 0 ? '+$' : '-$') + Math.abs(netPnl).toFixed(2);
    const netColor = netPnl >= 0 ? 'var(--green)' : 'var(--red)';
    // 전략명 정제
    const stratRaw = pos.strategy || '';
    const stratLabel = !stratRaw || stratRaw === 'broker_balance' || stratRaw === 'broker_sync' ? '' : stratRaw;
    // 종목명 (display_ticker에 이미 이름 없으면 name 표시)
    const tickerDisp = pos.display_ticker || pos.ticker;
    const nameDisp   = pos.name && !tickerDisp.includes(pos.name) ? pos.name : '';
    // 트레일링 / SL / TP
    const isTrailing = pos.trailing;
    const trailSlRaw = Number(pos.trail_sl || 0);
    const trailPct   = Number(pos.trail_pct || 0);
    const slRaw = Number(pos.sl || 0);
    const tpRaw = Number(pos.tp || 0);
    const trailSl = !isKR && usdKrw > 0 && trailSlRaw > 0 ? trailSlRaw / usdKrw : trailSlRaw;
    const sl = !isKR && usdKrw > 0 && slRaw > 0 ? slRaw / usdKrw : slRaw;
    const tp = !isKR && usdKrw > 0 && tpRaw > 0 ? tpRaw / usdKrw : tpRaw;
    const tpPriceRaw = Number(pos.tp_price || 0);
    const tpPrice = !isKR && usdKrw > 0 && tpPriceRaw > 0 ? tpPriceRaw / usdKrw : tpPriceRaw;
    const tpTriggered = !!pos.tp_triggered;
    const fmtPx = v => {
      if (!(v > 0)) return null;
      if (isKR) return fmt.price(v);
      const krw = usdKrw > 0 ? ` (약 ${fmt.price(v * usdKrw)})` : '';
      return '$' + v.toFixed(2) + krw;
    };
    let stopLine = '';
    if (isTrailing && trailSl > 0) {
      const distPct = curPrice > 0 ? ((trailSl / curPrice - 1) * 100).toFixed(1) : '';
      const tpTxt = fmtPx(tp) ? `최초 목표 ${fmtPx(tp)}` : '';
      const trailStartTxt = fmtPx(tpPrice) ? `TP 달성가 ${fmtPx(tpPrice)}` : '';
      const stopTxt = `현재 스탑 ${fmtPx(trailSl)} 이하 시 자동 매도 (현재가 대비 ${distPct}%)`;
      stopLine = `<span style="color:#f59e0b">트레일링 중</span> — ${[tpTxt, trailStartTxt, stopTxt, '상방 목표 고정 없음'].filter(Boolean).join(' · ')}`;
    } else if (sl > 0 || tp > 0) {
      const slPct = sl > 0 && avgPrice > 0 ? ` (${((sl/avgPrice-1)*100).toFixed(1)}%)` : '';
      const tpPct = tp > 0 && avgPrice > 0 ? ` (${((tp/avgPrice-1)*100).toFixed(1)}%)` : '';
      const slTxt = fmtPx(sl) ? `손절 ${fmtPx(sl)}${slPct}` : '';
      const tpTxt = fmtPx(tp) ? `목표 ${fmtPx(tp)}${tpPct}` : '';
      stopLine = [slTxt, tpTxt].filter(Boolean).join(' · ');
    }
    // 보유일
    const heldDays  = pos.held_days || 0;
    const entryDate = pos.entry_date ? pos.entry_date.slice(5) : '';
    // Claude 판단
    const adv       = pos.hold_advice;
    const advAction = adv ? (adv.action || '') : '';
    const advLabel  = advAction === 'SELL' ? '매도 권고' : advAction === 'TRAIL' ? 'TP 달성 → 트레일링 유지' : advAction === 'HOLD' ? '홀드 유지' : '';
    let advReasonText = '';
    if (adv && adv.votes) {
      const reasons = Object.values(adv.votes).filter(v => v.action === advAction && v.reason).map(v => v.reason);
      if (reasons.length) advReasonText = reasons[0];
    }
    const advColor = advAction === 'SELL' ? '#ef4444' : advAction === 'TRAIL' ? '#f59e0b' : '#34d399';
    const advHtml  = advLabel
      ? `<div style="font-size:10px;margin-top:5px;padding-top:5px;border-top:1px solid rgba(100,116,139,0.2);overflow-wrap:anywhere;word-break:break-word">
           <span style="color:${advColor};font-weight:600;display:block">Claude: ${advLabel}</span>
           ${advReasonText ? `<span style="color:#94a3b8;display:block;margin-top:3px;max-height:120px;overflow-y:auto;line-height:1.45;padding-right:4px;white-space:normal">→ ${advReasonText}</span>` : ''}
         </div>`
      : '';
    const cardId = 'pos-card-kr-' + pos.ticker;
    return `
    <div class="card" id="${cardId}" style="min-width:260px;flex:0 0 280px;padding:14px 16px">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:2px">
        <div>
          <span style="font-size:15px;font-weight:700">${tickerDisp}</span>
          ${nameDisp ? `<span style="font-size:11px;color:var(--text-dim);margin-left:6px">${nameDisp}</span>` : ''}
        </div>
        <div style="text-align:right">
          <div style="font-size:16px;font-weight:700;color:${pnlColor}">${fmt.pct(pnl)}</div>
          <div style="font-size:11px;color:${netColor}">${netStr}</div>
        </div>
      </div>
      <div style="font-size:11px;color:var(--text-dim);margin-bottom:8px">${[stratLabel, qty+'주', entryDate ? entryDate+(heldDays?' ('+heldDays+'일째)':'') : (heldDays?heldDays+'일째':'')].filter(Boolean).join(' · ')}</div>
      <div style="display:flex;gap:16px;margin-bottom:4px">
        <div><div style="font-size:10px;color:var(--text-dim)">매수가</div><div style="font-family:var(--mono);font-size:12px">${entry}</div></div>
        <div><div style="font-size:10px;color:var(--text-dim)">현재가</div><div style="font-family:var(--mono);font-size:12px">${cur}</div></div>
      </div>
      ${stopLine ? `<div style="font-size:10px;color:var(--text-dim);margin-top:2px">${stopLine}</div>` : ''}
      ${advHtml}
      <div style="margin-top:8px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
        <button onclick="reviewPosition('KR','${pos.ticker}','${cardId}')"
          style="font-size:10px;padding:3px 10px;border:1px solid rgba(100,116,139,0.4);border-radius:4px;background:rgba(100,116,139,0.1);color:var(--text-dim);cursor:pointer"
          id="${cardId}-btn">Claude 재판단</button>
        <button onclick="immediatelySell('KR','${pos.ticker}','${cardId}')"
          style="font-size:10px;padding:3px 10px;border:1px solid rgba(239,68,68,0.5);border-radius:4px;background:rgba(239,68,68,0.1);color:#ef4444;cursor:pointer"
          id="${cardId}-sell-btn">즉시 매도</button>
        <span id="${cardId}-status" style="font-size:10px;color:var(--text-dim)"></span>
      </div>
    </div>`;
  }).join('');
  }

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
          <div style="font-size:15px;font-weight:700;margin-bottom:6px">${order.display_ticker || order.ticker}</div>
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

async function refreshPrices(market) {
  try {
    await apiPost('/api/refresh_prices', {market});
  } catch(e) {}
}

async function loadAll() {
  await refreshPrices(MARKET);
  await Promise.all([loadSummary(), loadJudgments(), loadEquityChart(), loadMarketContext(), loadCredits()]);
  loadMonitorTickers();
  loadSignalFeed();
}

async function reviewPosition(market, ticker, cardId) {
  const btn    = document.getElementById(cardId + '-btn');
  const status = document.getElementById(cardId + '-status');
  if (btn) { btn.disabled = true; btn.textContent = '분석 중...'; }
  if (status) status.textContent = '';
  try {
    const res = await apiPost('/api/review_position', {market, ticker});
    const data = await res.json();
    if (!res.ok || data.error) {
      if (status) status.textContent = '❌ ' + (data.error || '오류');
    } else {
      const actionKo = data.action === 'SELL' ? '매도 권고' : data.action === 'TRAIL' ? '트레일링 유지' : '홀드 유지';
      const votes    = data.votes || {};
      const reasons  = Object.values(votes).filter(v => v.reason).map(v => v.reason);
      const reason   = reasons.length ? reasons[0].slice(0, 80) : '';
      if (status) {
        status.textContent = '';
        const color = data.action === 'SELL' ? '#ef4444' : data.action === 'TRAIL' ? '#f59e0b' : '#34d399';
        status.innerHTML = `<span style="color:${color};font-weight:600">${actionKo}</span>`;
      }
      // 카드 내 Claude 판단 영역 갱신 후 loadSummary로 전체 새로고침
      setTimeout(() => loadSummary(), 500);
    }
  } catch(e) {
    if (status) status.textContent = '❌ 연결 오류';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = 'Claude 재판단'; }
  }
}

async function immediatelySell(market, ticker, cardId) {
  const btn    = document.getElementById(cardId + '-sell-btn');
  const status = document.getElementById(cardId + '-status');
  if (!confirm(`${ticker} 즉시 매도를 요청합니다. Claude가 적정 매도가를 결정합니다. 계속할까요?`)) return;
  if (btn) { btn.disabled = true; btn.textContent = '가격 산정 중...'; }
  if (status) status.textContent = '';
  try {
    const res = await apiPost('/api/immediate_sell', {market, ticker});
    const data = await res.json();
    if (!res.ok || data.error) {
      if (status) status.textContent = '❌ ' + (data.error || '오류');
    } else {
      const px = data.currency === 'USD' ? '$' + Number(data.sell_price).toFixed(2) : Math.round(data.sell_price).toLocaleString() + '원';
      if (status) status.innerHTML = `<span style="color:#f59e0b;font-weight:600">매도 예약 ${px}</span>`;
      setTimeout(() => loadSummary(), 2000);
    }
  } catch(e) {
    if (status) status.textContent = '❌ 연결 오류';
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = '즉시 매도'; }
  }
}

loadAll();
setInterval(loadMonitorTickers, 15000);
setInterval(loadSignalFeed, 10000);
// 30초마다 현재가 갱신 후 포지션 카드 새로고침
setInterval(async () => { await refreshPrices(MARKET); loadSummary(); }, 30000);
</script>
"""


# 페이지 2: 기간별 성과

PAGE_HISTORY_HTML = """
<main>

<!-- 4 통계 카드 -->
<div class="grid-4" id="stat-cards">
  <div class="card green">
    <div class="card-label">청산 승률</div>
    <div class="card-value" id="h-win-rate">--</div>
    <div class="card-sub"  id="h-win-detail">-- 승 / -- 패</div>
  </div>
  <div class="card cyan">
    <div class="card-label">청산 손익 합계</div>
    <div class="card-value" id="h-total-pnl">--</div>
    <div class="card-sub"  id="h-days">-- 거래일</div>
  </div>
  <div class="card blue">
    <div class="card-label">평균 청산 손익률</div>
    <div class="card-value" id="h-avg-pnl">--</div>
    <div class="card-sub" style="color:var(--muted)">매도 완료 1건당 평균</div>
  </div>
  <div class="card yellow">
    <div class="card-label">청산 거래 수</div>
    <div class="card-value" id="h-trades">--</div>
    <div class="card-sub" style="color:var(--muted)">총 체결 건수</div>
  </div>
</div>

<!-- 2 차트 -->
<div class="grid-2">
  <div class="card blue">
    <div class="section-title">손익 곡선</div>
    <div class="chart-container"><canvas id="histEquityChart"></canvas></div>
  </div>
  <div class="card red">
    <div class="section-title">월별 청산 손익</div>
    <div class="chart-container"><canvas id="monthlyChart"></canvas></div>
  </div>
</div>

<!-- 월별 테이블 -->
<div class="card">
  <div class="section-title">월별 청산 성과 요약</div>
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>월</th><th>거래일</th><th>승</th><th>패</th>
          <th>승률</th><th>총손익%</th><th>평균손익%</th>
          <th>거래수</th><th>최고일</th><th>최저일</th>
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
    cardSubs[0].textContent = `${d.wins || 0}승 / ${d.losses || 0}패 기준`;
    cardSubs[1].textContent = `${d.days || 0} 거래일 기준`;
    cardSubs[2].textContent = '매도 완료 1건당 평균';
    cardSubs[3].textContent = '매도 완료 건수';
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
  const rows = await apiGet('/api/history/monthly', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => []);

  const historyTitles = document.querySelectorAll('/**/'.replace('/**/','main .grid-2 .section-title, main .card .section-title'));
  if (historyTitles.length >= 3) {
    historyTitles[1].textContent = '월별 청산 손익';
    historyTitles[2].textContent = '월별 청산 성과 요약';
  }

  // ?붾퀎 李⑦듃
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


# 페이지 3: 매매 원장

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

<div class="grid-4" style="margin-top:12px">
  <div class="card">
    <div class="card-label">매칭됨</div>
    <div class="card-value" id="m-matched">--</div>
    <div class="card-sub" id="m-match-rate">매칭률 --%</div>
  </div>
  <div class="card">
    <div class="card-label">전략만 존재</div>
    <div class="card-value" id="m-strategy-only">--</div>
    <div class="card-sub">브로커 체결 미확인</div>
  </div>
  <div class="card">
    <div class="card-label">브로커만 존재</div>
    <div class="card-value" id="m-broker-only">--</div>
    <div class="card-sub">전략 원장 미매칭</div>
  </div>
  <div class="card">
    <div class="card-label">매칭 불확실</div>
    <div class="card-value" id="m-ambiguous">0</div>
    <div class="card-sub">현재는 엄격 매칭만 사용</div>
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
          <th style="width:110px">종목</th>
          <th style="width:110px">전략</th>
          <th style="width:90px">매칭</th>
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
  const filterMatch    = document.getElementById('f-match') ? document.getElementById('f-match').value : '';

  let url = '/api/trades/list' + marketParam();
  if (filterTicker)   url += '&ticker='   + encodeURIComponent(filterTicker);
  if (filterStrategy) url += '&strategy=' + encodeURIComponent(filterStrategy);
  if (filterSide)     url += '&side='     + encodeURIComponent(filterSide);
  if (filterMatch)    url += '&match_status=' + encodeURIComponent(filterMatch);

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
  const matched = trades.filter(t => t.match_status === 'matched').length;
  const strategyOnly = trades.filter(t => t.match_status === 'strategy_only').length;
  const brokerOnly = trades.filter(t => t.match_status === 'broker_only').length;
  const ambiguous = trades.filter(t => t.match_status === 'ambiguous').length;
  const matchBase = matched + strategyOnly;
  const matchRate = matchBase > 0 ? ((matched / matchBase) * 100).toFixed(1) : '0.0';

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
  document.getElementById('m-matched').textContent = fmt.num(matched);
  document.getElementById('m-match-rate').textContent = `매칭률 ${matchRate}%`;
  document.getElementById('m-strategy-only').textContent = fmt.num(strategyOnly);
  document.getElementById('m-broker-only').textContent = fmt.num(brokerOnly);
  document.getElementById('m-ambiguous').textContent = fmt.num(ambiguous);
}

function dayOfWeek(dateStr) {
  const days = ['일','월','화','수','목','금','토'];
  try { return days[new Date(dateStr).getDay()]; } catch { return ''; }
}

function renderTrades(trades) {
  const tbody = document.getElementById('trades-tbody');
  if (!trades.length) {
    tbody.innerHTML = `<tr><td colspan="9" style="text-align:center;color:var(--muted);padding:24px">선택 시장(${MARKET}) 기준 거래 이력이 없습니다</td></tr>`;
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
      const sep = '-'.repeat(30);
      html += `<tr class="date-group-row"><td colspan="8">날짜 ${d} (${dow}) ${sep}</td></tr>`;
    }

    const isSell  = t.side === 'sell' || t.side === '매도';
    const sideLbl = isSell ? '매도' : '매수';
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
    if (t.price_source === 'broker_balance') metaBits.push('브로커 평균단가');
    if (t.price_source === 'order_fill') metaBits.push('체결 조회 기준');
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
    const matchStatus = String(t.match_status || '').trim();
    const matchMap = {
      matched: ['매칭됨', 'rgba(34,197,94,0.16)', 'var(--green)'],
      strategy_only: ['전략만', 'rgba(245,158,11,0.16)', 'var(--yellow)'],
      broker_only: ['브로커만', 'rgba(59,130,246,0.16)', 'var(--blue)'],
      ambiguous: ['불확실', 'rgba(239,68,68,0.16)', 'var(--red)'],
    };
    const matchInfo = matchMap[matchStatus] || ['-', 'rgba(148,163,184,0.16)', '#cbd5e1'];
    const matchTitle = [t.matched_order_no ? `주문 ${t.matched_order_no}` : '', t.matched_date || t.matched_time ? `${t.matched_date || ''} ${t.matched_time || ''}`.trim() : ''].filter(Boolean).join(' · ');

    if (t.source_kind_label) {
      sourceBadge = `<div style="display:inline-block;margin-top:4px;padding:1px 6px;border-radius:999px;background:rgba(148,163,184,0.16);color:#cbd5e1;font-size:10px">${t.source_kind_label}</div>`;
    }

    html += `
    <tr>
      <td class="${sideCls}" style="font-weight:600;white-space:nowrap">${sideLbl}<div style="font-size:10px;color:var(--muted);margin-top:4px">${timeLbl}</div></td>
      <td style="font-weight:600">${t.display_ticker || t.ticker || '-'}</td>
      <td><span style="color:var(--blue)">${t.strategy || '-'}</span></td>
      <td><span title="${matchTitle.replace(/"/g,'&quot;')}" style="display:inline-block;padding:2px 8px;border-radius:999px;background:${matchInfo[1]};color:${matchInfo[2]};font-size:11px;font-weight:700">${matchInfo[0]}</span></td>
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

if (PERIOD === 'month') {
  PERIOD = 'all';
  localStorage.setItem('period', 'all');
  document.querySelectorAll('.period-btn').forEach(b => b.classList.toggle('active', b.dataset.p === 'all'));
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
  <select class="date-input" id="f-match">
    <option value="">매칭 전체</option>
    <option value="matched">매칭됨</option>
    <option value="strategy_only">전략만</option>
    <option value="broker_only">브로커만</option>
    <option value="ambiguous">불확실</option>
  </select>
  <button class="apply-btn" onclick="loadAll()">적용</button>
"""

PAGE_BROKER_TRADES_HTML = (
    PAGE_TRADES_HTML
    .replace("/api/trades/list", "/api/trades/broker")
    .replace("선택 시장(${MARKET}) 기준 거래 이력이 없습니다", "선택 시장(${MARKET}) 기준 실거래 체결 이력이 없습니다")
    .replace("매매 원장", "실거래 원장")
    .replace("브로커 체결 미확인", "전략 원장과 연결되지 않은 체결")
    .replace("전략 원장 미매칭", "브로커에서만 확인된 체결")
    .replace("현재는 엄격 매칭만 사용", "주문번호·종목·수량 기준 매칭")
)

BROKER_TRADES_STATUS_JS = """
<script>
async function loadBrokerTradeStatus() {
  const s = await fetch('/api/trades/broker/status' + marketParam()).then(r => r.json()).catch(() => ({}));
  const label = document.getElementById('trades-count-label');
  if (label) {
    label.textContent = s.ok
      ? `브로커 조회 성공 · ${s.query_start || '-'} ~ ${s.query_end || '-'} · ${s.count || 0}건`
      : `브로커 조회 실패${s.error ? ' · ' + s.error : ''}`;
  }
  const brokerSub = document.getElementById('m-broker-only') ? document.getElementById('m-broker-only').nextElementSibling : null;
  if (brokerSub) {
    brokerSub.textContent = s.ok
      ? `브로커에서만 확인된 체결 · 최근 ${s.latest_fill || '없음'}`
      : '브로커 조회 실패';
  }
  const ambiguousSub = document.querySelector('#m-ambiguous + .card-sub');
  if (ambiguousSub && s.ok) {
    ambiguousSub.textContent = `조회 구간 ${s.query_start || '-'} ~ ${s.query_end || '-'}`;
  }
}
loadBrokerTradeStatus();
</script>
"""


# 페이지 4: 분석

TODAY_SUMMARY_OVERRIDE_JS = """
<script>
/* legacy summary override disabled */
</script>
"""

TODAY_SUMMARY_OVERRIDE_JS_V2 = """
<script>
async function loadPositionCharts(positions) {
  if (typeof Chart === 'undefined' || !Array.isArray(positions)) return;
  await Promise.all(positions.map(async pos => {
    const ticker = String(pos.ticker || '').trim();
    if (!ticker) return;
    const safeId = ticker.replace(/[^A-Za-z0-9_-]/g, '_');
    const canvasId = 'pos-chart-' + safeId;
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const d = await apiGet('/api/position/chart', 'market=' + encodeURIComponent(MARKET) + '&ticker=' + encodeURIComponent(ticker))
      .then(r => r.json()).catch(() => ({}));
    const labels = d.labels || [];
    const prices = (d.prices || []).map(Number).filter(v => Number.isFinite(v));
    const meta = document.getElementById(canvasId + '-meta');
    if (meta) meta.textContent = d.source ? `${d.source} · ${prices.length}점` : '';
    if (prices.length < 2) return;
    const key = 'position_' + MARKET + '_' + safeId;
    if (charts[key]) charts[key].destroy();
    const first = prices[0] || 0;
    const last = prices[prices.length - 1] || 0;
    const color = last >= first ? '#22c55e' : '#ef4444';
    charts[key] = new Chart(canvas.getContext('2d'), {
      type: 'line',
      data: {
        labels,
        datasets: [{
          data: prices,
          borderColor: color,
          backgroundColor: color + '22',
          borderWidth: 1.5,
          fill: true,
          tension: 0.25,
          pointRadius: 0
        }]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false }, tooltip: { mode: 'index', intersect: false } },
        scales: {
          x: { display: false },
          y: { display: false }
        }
      }
    });
  }));
}

async function loadSummary() {
  const d = await apiGet('/api/summary', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({}));
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
  if (cumulativePnl) {
    const assetBreakdown = formatAssetBreakdown(t);
    const rawAssetSource = String(t.asset_source || '');
    const assetSourceLabel = (MODE === 'live' && rawAssetSource.includes('paper'))
      ? '브로커 기준'
      : ({
      broker: '브로커 기준',
      'broker+paper_us_cash_estimated': '브로커 기준 · US 모의현금 추정',
      'broker+paper_us_cash_estimated+live_fallback': '브로커 기준 · US 모의보정',
      'broker+live_fallback': '브로커 기준 · 라이브 보정',
      internal_fallback: '엔진 추정',
    }[rawAssetSource] || (rawAssetSource || '기준 미상'));
    cumulativePnl.textContent = `실현 ${fmt.krw(t.realized_pnl_krw || 0)} · 평가 ${fmt.krw(t.unrealized_pnl_krw || 0)}${assetBreakdown ? ` · ${assetBreakdown}` : ''} · 자산 ${assetSourceLabel}`;
  }

  const sess = t.session || {};
  const riskText = t.risk_value ? ` | ${t.risk_label} ${Number(t.risk_value).toFixed(1)} ${t.risk_status || ''}` : '';
  const sessTxt = sess.label ? ` | ${sess.label} (${sess.open_time || '--:--'}~${sess.close_time || '--:--'})` : '';
  const todayMode = document.getElementById('today-mode');
  if (todayMode) {
    const orderTxt = t.mode_order_limit_krw ? ` | 최대매수 ${fmt.asset(t.mode_order_limit_krw)}` : '';
    const cashBreakdown = formatOrderableBreakdown(t);
    const cashTxt = cashBreakdown ? ` | 주문가능 ${cashBreakdown}` : '';
    const brokerTxt = t.broker_trust ? ` | 브로커 ${t.broker_trust}${t.broker_last_ok_at ? ' ' + String(t.broker_last_ok_at).slice(11, 19) : ''}` : '';
    const alertTxt = (t.state_alerts || []).length
      ? `<div style="margin-top:6px;color:#ef4444;font-size:12px">상태 파일 경고 ${(t.state_alerts || []).length}건 · 로그 탭 확인</div>`
      : '';
    const descTxt = modeDescription(t.mode) ? `<div style="margin-top:6px;color:var(--muted);font-size:12px">${modeDescription(t.mode)}</div>` : '';
    todayMode.innerHTML = `모드: <span class="mode-badge mode-${t.mode}">${koMode(t.mode)}</span> 거래 ${t.trades || 0}건${orderTxt}${cashTxt}${sessTxt}${riskText}${brokerTxt}${descTxt}${alertTxt}`;
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

  const ml = t.ml_db || {};
  const mlTitle = document.getElementById('ml-db-title');
  const mlMeta = document.getElementById('ml-db-meta');
  const mlBreakdown = document.getElementById('ml-db-breakdown');
  if (mlTitle) {
    mlTitle.textContent = ml.enabled
      ? `누적 ${ml.total || 0}건 · 오늘 ${ml.today || 0}건`
      : 'ML DB 비활성';
  }
  if (mlMeta) {
    mlMeta.textContent = ml.enabled
      ? `BUY_SIGNAL ${ml.buy_signal || 0}건 · 체결 ${ml.filled || 0}건 · 결과기록 ${ml.with_outcome || 0}건 · 마지막 ${ml.last_ts || '--'}`
      : '--';
  }
  if (mlBreakdown) {
    const recentDays = (ml.recent_days || []).map(x => `${String(x.date || '').slice(5)} ${x.count || 0}`).join(' · ');
    mlBreakdown.textContent = ml.enabled
      ? `forward 준비 ${ml.forward_ready || 0}건 (${Number(ml.forward_ready_rate || 0).toFixed(1)}%)${recentDays ? ' · 최근7일 ' + recentDays : ''}`
      : '--';
  }

  renderAdaptive(t, 'adaptive-title', 'adaptive-meta', 'adaptive-breakdown');

  // 보유/진입 상태 바
  const barPos2 = document.getElementById('bar-position-count');
  const barEnt2 = document.getElementById('bar-entry-count');
  if (barPos2) barPos2.textContent = `${t.position_count ?? (t.positions || []).length}/${t.position_limit || 0}개`;
  if (barEnt2) barEnt2.textContent = `${t.entries_today ?? 0}/${t.max_daily_entries || 0}회`;

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
  } else {
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
      const fmtEntryPx = v => {
        if (!(v > 0)) return '--';
        if (isKRW) return fmt.price(v);
        const krw = usdKrw > 0 ? ` (약 ${fmt.price(v * usdKrw)})` : '';
        return '$' + v.toFixed(2) + krw;
      };
      const entry = fmtEntryPx(Number(pos.avg_price || 0));
      const cur = fmtEntryPx(Number(pos.current_price || 0));
      // 수수료 차감 실손익
      const feeBuy2  = avg * qty * 0.00015;
      const feeSell2 = curPx * qty * (isKRW ? 0.00195 : 0.00015);
      const netPnl2  = pnlNative - feeBuy2 - feeSell2;
      const netStr2  = isKRW
        ? (netPnl2 >= 0 ? '+' : '') + Math.round(netPnl2).toLocaleString() + '원'
        : (netPnl2 >= 0 ? '+$' : '-$') + Math.abs(netPnl2).toFixed(2);
      const netColor2 = netPnl2 >= 0 ? 'var(--green)' : 'var(--red)';
      const pnlKrwText = !isKRW && usdKrw > 0 ? fmt.krw(netPnl2 * usdKrw) : '';
      const valueText2 = isKRW
        ? fmt.asset(curTotal)
        : `${fmtUsd(curTotal)}${usdKrw > 0 ? ' · ' + fmt.asset(curTotal * usdKrw) : ''}`;
      // 전략명 정제
      const stratRaw = pos.strategy || '';
      const stratLabel = !stratRaw || stratRaw === 'broker_balance' || stratRaw === 'broker_sync' ? '' : stratRaw;
      const tickerDisp2 = pos.display_ticker || pos.ticker;
      const nameDisp2   = pos.name && !tickerDisp2.includes(pos.name) ? pos.name : '';
      // 트레일링 / SL / TP
      const isTrailing = pos.trailing;
      const trailSlRaw = Number(pos.trail_sl || 0);
      const slRaw = Number(pos.sl || 0);
      const tpRaw = Number(pos.tp || 0);
      const trailSl = !isKRW && usdKrw > 0 && trailSlRaw > 0 ? trailSlRaw / usdKrw : trailSlRaw;
      const sl = !isKRW && usdKrw > 0 && slRaw > 0 ? slRaw / usdKrw : slRaw;
      const tp = !isKRW && usdKrw > 0 && tpRaw > 0 ? tpRaw / usdKrw : tpRaw;
      const tpPriceRaw = Number(pos.tp_price || 0);
      const tpPrice = !isKRW && usdKrw > 0 && tpPriceRaw > 0 ? tpPriceRaw / usdKrw : tpPriceRaw;
      const tpTriggered = !!pos.tp_triggered;
      const fmtPx = v => {
        if (!(v > 0)) return null;
        if (isKRW) return fmt.price(v);
        const krw = usdKrw > 0 ? ` (약 ${fmt.price(v * usdKrw)})` : '';
        return '$' + v.toFixed(2) + krw;
      };
      let stopLine = '';
      if (isTrailing && trailSl > 0) {
        const distPct = curPx > 0 ? ((trailSl / curPx - 1) * 100).toFixed(1) : '';
        const tpTxt = fmtPx(tp) ? `최초 목표 ${fmtPx(tp)}` : '';
        const trailStartTxt = fmtPx(tpPrice) ? `TP 달성가 ${fmtPx(tpPrice)}` : '';
        const stopTxt = `현재 스탑 ${fmtPx(trailSl)} 이하 시 자동 매도 (현재가 대비 ${distPct}%)`;
        stopLine = `<span style="color:#f59e0b">트레일링 중</span> — ${[tpTxt, trailStartTxt, stopTxt, '상방 목표 고정 없음'].filter(Boolean).join(' · ')}`;
      } else if (sl > 0 || tp > 0) {
        const slPct = sl > 0 && avg > 0 ? ` (${((sl/avg-1)*100).toFixed(1)}%)` : '';
        const tpPct = tp > 0 && avg > 0 ? ` (${((tp/avg-1)*100).toFixed(1)}%)` : '';
        const slTxt = fmtPx(sl) ? `손절 ${fmtPx(sl)}${slPct}` : '';
        const tpTxt = fmtPx(tp) ? `목표 ${fmtPx(tp)}${tpPct}` : '';
        stopLine = [slTxt, tpTxt].filter(Boolean).join(' · ');
      }
      const pathbPlan = pos.pathb_plan || {};
      const pathbBuyLow = Number(pathbPlan.buy_zone_low || 0);
      const pathbBuyHigh = Number(pathbPlan.buy_zone_high || 0);
      const pathbTarget = Number(pathbPlan.sell_target || 0);
      const pathbStop = Number(pathbPlan.stop_loss || 0);
      const pathbLine = (pathbBuyLow || pathbBuyHigh || pathbTarget || pathbStop)
        ? [
            (pathbBuyLow || pathbBuyHigh) ? `B플랜 매수존 ${fmtPx(pathbBuyLow) || '-'}~${fmtPx(pathbBuyHigh) || '-'}` : '',
            pathbTarget ? `목표 ${fmtPx(pathbTarget)}` : '',
            pathbStop ? `손절 ${fmtPx(pathbStop)}` : ''
          ].filter(Boolean).join(' · ')
        : '';
      const heldDays = pos.held_days || 0;
      const entryDate = pos.entry_date ? pos.entry_date.slice(5) : '';
      const adv = pos.hold_advice;
      const advAction = adv ? (adv.action || '') : '';
      const advLabel = advAction === 'SELL' ? '매도 권고' : advAction === 'TRAIL' ? 'TP 달성 → 트레일링 유지' : advAction === 'HOLD' ? '홀드 유지' : '';
      let advReasonText2 = '';
      if (adv && adv.votes) {
        const reasons2 = Object.values(adv.votes).filter(v => v.action === advAction && v.reason).map(v => v.reason);
        if (reasons2.length) advReasonText2 = reasons2[0];
      }
      const advColor2 = advAction === 'SELL' ? '#ef4444' : advAction === 'TRAIL' ? '#f59e0b' : '#34d399';
      const advHtml = advLabel
        ? `<div style="font-size:10px;margin-top:5px;padding-top:5px;border-top:1px solid rgba(100,116,139,0.2);overflow-wrap:anywhere;word-break:break-word">
             <span style="color:${advColor2};font-weight:600;display:block">Claude: ${advLabel}</span>
             ${advReasonText2 ? `<span style="color:#94a3b8;display:block;margin-top:3px;max-height:120px;overflow-y:auto;line-height:1.45;padding-right:4px;white-space:normal">→ ${advReasonText2}</span>` : ''}
           </div>`
        : '';
      const safeTickerId2 = String(pos.ticker || '').replace(/[^A-Za-z0-9_-]/g, '_');
      const cardId2 = 'pos-card-' + safeTickerId2;
      const chartId2 = 'pos-chart-' + safeTickerId2;
      return `
        <div class="card" id="${cardId2}" style="min-width:260px;flex:0 0 280px;padding:14px 16px">
          <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:2px">
            <div>
              <span style="font-size:15px;font-weight:700">${tickerDisp2}</span>
              ${nameDisp2 ? `<span style="font-size:11px;color:var(--text-dim);margin-left:6px">${nameDisp2}</span>` : ''}
            </div>
            <div style="text-align:right">
              <div style="font-size:16px;font-weight:700;color:${pnlColor}">${fmt.pct(pnl)}</div>
              <div style="font-size:11px;color:${netColor2}">${netStr2}${pnlKrwText ? ' · '+pnlKrwText : ''}</div>
            </div>
          </div>
          <div style="font-size:11px;color:var(--text-dim);margin-bottom:8px">${[stratLabel, qty+'주', entryDate ? entryDate+(heldDays?' ('+heldDays+'일째)':'') : (heldDays?heldDays+'일째':'')].filter(Boolean).join(' · ')}</div>
          <div style="display:flex;gap:16px;margin-bottom:4px">
            <div><div style="font-size:10px;color:var(--text-dim)">매수가</div><div style="font-family:var(--mono);font-size:12px">${entry}</div></div>
            <div><div style="font-size:10px;color:var(--text-dim)">현재가</div><div style="font-family:var(--mono);font-size:12px">${cur}</div></div>
          </div>
          <div style="display:flex;justify-content:space-between;gap:8px;font-size:10px;color:var(--text-dim);margin-bottom:5px">
            <span>평가 ${valueText2}</span>
            <span id="${chartId2}-meta"></span>
          </div>
          <div style="height:58px;margin:5px 0 7px 0"><canvas id="${chartId2}"></canvas></div>
          ${stopLine ? `<div style="font-size:10px;color:var(--text-dim);margin-top:2px">${stopLine}</div>` : ''}
          ${pathbLine ? `<div style="font-size:10px;color:#93c5fd;margin-top:2px">${pathbLine}</div>` : ''}
          ${advHtml}
          <div style="margin-top:8px;display:flex;align-items:center;gap:6px;flex-wrap:wrap">
            <button onclick="reviewPosition(MARKET,'${pos.ticker}','${cardId2}')"
              style="font-size:10px;padding:3px 10px;border:1px solid rgba(100,116,139,0.4);border-radius:4px;background:rgba(100,116,139,0.1);color:var(--text-dim);cursor:pointer"
              id="${cardId2}-btn">Claude 재판단</button>
            <button onclick="immediatelySell(MARKET,'${pos.ticker}','${cardId2}')"
              style="font-size:10px;padding:3px 10px;border:1px solid rgba(239,68,68,0.5);border-radius:4px;background:rgba(239,68,68,0.1);color:#ef4444;cursor:pointer"
              id="${cardId2}-sell-btn">즉시 매도</button>
            <span id="${cardId2}-status" style="font-size:10px;color:var(--text-dim)"></span>
          </div>
        </div>`;
    }).join('');
    loadPositionCharts(positions);
  }

}

if (typeof loadSummary === 'function') {
  loadSummary();
}

loadSummary();
</script>
"""

TODAY_SIGNAL_DIGEST_JS = """
<script>
(function() {
  if (typeof loadSummary !== 'function') return;
  const _origLoadSummary = loadSummary;
  loadSummary = async function() {
    await _origLoadSummary();
    const d = await apiGet('/api/summary', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({}));
    if (!d.today) return;
    const t = d.today || {};
    const sig = t.signal_digest || {};
    const funnelTxt = (sig.candidates || sig.selected || sig.buy_signal || sig.no_signal || sig.blocked || sig.failed)
      ? `후보 ${sig.candidates || 0} → 선택 ${sig.selected || 0} · 매수신호 ${sig.buy_signal || 0} · 무신호 ${sig.no_signal || 0} · 차단 ${sig.blocked || 0} · 실패 ${sig.failed || 0}`
      : '';
    const noneTopTxt = (sig.top_none || []).length ? `무신호 상위 원인: ${(sig.top_none || []).join(', ')}` : '';
    const todayMode = document.getElementById('today-mode');
    if (!todayMode) return;
    const extraId = 'today-signal-digest-extra';
    const prev = document.getElementById(extraId);
    if (prev) prev.remove();
    if (!funnelTxt && !noneTopTxt) return;
    const box = document.createElement('div');
    box.id = extraId;
    box.style.marginTop = '6px';
    box.innerHTML =
      (funnelTxt ? `<div style="font-size:11px;color:var(--text-dim)">${funnelTxt}</div>` : '')
      + (noneTopTxt ? `<div style="font-size:11px;color:#94a3b8;margin-top:4px">${noneTopTxt}</div>` : '');
    todayMode.appendChild(box);
  };
})();
</script>
"""

CLAUDE_CONTROL_JS = """
<script>
async function loadClaudeControl() {
  const d = await apiGet('/api/claude/status', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({}));
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
  stateLine.textContent = enabled ? `활성 상태${sess.label ? ' | ' + sess.label : ''}` : '비활성 상태';
  metaLine.textContent = [
    d.last_trigger_at ? `마지막 트리거 ${String(d.last_trigger_at).slice(11, 19)}` : '',
    d.last_result_at ? `마지막 결과 ${status}` : '',
    pending ? `대기 ${pending.market} ${String(pending.requested_at || '').slice(11, 19)}` : '',
    stats.count ? `오늘 적중 ${stats.win_rate}% (${stats.wins}/${stats.count})` : ''
  ].filter(Boolean).join(' | ') || '--';
  const statsLine = stats.count
    ? `오늘 판단 ${stats.count}건 · 모드변경 ${stats.changed}건 · 유지 ${stats.unchanged}건 · HIT ${stats.wins} / MISS ${stats.losses} / FLAT ${stats.flats} · 누적 ${fmt.krw(stats.pnl_krw || 0)}`
    : '';
  errLine.textContent = [statsLine, d.last_error || ''].filter(Boolean).join(' | ');

  toggleBtn.textContent = enabled ? 'OFF로 전환' : 'ON으로 전환';
  toggleBtn.style.background = enabled ? '#7f1d1d' : '#065f46';
  triggerBtn.disabled = !enabled || !sess.active;
  triggerBtn.style.opacity = triggerBtn.disabled ? '0.5' : '1';
}

async function loadProcessControl() {
  const d = await apiGet('/api/control/status').then(r => r.json()).catch(() => ({}));
  const stateLine = document.getElementById('control-state-line');
  const metaLine = document.getElementById('control-meta-line');
  const errLine = document.getElementById('control-error-line');
  const botBtn = document.getElementById('restart-bot-btn');
  if (!stateLine || !metaLine || !errLine || !botBtn) return;

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
  if (errLine) errLine.textContent = '대시보드 서버 재시작 요청 중..';
  apiPost('/api/control/restart-dashboard').catch(() => ({}));
}

async function restartTradingBot() {
  const errLine = document.getElementById('control-error-line');
  const res = await apiPost('/api/control/restart-bot')
    .then(async r => ({ ok: r.ok, data: await r.json() }))
    .catch(() => ({ ok: false, data: {} }));
  if (errLine) errLine.textContent = res.data.message || (res.ok ? '봇 재시작 요청 완료' : '봇 재시작 요청 실패');
  await loadProcessControl();
}

async function toggleClaudeReinvoke() {
  const current = await apiGet('/api/claude/status', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({}));
  const nextEnabled = !current.enabled;
  const res = await apiPost('/api/claude/toggle', { enabled: nextEnabled }).then(r => r.json()).catch(() => ({ ok: false }));
  if (!res.ok) return;
  await loadClaudeControl();
}

async function triggerClaudeReinvoke() {
  const res = await apiPost('/api/claude/trigger', { market: MARKET }).then(async r => ({ ok: r.ok, data: await r.json() })).catch(() => ({ ok: false, data: {} }));
  if (!res.ok) {
    const errLine = document.getElementById('claude-error-line');
    if (errLine) errLine.textContent = res.data.error || 'Claude 판단 요청 실패';
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
    apiGet('/api/judgments', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({})),
    apiGet('/api/tickers/today', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({})),
    apiGet('/api/signals/recent', 'market=' + encodeURIComponent(MARKET) + '&n=8').then(r => r.json()).catch(() => ([])),
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
    waiting: '대기중',
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
    session_close: '세션 종료 청산',
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
      `${t.display_ticker || t.ticker} 선택`,
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
      `${koReason(s.reason || '-') }<br>가격 ${priceTxt}`,
      s.timestamp ? s.timestamp.substring(11, 19) : ''
    ));
  });

  (trades || []).slice(0, 6).forEach(t => {
    const price = Number(t.display_price || t.price || 0);
    const currency = (t.currency || (MARKET === 'KR' ? 'KRW' : 'USD'));
    const isKRW = currency === 'KRW';
    const priceTxt = price > 0 ? (isKRW ? Math.round(price).toLocaleString() + '원' : '$' + price.toFixed(2)) : '-';
    const buyPrice = Number(t.buy_price_native || 0);
    const buyTotal = Number(t.buy_total_native || 0);
    const sellTotal = Number(t.sell_total_native || 0);
    const pnlKrw = Number(t.pnl_krw || 0);
    let detail = `${koReason(t.reason || '-') }<br>가격 ${priceTxt}`;
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
      `${t.display_ticker || t.ticker || '-'} ${t.side || '-'} ${t.qty || 0}주`,
      detail,
      t.time || t.fill_time || t.date || ''
    ));
  });

  if (!rows.length) {
    box.innerHTML = '<div style="color:var(--text-dim);font-size:13px">실시간 Claude 판단/실행 이력이 아직 없습니다</div>';
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

loadClaudeControl();
loadProcessControl();
loadClaudeNarrative();
</script>
"""

PAGE_ANALYTICS_HTML = """
<main>

<!-- Row 1: 분석가 영향률 추이 + 모드별 성과 -->
<div class="grid-2">
  <div class="card purple">
    <div class="section-title">분석가 영향률 추이 (7일 이동평균)</div>
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

<!-- Row 3: Brain 상태 + 분석가 성과 상세 -->
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
          <th>손익률</th>
          <th>시장%</th>
          <th>판정</th>
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
  const d = await apiGet('/api/chart/analyst', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({}));
  if (!d.labels) return;

  if (charts.analyst) charts.analyst.destroy();
  charts.analyst = new Chart(document.getElementById('analystChart').getContext('2d'), {
    type: 'line',
    data: {
      labels: d.labels,
      datasets: [
        { label: '상승', data: d.bull, borderColor: '#22c55e', backgroundColor: 'rgba(34,197,94,0.14)', borderWidth: 3, pointRadius: 3, pointHoverRadius: 5, pointBackgroundColor: '#22c55e', tension: 0.35, fill: false },
        { label: '하락', data: d.bear, borderColor: '#ef4444', backgroundColor: 'rgba(239,68,68,0.14)', borderWidth: 3, pointRadius: 3, pointHoverRadius: 5, pointBackgroundColor: '#ef4444', tension: 0.35, fill: false },
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
  const d = await apiGet('/api/patterns', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({}));

  // 모드별 테이블
  const modes = d.modes || {};
  document.getElementById('modes-tbody').innerHTML = Object.entries(modes)
    .sort((a, b) => b[1].count - a[1].count)
    .map(([mode, v]) => {
      const wc = v.win_rate >= 55 ? 'var(--green)' : v.win_rate >= 45 ? 'var(--yellow)' : 'var(--red)';
      const bw = Math.min(100, Math.round(v.win_rate));
      const desc = modeDescription(mode);
      return `<tr>
        <td>
          <span class="mode-badge mode-${mode}">${koMode(mode)}</span>
          ${desc ? `<div style="margin-top:4px;color:var(--muted);font-size:12px;line-height:1.4">${desc}</div>` : ''}
        </td>
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
  const d = await apiGet('/api/brain', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({}));
  const a = d.analyst || {};

  // Brain 상태 카드
  document.getElementById('brain-status').innerHTML = `
    <div style="font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:12px">
      버전 ${d.version || '-'} &nbsp;|&nbsp; ${d.trained_days || 0}일 학습 &nbsp;|&nbsp; 체제: <span style="color:var(--text)">${koRegime(d.regime)}</span>
    </div>
    <div style="font-family:var(--mono);font-size:11px;color:var(--muted);margin-bottom:4px">마지막 업데이트</div>
    <div style="font-family:var(--mono);font-size:12px;margin-bottom:16px">${d.updated || '-'}</div>
    <div style="font-size:11px;font-weight:600;letter-spacing:1.5px;color:var(--muted);margin-bottom:10px">분석가 영향률</div>
    ${['bull','bear','neutral'].map(k => {
      const perf = a[k] || {};
      const rate = Math.round((perf.rate || 0) * 100);
      const col  = k === 'bull' ? 'var(--green)' : k === 'bear' ? 'var(--red)' : 'var(--yellow)';
      const lbl  = k === 'bull' ? '상승' : k === 'bear' ? '하락' : '중립';
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
        const lbl = k === 'bull' ? '상승 분석가' : k === 'bear' ? '하락 분석가' : '중립 분석가';
        const rate = Math.round((v.rate || 0) * 100);
        return `
        <div style="background:rgba(255,255,255,0.03);border-radius:8px;padding:16px;border:1px solid var(--border)">
          <div style="color:${col};font-weight:600;margin-bottom:10px">${lbl}</div>
          <div style="font-family:var(--mono);font-size:12px;line-height:2;color:var(--muted)">
            <div>영향률 <span style="color:${col}">${rate}%</span></div>
            <div>총 판단 <span style="color:var(--text)">${v.total || 0}건</span></div>
            <div>HIT <span class="up">${v.hit || v.hits || 0}</span> / MISS <span class="down">${v.miss || v.misses || 0}</span> / PARTIAL <span class="neutral-color">${v.partial || v.partials || Math.max((v.total || 0) - (v.hit || v.hits || 0) - (v.miss || v.misses || 0), 0)}</span></div>
            ${v.recent_streak !== undefined ? `<div>최근 연속 <span style="color:var(--text)">${v.recent_streak}</span></div>` : ''}
            ${v.avg_confidence !== undefined ? `<div>평균 신뢰도 <span style="color:var(--text)">${Math.round((v.avg_confidence||0)*100)}%</span></div>` : ''}
          </div>
        </div>`;
      }).join('')}
    </div>
  `;

  // 전략별 성과
  const strategy = d.strategy || {};
  const strategyDesc = {
    '변동성돌파': '당일 고점·기준선 돌파 추세 추종',
    'OR눌림': '장초 OR 형성 후 눌림 재진입',
    '갭눌림': '갭 상승 후 눌림 뒤 재상승 진입',
    '모멘텀': '추세·강도 지속형 진입',
    '평균회귀': '과매도 반등 노림',
    '브로커동기화': '전략 진입이 아니라 브로커 잔고 복구'
  };
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
  ['변동성돌파', 'OR눌림', '갭눌림', '모멘텀', '평균회귀', '브로커동기화'].forEach(name => {
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
        const desc = strategyDesc[name] || '';
        return `<tr>
          <td style="color:var(--cyan)">
            <div>${name}</div>
            ${desc ? `<div style="font-size:10px;color:var(--muted);line-height:1.35;margin-top:2px">${desc}</div>` : ''}
          </td>
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
  const d = await apiGet('/api/brain/history', 'market=' + encodeURIComponent(MARKET)).then(r => r.json()).catch(() => ({}));
  const days = d.recent_days || [];

  // 메타 헤더
  const regime = koRegime(d.market_regime || 'unknown');
  document.getElementById('brain-history-meta').textContent =
    `${d.trained_days || 0}일 학습 | 체제: ${regime}`;

  // 결과 배지
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
      const lesson  = (day.key_lesson || '').slice(0, 80) || '-';
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
        <td style="max-width:260px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--cyan)"
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

PAGE_PATHB_HTML = """
<main>
  <section class="section">
    <div class="section-title">B플랜 실시간</div>
    <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));">
      <div class="card"><div class="card-sub">상태</div><div class="metric" id="pathb-enabled">-</div></div>
      <div class="card"><div class="card-sub">모드</div><div class="metric" id="pathb-mode">-</div></div>
      <div class="card"><div class="card-sub">오늘 B 플랜</div><div class="metric" id="pathb-runs">-</div></div>
      <div class="card"><div class="card-sub">대기</div><div class="metric" id="pathb-waiting">-</div></div>
      <div class="card"><div class="card-sub">보유</div><div class="metric" id="pathb-filled">-</div></div>
      <div class="card"><div class="card-sub">주문상태 불명</div><div class="metric danger" id="pathb-unknown">-</div></div>
    </div>
    <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); margin-top:12px;">
      <div class="card green"><div class="card-sub">진입률</div><div class="metric" id="pathb-entry-rate">-</div></div>
      <div class="card blue"><div class="card-sub">목표가 적중률</div><div class="metric" id="pathb-target-rate">-</div></div>
      <div class="card yellow"><div class="card-sub">평균 수익률</div><div class="metric" id="pathb-avg-pnl">-</div></div>
      <div class="card purple"><div class="card-sub">추정 실현손익</div><div class="metric" id="pathb-realized">-</div></div>
    </div>
    <div class="card" style="margin-top:12px;">
      <div class="card-title">운용 제한</div>
      <div id="pathb-limits" class="muted"></div>
    </div>
    <div class="card" style="margin-top:12px;">
      <div class="card-title">B플랜 판단 흐름</div>
      <div id="pathb-flow" class="muted"></div>
    </div>
    <div class="card" style="margin-top:12px;">
      <div class="card-title">A플랜 진입 타이밍</div>
      <div id="entry-timing-summary" class="muted"></div>
      <div id="entry-timing-recent" class="table-wrap" style="margin-top:10px;"></div>
    </div>
    <div class="card" style="margin-top:12px;">
      <div class="card-title">후보 바구니 모니터</div>
      <div id="bucket-summary" class="table-wrap"><div class="muted">바구니 데이터 로딩 중...</div></div>
      <div id="bucket-candidates" class="table-wrap" style="margin-top:10px;"></div>
    </div>
    <div class="card" style="margin-top:12px;">
      <div class="card-title">계좌 조회 기준 상태</div>
      <div id="pathb-broker-truth" class="muted"></div>
    </div>
    <div class="card" style="margin-top:12px;">
      <div class="card-title">A/B 실현 수익률 비교</div>
      <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));">
        <div class="card"><div class="card-sub">A플랜 평균 수익률</div><div class="metric" id="patha-avg-pnl">-</div></div>
        <div class="card"><div class="card-sub">B플랜 평균 수익률</div><div class="metric" id="pathb-compare-avg-pnl">-</div></div>
        <div class="card"><div class="card-sub">B-A 차이</div><div class="metric" id="pathb-delta-pnl">-</div></div>
        <div class="card"><div class="card-sub">B-A 실현손익 차이</div><div class="metric" id="pathb-delta-realized">-</div></div>
      </div>
      <div class="grid" style="grid-template-columns: 1fr 1.2fr; margin-top:12px;">
        <div id="pathb-comparison-table" class="table-wrap"></div>
        <div class="chart-container"><canvas id="pathbCompareChart"></canvas></div>
      </div>
    </div>
    <div class="card" style="margin-top:12px;">
      <div class="card-title">B플랜 관찰 / 미동작 사유</div>
      <div id="pathb-watch" class="table-wrap"></div>
    </div>
    <div class="card" style="margin-top:12px;">
      <div class="card-title">현재 보유 경로</div>
      <div id="pathb-position-paths" class="table-wrap"></div>
    </div>
    <div class="grid" style="grid-template-columns: 1.2fr 1fr 1fr; margin-top:12px;">
      <div class="card">
        <div class="card-title">누적 수익률</div>
        <div class="chart-container"><canvas id="pathbPnlChart"></canvas></div>
      </div>
      <div class="card">
        <div class="card-title">적중률</div>
        <div class="chart-container"><canvas id="pathbOutcomeChart"></canvas></div>
      </div>
      <div class="card">
        <div class="card-title">상태 분포</div>
        <div class="chart-container"><canvas id="pathbStatusChart"></canvas></div>
      </div>
    </div>
    <div class="card" style="margin-top:12px;">
      <div class="card-title">진행 중인 클로드 가격 플랜</div>
      <div id="pathb-active" class="table-wrap"></div>
    </div>
    <div class="card" style="margin-top:12px;">
      <div class="card-title">클로드 매수/매도 근거</div>
      <div id="pathb-recent" class="table-wrap"></div>
    </div>
    <div class="card" style="margin-top:12px;">
      <div class="card-title">주문상태 불명</div>
      <div id="pathb-unknown-table" class="table-wrap"></div>
    </div>
    <div class="card" style="margin-top:12px;">
      <div class="card-title">상태별 건수</div>
      <div id="pathb-status" class="muted"></div>
    </div>
  </section>
</main>
<script>
async function loadPathB() {
  const market = (window.MARKET || localStorage.getItem('market') || 'KR');
  const mode = localStorage.getItem('runtime_mode') || 'live';
  MODE = mode;
  const modeBtn = document.getElementById('btn-' + mode);
  if (modeBtn) {
    document.querySelectorAll('.mkt-btn[data-group="mode"]').forEach(b => b.classList.remove('active'));
    modeBtn.classList.add('active');
  }
  const d = await fetch('/api/v2/ops?market=' + encodeURIComponent(market) + '&mode=' + encodeURIComponent(mode)).then(r => r.json()).catch(() => ({}));
  const b = d.path_b_live || {};
  const cfg = b.config || {};
  const ctl = b.control || {};
  const m = b.metrics || {};
  const enabled = !!cfg.enabled && !!ctl.enabled && !cfg.emergency_disable && !ctl.emergency_disabled;
  document.getElementById('pathb-enabled').textContent = enabled ? '켜짐' : '꺼짐';
  document.getElementById('pathb-mode').textContent = koPathBMode(cfg.mode || '-');
  document.getElementById('pathb-runs').textContent = b.runs ?? 0;
  document.getElementById('pathb-waiting').textContent = b.waiting ?? 0;
  document.getElementById('pathb-filled').textContent = b.filled ?? 0;
  document.getElementById('pathb-unknown').textContent = (b.order_unknown || []).length;
  document.getElementById('pathb-entry-rate').textContent = fmtPct(m.entry_rate_pct);
  document.getElementById('pathb-target-rate').textContent = fmtPct(m.target_hit_rate_pct);
  document.getElementById('pathb-avg-pnl').textContent = fmtSignedPct(m.avg_pnl_pct);
  document.getElementById('pathb-realized').textContent = fmtMoney(m.realized_pnl_value);
  const src = cfg.source || {};
  const conflicts = src.conflicts || {};
  const conflictText = Object.keys(conflicts).length
    ? ` | 설정 충돌 ${Object.entries(conflicts).map(([k, v]) => `${koConfigKey(k)} ${v.runtime_env}→${v.start_config}`).join(', ')}`
    : '';
  document.getElementById('pathb-limits').textContent =
    `1회 예산 ${fmtMoney(cfg.fixed_order_krw || 0)}원 | 투입금액 ${fmtMoney(m.deployed_value || 0)}원 | 동시보유 ${cfg.max_positions || 0}개 | 하루 진입 ${cfg.max_daily_entries || 0}회 | 최소 신뢰도 ${cfg.min_confidence || 0} | 당일청산 ${cfg.intraday_only ? '사용' : '미사용'} | 설정 ${src.start_config_applied ? '시작설정 적용' : 'env 기준'} | 제어 ${koControlBy(ctl.updated_by || 'default')} ${ctl.reason || ''}${conflictText}`;
  const sel = b.selection || {};
  const sc = sel.counts || {};
  const noPlan = (sel.no_plan_reasons || []).map(koPathBSelectionReason).filter(Boolean);
  const missing = (sel.missing_price_targets || []).join(', ');
  document.getElementById('pathb-flow').innerHTML =
    `Claude 입력 후보 ${sc.universe || 0}개 | Claude 관찰 ${sc.watchlist || 0}개 | 원래 매수 후보 ${sc.raw_trade_ready || 0}개 | 실제 적용 매수 후보 ${sc.applied_trade_ready || 0}개 | 가격목표 ${sc.price_targets || 0}개 | 생성된 B플랜 ${sc.registered_plans || 0}개`
    + `<br>상태: ${(noPlan.length ? noPlan.join(' / ') : '정상')}`
    + (missing ? `<br>가격목표 누락: ${missing}` : '')
    + (sel.fallback_mode ? `<br>Claude 응답 복구 모드: ${sel.fallback_mode}` : '');
  renderEntryTiming(d.entry_timing || {});
  renderBucketMonitor(d.bucket_monitor || {});
  const brokerTruth = d.broker_truth || {};
  const brokerMarkets = brokerTruth.markets || {};
  document.getElementById('pathb-broker-truth').innerHTML = ['KR', 'US'].map(mkt => {
    const bt = brokerMarkets[mkt] || {};
    const err = bt.error ? ` | 오류 ${bt.error}` : '';
    return `${mkt}: ${bt.missing ? '계좌 조회 대기' : (bt.stale ? '계좌 조회 오래됨' : '계좌 조회 정상')} | 보유 ${(bt.positions || []).length}개 | 미체결 ${(bt.open_orders || []).length}개 | 당일체결 ${(bt.today_fills || []).length}개 | 마지막 ${bt.last_success_at || '-'}${err}`;
  }).join('<br>');
  renderPathComparison(b.path_comparison || {});
  const watchRows = (sel.watch_rows || []).map(r => `
    <tr>
      <td>${r.display_ticker || r.ticker || ''}</td>
      <td>${koPathBWatchCategory(r.category || '')}</td>
      <td>${koPathBWatchState(r.state || '')}</td>
      <td>${r.buy_zone_low || ''}${r.buy_zone_high ? ' ~ ' + r.buy_zone_high : ''}</td>
      <td>${r.sell_target || ''}</td>
      <td>${r.stop_loss || ''}</td>
      <td>${r.confidence || ''}</td>
      <td>${koPathBReason(r.filter_reason || '')}</td>
      <td>${r.reason || ''}</td>
      <td>${r.entry_rationale || ''}</td>
      <td>${r.exit_rationale || ''}</td>
    </tr>
  `).join('');
  document.getElementById('pathb-watch').innerHTML = watchRows
    ? `<table><thead><tr><th>종목</th><th>구분</th><th>B플랜 상태</th><th>매수 존</th><th>목표</th><th>손절</th><th>신뢰도</th><th>차단 사유</th><th>Claude 선택 이유</th><th>진입 근거</th><th>청산 근거</th></tr></thead><tbody>${watchRows}</tbody></table>`
    : '<div class="muted">오늘 B플랜 관찰 대상이 없습니다.</div>';
  const posRows = (d.positions || []).map(p => `
    <tr>
      <td>${p.display_ticker || p.ticker || ''}</td>
      <td>${koBuyPath(p.buy_path || '')}</td>
      <td>${p.qty || ''}</td>
      <td>${p.entry || ''}</td>
      <td>${p.current_price || ''}</td>
      <td>${p.strategy || ''}</td>
      <td>${p.target || ''}</td>
      <td>${p.stop_loss || ''}</td>
      <td>${p.source === 'broker_truth' ? '계좌 조회' : '로컬 fallback'}</td>
    </tr>
  `).join('');
  document.getElementById('pathb-position-paths').innerHTML = posRows
    ? `<table><thead><tr><th>종목</th><th>매수 경로</th><th>수량</th><th>진입가</th><th>현재가</th><th>전략</th><th>B 목표</th><th>B 손절</th><th>상태 기준</th></tr></thead><tbody>${posRows}</tbody></table>`
    : '<div class="muted">현재 보유 포지션이 없습니다.</div>';
  drawPathBCharts(b.charts || {});
  const rows = (b.active || []).map(r => `
    <tr>
      <td>${r.market || ''}</td>
      <td>${r.display_ticker || r.ticker || ''}</td>
      <td>${koBuyPath(r.buy_path || 'path_b')}</td>
      <td>${koPathBStatus(r.status || '')}</td>
      <td>${r.buy_zone_low || ''} ~ ${r.buy_zone_high || ''}</td>
      <td>${r.sell_target || ''}</td>
      <td>${r.stop_loss || ''}</td>
      <td>${r.confidence || ''}</td>
      <td>${r.filled_qty || ''}</td>
      <td>${fmtSignedPct(r.pnl_pct)}</td>
      <td>${r.entry_rationale || ''}</td>
      <td>${r.exit_rationale || ''}</td>
    </tr>
  `).join('');
  document.getElementById('pathb-active').innerHTML = rows
    ? `<table><thead><tr><th>시장</th><th>종목</th><th>매수 경로</th><th>상태</th><th>매수 존</th><th>목표</th><th>손절</th><th>신뢰도</th><th>수량</th><th>수익률</th><th>진입 근거</th><th>청산 근거</th></tr></thead><tbody>${rows}</tbody></table>`
    : '<div class="muted">활성 B플랜이 없습니다.</div>';
  const recentRows = (b.recent || []).map(r => `
    <tr>
      <td>${r.display_ticker || r.ticker || ''}</td>
      <td>${koBuyPath(r.buy_path || 'path_b')}</td>
      <td>${koPathBStatus(r.status || '')}</td>
      <td>${r.buy_zone_low || ''} ~ ${r.buy_zone_high || ''}</td>
      <td>${r.actual_entry_price || r.entry_order_price || ''}</td>
      <td>${r.actual_exit_price || ''}</td>
      <td>${fmtSignedPct(r.pnl_pct)}</td>
      <td>${koPathBReason(r.close_reason || r.cancel_reason || '')}</td>
      <td>${r.rationale || r.entry_rationale || ''}</td>
      <td>${listText(r.entry_basis_tags)}</td>
      <td>${listText(r.invalidation_conditions)}</td>
    </tr>
  `).join('');
  document.getElementById('pathb-recent').innerHTML = recentRows
    ? `<table><thead><tr><th>종목</th><th>매수 경로</th><th>상태</th><th>매수 존</th><th>진입</th><th>청산</th><th>수익률</th><th>결과</th><th>Claude 근거</th><th>진입 태그</th><th>무효 조건</th></tr></thead><tbody>${recentRows}</tbody></table>`
    : '<div class="muted">오늘 클로드 가격 플랜 기록이 없습니다.</div>';
  const unknownRows = (b.order_unknown || []).map(r => `
    <tr><td>${r.market || ''}</td><td>${r.display_ticker || r.ticker || ''}</td><td>${koPathBStatus(r.status || '')}</td><td>${koOrderUnknownResolution(r.order_unknown_resolution || '')}</td><td>${r.broker_position_evidence ? '있음' : '없음'}</td><td>${r.broker_today_fill_evidence ? '있음' : '없음'}</td><td>${r.path_a_origin_possible ? '가능' : '-'}</td><td>${r.broker_truth_last_success_at || ''}</td><td>${r.path_run_id || ''}</td></tr>
  `).join('');
  document.getElementById('pathb-unknown-table').innerHTML = unknownRows
    ? `<table><thead><tr><th>시장</th><th>종목</th><th>상태</th><th>재판정</th><th>계좌보유</th><th>당일체결</th><th>A기인 가능</th><th>계좌조회</th><th>플랜ID</th></tr></thead><tbody>${unknownRows}</tbody></table>`
    : '<div class="muted">주문상태 불명 없음</div>';
  const counts = b.status_counts || {};
  document.getElementById('pathb-status').textContent = Object.keys(counts).length
    ? Object.entries(counts).map(([k, v]) => `${koPathBStatus(k)}: ${v}`).join(' | ')
    : '상태 카운트 없음';
}

function fmtPct(v) {
  const n = Number(v || 0);
  return n.toFixed(1) + '%';
}

function fmtSignedPct(v) {
  if (v === '' || v === null || typeof v === 'undefined') return '-';
  const n = Number(v || 0);
  return (n > 0 ? '+' : '') + n.toFixed(2) + '%';
}

function fmtMoney(v) {
  const n = Number(v || 0);
  return n.toLocaleString('ko-KR', { maximumFractionDigits: 0 });
}

function listText(v) {
  if (Array.isArray(v)) return v.join(', ');
  return v || '';
}

function koPathBMode(v) {
  const m = {
    min_size_live: '최소금액 실매매',
    disabled: '중지',
    off: '중지'
  };
  return m[v] || v || '-';
}

function koConfigKey(v) {
  const m = {
    PATHB_MAX_POSITIONS: '동시보유',
    PATHB_MAX_DAILY_ENTRIES: '하루 진입',
    PATHB_FIXED_ORDER_KRW: '1회 예산',
    PATHB_INTRADAY_ONLY: '당일청산',
    PATHB_MIN_CONFIDENCE: '최소 신뢰도',
    V2_MAX_DAILY_ENTRIES: '전체 하루 진입',
    KR_MAX_POSITIONS: 'KR 최대보유',
    US_MAX_POSITIONS: 'US 최대보유'
  };
  return m[v] || v || '';
}

function koControlBy(v) {
  const m = {
    default: '기본값',
    telegram: '텔레그램',
    operator: '운영자'
  };
  return m[v] || v || '';
}

function koPathBStatus(v) {
  const m = {
    WAITING: '지정가 대기',
    HIT: '매수가 도달',
    ORDER_SENT: '매수 주문 전송',
    ORDER_ACKED: '매수 주문 접수',
    PARTIAL_FILLED: '부분 체결',
    FILLED: '보유 중',
    SELL_SENT: '매도 주문 전송',
    SELL_ACKED: '매도 주문 접수',
    SELL_PARTIAL_FILLED: '매도 부분체결',
    CLOSED: '청산 완료',
    EXPIRED: '매수 미도달',
    CANCELLED: '취소',
    ORDER_UNKNOWN: '주문상태 불명'
  };
  return m[v] || v || '-';
}

function koOrderUnknownResolution(v) {
  const m = {
    path_a_origin_possible: 'A플랜 기인 가능',
    broker_no_evidence: '계좌 근거 없음',
    broker_truth_unavailable: '계좌 조회 실패',
    ambiguous_broker_truth: '계좌 근거 애매함',
    pathb_fill_recovered: 'B플랜 체결 복구',
    pathb_open_order_recovered: 'B플랜 미체결 주문 복구',
    session_end_unresolved: '세션 종료 미해결'
  };
  return m[v] || v || '-';
}

function koPathBReason(v) {
  const m = {
    CLOSED_CLAUDE_PRICE_TARGET: '목표가 도달 청산',
    CLOSED_CLAUDE_PRICE_STOP: '클로드 손절가 청산',
    CLOSED_HARD_STOP: '하드스탑 청산',
    CLOSED_CLAUDE_PRICE_PRE_CLOSE: '마감 전 청산',
    CLOSED_USER_MANUAL: '수동 청산',
    CLOSED_PANIC: '긴급 청산',
    cancel_if_open_above: '시가가 무효 가격 초과',
    precheck_failed: '주문 사전검사 실패',
    PATHB_MAX_DAILY_ENTRIES: 'B플랜 하루 진입 제한',
    PATHB_MAX_POSITIONS: 'B플랜 최대 보유 제한',
    PATH_DUPLICATE_HOLDING: '같은 종목 중복 보유 차단',
    PATHB_MANUALLY_DISABLED: 'B플랜 수동 중지',
    PATHB_EMERGENCY_DISABLED: 'B플랜 긴급 중지'
  };
  return m[v] || v || '';
}

function koPathBSelectionReason(v) {
  const m = {
    MARKET_NOT_SELECTED: '시장 선택 필요',
    PATHB_CONFIG_DISABLED: '설정에서 B플랜 꺼짐',
    PATHB_OPERATOR_DISABLED: '운영자가 B플랜 중지',
    PATHB_EMERGENCY_DISABLED: 'B플랜 긴급 중지',
    NO_SELECTION_FILE: '오늘 Claude 판단 파일 없음',
    NO_WATCHLIST: 'Claude 관찰 종목 없음',
    ALL_TRADE_READY_FILTERED: '매수 후보가 런타임 필터에서 모두 차단',
    PRICE_TARGETS_EMPTY: 'Claude 가격목표 전체 누락',
    MISSING_PRICE_TARGETS: '일부 매수 후보 가격목표 누락',
    NO_PATH_RUN_REGISTERED: '매수 후보는 있으나 B플랜 등록 없음',
    NO_TRADE_READY: '매수 후보 없음'
  };
  return m[v] || v || '';
}

function koPathBWatchCategory(v) {
  const m = {
    applied_trade_ready: '실제 매수 후보',
    filtered_trade_ready: '필터 차단',
    raw_trade_ready: '원래 매수 후보',
    watch_only: '관찰만'
  };
  return m[v] || v || '';
}

function koPathBWatchState(v) {
  const m = {
    WATCH_ONLY: '관찰만',
    READY_NO_PATH_RUN: '등록 대기/누락',
    MISSING_PRICE_TARGETS: '가격목표 없음',
    WAITING: '지정가 대기',
    HIT: '매수가 도달',
    ORDER_SENT: '매수 주문 전송',
    ORDER_ACKED: '매수 주문 접수',
    PARTIAL_FILLED: '부분 체결',
    FILLED: '보유 중',
    SELL_SENT: '매도 주문 전송',
    SELL_ACKED: '매도 주문 접수',
    CLOSED: '청산 완료',
    EXPIRED: '매수 미도달',
    CANCELLED: '취소',
    ORDER_UNKNOWN: '주문상태 불명',
    continuation_shadow_only: '연속 상승 전략 비활성',
    slot_cap: '전략 슬롯 초과'
  };
  if (String(v || '').startsWith('slot_cap:')) return '전략 슬롯 초과: ' + String(v).split(':')[1];
  if (String(v || '').startsWith('slot_disabled:')) return '전략 슬롯 비활성: ' + String(v).split(':')[1];
  return m[v] || koPathBReason(v) || v || '';
}

function koBuyPath(v) {
  const m = {
    path_a: 'Path A - Timing Adapter',
    path_b: 'Path B - Claude Price',
    manual_or_broker: '수동/브로커 동기화'
  };
  return m[v] || v || '';
}

function drawPathBCharts(chartsData) {
  if (typeof Chart === 'undefined') return;
  drawLineChart('pathbPnlChart', 'pathbPnl', chartsData.pnl || {}, '누적 수익률 %', '#10b981');
  drawBarChart('pathbOutcomeChart', 'pathbOutcome', chartsData.outcomes || {}, '#3b82f6');
  drawDoughnutChart('pathbStatusChart', 'pathbStatus', chartsData.status || {});
}

function renderPathComparison(cmp) {
  const a = cmp.path_a || {};
  const b = cmp.path_b || {};
  const delta = cmp.delta || {};
  document.getElementById('patha-avg-pnl').textContent = fmtSignedPct(a.avg_pnl_pct);
  document.getElementById('pathb-compare-avg-pnl').textContent = fmtSignedPct(b.avg_pnl_pct);
  document.getElementById('pathb-delta-pnl').textContent = fmtSignedPct(delta.avg_pnl_pct);
  document.getElementById('pathb-delta-realized').textContent = fmtMoney(delta.realized_pnl_value || 0);
  const rows = [
    ['A플랜', a.closed || 0, fmtPct(a.win_rate_pct), fmtSignedPct(a.avg_pnl_pct), fmtMoney(a.realized_pnl_value || 0)],
    ['B플랜', b.closed || 0, fmtPct(b.win_rate_pct), fmtSignedPct(b.avg_pnl_pct), fmtMoney(b.realized_pnl_value || 0)]
  ].map(r => `<tr><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td><td>${r[3]}</td><td>${r[4]}</td></tr>`).join('');
  document.getElementById('pathb-comparison-table').innerHTML =
    `<table><thead><tr><th>경로</th><th>청산건수</th><th>승률</th><th>평균 수익률</th><th>실현손익</th></tr></thead><tbody>${rows}</tbody></table>`
    + `<div class="muted" style="margin-top:8px;">기준: ${cmp.basis || '실현 청산 기준'}</div>`;
  if (typeof Chart !== 'undefined') {
    drawBarChart('pathbCompareChart', 'pathbCompare', cmp.chart || {}, '#0ea5e9');
  }
}

function renderEntryTiming(timing) {
  const summaryEl = document.getElementById('entry-timing-summary');
  const recentEl = document.getElementById('entry-timing-recent');
  if (!summaryEl || !recentEl) return;
  if (!timing || timing.missing) {
    summaryEl.textContent = '오늘 A플랜 진입 타이밍 로그가 아직 없습니다.';
    recentEl.innerHTML = '';
    return;
  }
  const avg = timing.averages || {};
  const events = timing.events || {};
  summaryEl.innerHTML =
    `로그 ${timing.row_count || 0}행 | 후보 ${events.candidate_detected || 0} | 신호 ${events.signal_fired || 0} | 주문 ${events.order_sent || 0} | 체결 ${(events.filled || 0) + (events.partial_filled || 0)}`
    + `<br>후보→주문 평균 ${fmtMaybeMin(avg.candidate_to_order_delay_min)} | 신호→주문 평균 ${fmtMaybeMin(avg.signal_to_order_delay_min)} | 주문→체결 평균 ${fmtMaybeSec(avg.order_to_fill_delay_sec)}`
    + `<br>후보→주문 가격변화 ${fmtMaybePct(avg.price_change_candidate_to_order_pct)} | 고점 대비 진입 ${fmtMaybePct(avg.entry_vs_intraday_high_pct)}`;
  const rows = (timing.recent || []).map(r => `
    <tr>
      <td>${r.market || ''}</td>
      <td>${r.ticker || ''}</td>
      <td>${koEntryTimingEvent(r.event || '')}</td>
      <td>${r.strategy || ''}</td>
      <td>${r.signal_check_count || 0}</td>
      <td>${fmtMaybeMin(r.candidate_to_order_delay_min)}</td>
      <td>${fmtMaybeMin(r.signal_to_order_delay_min)}</td>
      <td>${fmtMaybeSec(r.order_to_fill_delay_sec)}</td>
      <td>${fmtMaybePct(r.price_change_candidate_to_order_pct)}</td>
      <td>${fmtMaybePct(r.entry_vs_intraday_high_pct)}</td>
      <td>${r.order_no || ''}</td>
    </tr>
  `).join('');
  recentEl.innerHTML = rows
    ? `<table><thead><tr><th>시장</th><th>종목</th><th>이벤트</th><th>전략</th><th>신호체크</th><th>후보→주문</th><th>신호→주문</th><th>주문→체결</th><th>후보→주문 가격</th><th>고점 대비</th><th>주문번호</th></tr></thead><tbody>${rows}</tbody></table>`
    : '<div class="muted">아직 신호/주문/체결 이벤트가 없습니다.</div>';
}

function fmtMaybeMin(v) {
  if (v === null || typeof v === 'undefined' || v === '') return '-';
  return Number(v).toFixed(1) + '분';
}

function fmtMaybeSec(v) {
  if (v === null || typeof v === 'undefined' || v === '') return '-';
  return Number(v).toFixed(0) + '초';
}

function koEntryTimingEvent(v) {
  const m = {
    signal_fired: '신호 발생',
    order_sent: '주문 전송',
    partial_filled: '부분 체결',
    filled: '체결'
  };
  return m[v] || v || '-';
}

function renderBucketMonitor(bucket) {
  const summaryEl = document.getElementById('bucket-summary');
  const candidateEl = document.getElementById('bucket-candidates');
  if (!summaryEl || !candidateEl) return;
  if (!bucket || bucket.missing) {
    summaryEl.innerHTML = `<div class="muted">${(bucket && bucket.message) || '후보 바구니 데이터가 아직 없습니다.'}</div>`;
    candidateEl.innerHTML = '';
    return;
  }
  const warnings = (bucket.warnings || []).map(koBucketWarning).filter(Boolean).join(' / ');
  const bucketRows = (bucket.buckets || []).map(r => `
    <tr>
      <td>${koBucket(r.primary_bucket || '')}</td>
      <td>${r.candidates || 0}</td>
      <td>${r.claude_input || 0}</td>
      <td>${r.watch || 0}</td>
      <td>${r.trade_ready || 0}</td>
      <td>${r.not_in_prompt || 0}</td>
      <td>${r.winner_30m || 0}</td>
      <td>${r.winner_60m || 0}</td>
      <td>${r.winner_close || 0}</td>
      <td>${r.missed_winner || 0}</td>
      <td>${fmtMaybePct(r.avg_forward_30m)}</td>
      <td>${fmtMaybePct(r.avg_forward_60m)}</td>
      <td>${fmtMaybePct(r.avg_forward_close)}</td>
    </tr>
  `).join('');
  summaryEl.innerHTML = bucketRows
    ? `<div class="muted" style="margin-bottom:8px;">세션 ${bucket.session_date || ''} | 후보 ${bucket.unique_candidate_count || 0}개 | 표시 ${bucket.candidate_display_limit || 30}개 | 로그 ${bucket.row_count || 0}행${warnings ? ' | 경고 ' + warnings : ''}</div>`
      + `<table><thead><tr><th>바구니</th><th>후보</th><th>Claude 입력</th><th>관찰</th><th>매수 후보</th><th>미입력</th><th>30분 승</th><th>60분 승</th><th>종가 승</th><th>놓친 승</th><th>30분 수익률</th><th>60분 수익률</th><th>종가 수익률</th></tr></thead><tbody>${bucketRows}</tbody></table>`
    : '<div class="muted">집계 가능한 바구니가 없습니다.</div>';
  const candidateRows = (bucket.candidates || []).map(r => `
    <tr>
      <td>${r.display_ticker || r.ticker || ''}</td>
      <td>${koBucket(r.primary_bucket || '')}</td>
      <td>${(r.secondary_buckets || []).map(koBucket).join(', ')}</td>
      <td>${fmtSignedPct(r.change_rate)}</td>
      <td>${fmtMoney(r.turnover || 0)}</td>
      <td>${r.volume_ratio || ''}</td>
      <td>${koCandidateStatus(r.status || '')}</td>
      <td>${r.input_to_claude ? '예' : '아니오'}</td>
      <td>${r.first_bucket_detected_at || ''}</td>
      <td>${bucketReasonText(r.bucket_reasons || {})}</td>
    </tr>
  `).join('');
  candidateEl.innerHTML = candidateRows
    ? `<table><thead><tr><th>종목</th><th>대표 바구니</th><th>보조 바구니</th><th>등락률</th><th>거래대금</th><th>거래량비</th><th>상태</th><th>Claude 입력</th><th>첫 감지</th><th>근거</th></tr></thead><tbody>${candidateRows}</tbody></table>`
    : '<div class="muted">표시할 후보가 없습니다.</div>';
}

function fmtMaybePct(v) {
  if (v === null || typeof v === 'undefined' || v === '') return '-';
  return fmtSignedPct(v);
}

function koBucket(v) {
  const m = {
    momentum_now: '현재 강한 상승',
    volume_surge: '거래량 급증',
    liquidity_leader: '유동성 주도',
    prev_strength: '전일 강세 지속',
    near_breakout: '돌파 근접',
    pullback_watch: '눌림목 관찰',
    pre_move_setup: '상승 전 셋업',
    sector_lagging_leader: '섹터 후발/리더',
    unclassified: '미분류'
  };
  return m[v] || v || '-';
}

function koBucketWarning(v) {
  const m = {
    NO_BUCKET_DATA: '바구니 데이터 없음',
    KOSDAQ_BUCKET_ZERO: 'KOSDAQ 후보 0',
    MARKET_TYPE_MISSING: 'KR 시장구분 누락',
    PRE_MOVE_NOT_IN_PROMPT: '상승 전 셋업 미입력',
    MOMENTUM_BAD_SIGNAL: '모멘텀 후보 손실 신호'
  };
  return m[v] || v || '';
}

function koCandidateStatus(v) {
  const m = {
    TRADE_READY: '매수 후보',
    WATCH: '관찰',
    VETO: '거부',
    NOT_IN_PROMPT: 'Claude 미입력',
    SCREENER_ONLY: '스크리너만'
  };
  return m[v] || v || '-';
}

function bucketReasonText(reasons) {
  if (!reasons || typeof reasons !== 'object') return '';
  return Object.entries(reasons).map(([k, v]) => `${koBucket(k)}: ${v}`).join(' | ');
}

function resetChart(key) {
  if (charts[key]) {
    charts[key].destroy();
    delete charts[key];
  }
}

function drawLineChart(canvasId, key, data, label, color) {
  resetChart(key);
  const el = document.getElementById(canvasId);
  if (!el) return;
  charts[key] = new Chart(el.getContext('2d'), {
    type: 'line',
    data: {
      labels: data.labels || [],
      datasets: [{
        label,
        data: data.data || [],
        borderColor: color,
        backgroundColor: 'rgba(16,185,129,0.12)',
        tension: 0.25,
        fill: true
      }]
    },
    options: chartOptions('%')
  });
}

function drawBarChart(canvasId, key, data, color) {
  resetChart(key);
  const el = document.getElementById(canvasId);
  if (!el) return;
  charts[key] = new Chart(el.getContext('2d'), {
    type: 'bar',
    data: {
      labels: data.labels || [],
      datasets: [{ label: '비율 %', data: data.data || [], backgroundColor: color }]
    },
    options: chartOptions('%')
  });
}

function drawDoughnutChart(canvasId, key, data) {
  resetChart(key);
  const el = document.getElementById(canvasId);
  if (!el) return;
  charts[key] = new Chart(el.getContext('2d'), {
    type: 'doughnut',
    data: {
      labels: data.labels || [],
      datasets: [{
        data: data.data || [],
        backgroundColor: ['#06b6d4', '#10b981', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6', '#64748b']
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } }
    }
  });
}

function chartOptions(suffix) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    scales: {
      x: { ticks: { color: '#64748b' }, grid: { color: 'rgba(100,116,139,0.12)' } },
      y: {
        ticks: { color: '#64748b', callback: v => `${v}${suffix || ''}` },
        grid: { color: 'rgba(100,116,139,0.12)' }
      }
    },
    plugins: { legend: { labels: { color: '#94a3b8', font: { size: 11 } } } }
  };
}
loadPathB();
setInterval(loadPathB, 10000);
</script>
"""


# ?? Flask ?쇱슦?????????????????????????????????????????????????????????????????

PAGE_PREOPEN_HTML = """
<main>
  <section class="section">
    <div class="section-head">
      <div><h2>장전 후보 Shadow</h2><p>주문/TRADE_READY에 영향 없는 preopen 관찰 데이터</p></div>
      <button class="btn small" onclick="loadPreopen()">새로고침</button>
    </div>
    <div class="grid-4">
      <div class="card"><div class="card-sub">수집 상태</div><div class="metric" id="preopen-status">-</div></div>
      <div class="card"><div class="card-sub">후보 수</div><div class="metric" id="preopen-count">-</div></div>
      <div class="card"><div class="card-sub">상태 나이</div><div class="metric" id="preopen-age">-</div></div>
      <div class="card"><div class="card-sub">Rank Diff</div><div class="metric" id="preopen-diff-count">-</div></div>
    </div>
  </section>
  <section class="section">
    <div class="section-head"><h2>Top Preopen Candidates</h2></div>
    <div id="preopen-candidates" class="table-wrap"></div>
  </section>
  <section class="section">
    <div class="section-head"><h2>Rank Diff</h2></div>
    <div id="preopen-rank-diff" class="table-wrap"></div>
  </section>
  <section class="section">
    <div class="section-head"><h2>Post-open Outcome</h2></div>
    <div id="preopen-outcome" class="table-wrap"></div>
  </section>
</main>
<script>
function fmtPreopen(v) {
  if (v === null || v === undefined || v === '') return '-';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}
function tags(v) {
  return Array.isArray(v) ? v.join(', ') : (v || '-');
}
function tableOrEmpty(rows, headers, mapper) {
  if (!rows || !rows.length) return '<div class="muted">데이터 없음</div>';
  return '<table><thead><tr>' + headers.map(h => `<th>${h}</th>`).join('') + '</tr></thead><tbody>' +
    rows.map(r => '<tr>' + mapper(r).map(c => `<td>${c}</td>`).join('') + '</tr>').join('') +
    '</tbody></table>';
}
async function loadPreopen() {
  const market = getMarket();
  const d = await apiGet('/api/preopen', 'market=' + encodeURIComponent(market)).then(r => r.json()).catch(() => ({}));
  const s = d.summary || {};
  document.getElementById('preopen-status').textContent = s.collector_status || '-';
  document.getElementById('preopen-count').textContent = s.candidate_count ?? 0;
  document.getElementById('preopen-age').textContent = s.state_age_min === null || s.state_age_min === undefined ? '-' : `${s.state_age_min}m`;
  document.getElementById('preopen-diff-count').textContent = s.rank_diff_count ?? 0;
  document.getElementById('preopen-candidates').innerHTML = tableOrEmpty(
    d.candidates || [],
    ['순위','종목','점수','등급','변동%','거래대금','스프레드','태그'],
    r => [fmtPreopen(r.shadow_preopen_rank), r.ticker || '', fmtPreopen(r.preopen_score), r.preopen_grade || '',
          fmtPreopen(r.extended_change_pct), fmtPreopen(r.extended_dollar_volume), fmtPreopen(r.spread_pct), tags(r.risk_tags)]
  );
  document.getElementById('preopen-rank-diff').innerHTML = tableOrEmpty(
    d.rank_diff || [],
    ['종목','Preopen','Claude','차이','선택','TradeReady','주문','사유'],
    r => [r.ticker || '', fmtPreopen(r.shadow_preopen_rank), fmtPreopen(r.actual_selection_rank), fmtPreopen(r.rank_delta),
          r.actual_selected ? 'Y' : '-', r.actual_trade_ready ? 'Y' : '-', r.actual_ordered ? 'Y' : '-', r.actual_rejection_reason || '']
  );
  document.getElementById('preopen-outcome').innerHTML = tableOrEmpty(
    d.outcome || [],
    ['종목','Offset','상태','5m','30m','60m','MFE','MAE'],
    r => [r.ticker || '', fmtPreopen(r.offset_min), r.outcome_status || '', fmtPreopen(r.post_open_5m_return_pct),
          fmtPreopen(r.post_open_30m_return_pct), fmtPreopen(r.post_open_60m_return_pct),
          fmtPreopen(r.post_open_mfe_pct), fmtPreopen(r.post_open_mae_pct)]
  );
}
loadPreopen();
setInterval(loadPreopen, 30000);
</script>
"""


PAGE_LOGS_HTML = """
<main>
  <section class="section">
    <div class="section-title">위험 / 오류 로그</div>
    <div class="grid" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));">
      <div class="card"><div class="card-sub">전체 알림</div><div class="metric" id="log-count">-</div></div>
      <div class="card"><div class="card-sub">에러</div><div class="metric danger" id="log-errors">-</div></div>
      <div class="card"><div class="card-sub">경고</div><div class="metric" id="log-warnings">-</div></div>
      <div class="card"><div class="card-sub">상태 파일</div><div class="metric" id="log-state">-</div></div>
    </div>
    <div class="period-bar" style="margin-top:12px">
      <button class="period-btn active" data-log-filter="all" onclick="setLogFilter('all')">전체</button>
      <button class="period-btn" data-log-filter="error" onclick="setLogFilter('error')">에러</button>
      <button class="period-btn" data-log-filter="order" onclick="setLogFilter('order')">주문 위험</button>
      <button class="period-btn" data-log-filter="broker" onclick="setLogFilter('broker')">브로커</button>
      <button class="period-btn" data-log-filter="pathb" onclick="setLogFilter('pathb')">B플랜</button>
      <button class="apply-btn" onclick="loadLogs()">새로고침</button>
      <span id="log-updated" class="muted" style="font-size:12px"></span>
    </div>
  </section>
  <section class="section">
    <div id="logs-table" class="table-wrap"></div>
  </section>
</main>
<script>
let LOG_FILTER = 'all';
let LOG_ROWS = [];

function escapeHtml(v) {
  return String(v ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  }[ch]));
}

function setLogFilter(filter) {
  LOG_FILTER = filter;
  document.querySelectorAll('[data-log-filter]').forEach(btn => btn.classList.toggle('active', btn.dataset.logFilter === filter));
  renderLogs();
}

function logLevelClass(level) {
  const v = String(level || '').toUpperCase();
  if (v === 'ERROR' || v === 'CRITICAL') return 'danger';
  if (v === 'WARNING') return 'warn';
  return '';
}

function logMatchesFilter(row) {
  const hay = `${row.level || ''} ${row.source || ''} ${row.category || ''} ${row.message || ''}`.toLowerCase();
  if (LOG_FILTER === 'error') return hay.includes('error') || hay.includes('critical');
  if (LOG_FILTER === 'order') return hay.includes('order') || hay.includes('주문') || hay.includes('safety') || hay.includes('timing');
  if (LOG_FILTER === 'broker') return hay.includes('broker') || hay.includes('계좌');
  if (LOG_FILTER === 'pathb') return hay.includes('pathb') || hay.includes('b플랜') || hay.includes('order_unknown');
  return true;
}

function renderLogs() {
  const rows = LOG_ROWS.filter(logMatchesFilter);
  const table = document.getElementById('logs-table');
  if (!table) return;
  if (!rows.length) {
    table.innerHTML = '<div class="muted">표시할 위험/오류 로그가 없습니다.</div>';
    return;
  }
  table.innerHTML = `<table>
    <thead><tr><th>시간</th><th>등급</th><th>출처</th><th>시장</th><th>종목</th><th>분류</th><th>메시지</th></tr></thead>
    <tbody>${rows.map(row => `
      <tr>
        <td>${escapeHtml(String(row.timestamp || '').replace('T', ' ').slice(0, 19))}</td>
        <td class="${logLevelClass(row.level)}">${escapeHtml(row.level || '-')}</td>
        <td>${escapeHtml(row.source || '-')}</td>
        <td>${escapeHtml(row.market || '-')}</td>
        <td>${escapeHtml(row.ticker || '-')}</td>
        <td>${escapeHtml(row.category || '-')}</td>
        <td style="max-width:720px;white-space:normal;word-break:break-word">${escapeHtml(row.message || '')}</td>
      </tr>`).join('')}</tbody>
  </table>`;
}

async function loadLogs() {
  const d = await apiGet('/api/logs/alerts', 'market=' + encodeURIComponent(MARKET) + '&limit=250').then(r => r.json()).catch(() => ({}));
  LOG_ROWS = d.alerts || [];
  const errors = LOG_ROWS.filter(r => ['ERROR', 'CRITICAL'].includes(String(r.level || '').toUpperCase())).length;
  const warnings = LOG_ROWS.filter(r => String(r.level || '').toUpperCase() === 'WARNING').length;
  const stateAlerts = (d.state_file_alerts || []).length;
  document.getElementById('log-count').textContent = LOG_ROWS.length;
  document.getElementById('log-errors').textContent = errors;
  document.getElementById('log-warnings').textContent = warnings;
  document.getElementById('log-state').textContent = stateAlerts;
  document.getElementById('log-updated').textContent = '갱신 ' + new Date().toLocaleTimeString();
  renderLogs();
}

loadLogs();
setInterval(loadLogs, 15000);
</script>
"""


@app.route("/")
def page_today():
    html = (
        _head("오늘 현황")
        + _header_html("/")
        + COMMON_JS_BLOCK
        + PAGE_TODAY_HTML
        + TODAY_SUMMARY_OVERRIDE_JS_V2
        + TODAY_SIGNAL_DIGEST_JS
        + CLAUDE_CONTROL_JS
        + "</body></html>"
    )
    return render_template_string(html)


@app.route("/history")
def page_history():
    return redirect("/pathb")


@app.route("/trades")
def page_trades():
    return redirect("/pathb")


@app.route("/broker-trades")
def page_broker_trades():
    return redirect("/pathb")


@app.route("/pathb")
def page_pathb():
    html = (
        _head("B플랜 실시간")
        + _header_html("/pathb")
        + COMMON_JS_BLOCK
        + PAGE_PATHB_HTML
        + "</body></html>"
    )
    return render_template_string(html)


@app.route("/preopen")
def page_preopen():
    html = (
        _head("장전 후보")
        + _header_html("/preopen")
        + COMMON_JS_BLOCK
        + PAGE_PREOPEN_HTML
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


@app.route("/logs")
def page_logs():
    html = (
        _head("로그")
        + _header_html("/logs")
        + COMMON_JS_BLOCK
        + PAGE_LOGS_HTML
        + "</body></html>"
    )
    return render_template_string(html)


# ?? ?ㅽ뻾 ??????????????????????????????????????????????????????????????????????

if __name__ == "__main__":
    _write_pid_file(DASHBOARD_PID_PATH, "dashboard_server", [sys.executable, str(Path(__file__).resolve())])
    atexit.register(lambda: _clear_pid_file(DASHBOARD_PID_PATH))
    print("=" * 52)
    print("  TRADINGBRAIN Dashboard 시작")
    print("  http://localhost:5000 으로 접속하세요")
    print()
    print("  /            오늘 현황")
    print("  /pathb       B플랜 실시간")
    print("  /preopen     장전 후보")
    print("  /analytics   분석")
    print("  /logs        위험/오류 로그")
    print("=" * 52)
    app.run(host="0.0.0.0", port=5000, debug=False)
