from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_paths import get_runtime_path

KST = timezone(timedelta(hours=9))

PRICE_BY_MODEL_PER_M = {
    "haiku": (
        float(os.getenv("CLAUDE_PRICE_HAIKU_INPUT_PER_M", "0.80")),
        float(os.getenv("CLAUDE_PRICE_HAIKU_OUTPUT_PER_M", "4.00")),
    ),
    "sonnet": (
        float(os.getenv("CLAUDE_PRICE_SONNET_INPUT_PER_M", "3.00")),
        float(os.getenv("CLAUDE_PRICE_SONNET_OUTPUT_PER_M", "15.00")),
    ),
    "opus": (
        float(os.getenv("CLAUDE_PRICE_OPUS_INPUT_PER_M", "15.00")),
        float(os.getenv("CLAUDE_PRICE_OPUS_OUTPUT_PER_M", "75.00")),
    ),
}


@dataclass(frozen=True)
class UsageEvent:
    event_id: str
    timestamp: datetime
    market: str
    label: str
    category: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    source: str

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


def now_kst() -> datetime:
    return datetime.now(KST)


def parse_dt(value: Any) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        try:
            parsed = datetime.fromisoformat(raw[:19])
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def parse_start_at(raw: str, *, now: datetime | None = None) -> datetime:
    current = now or now_kst()
    value = str(raw or "").strip()
    if not value:
        return current
    parsed = parse_dt(value)
    if parsed is not None:
        return parsed
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            parsed_time = datetime.strptime(value, fmt).time()
            return datetime.combine(current.date(), parsed_time, tzinfo=KST)
        except Exception:
            pass
    raise ValueError(f"invalid --start-at: {raw}")


def category_for_label(label: str) -> str:
    normalized = str(label or "").strip().lower()
    if normalized.startswith("hold_advisor"):
        return "홀드어드바이저"
    if normalized.startswith("quick_exit"):
        return "빠른청산"
    if normalized.startswith("select_tickers") or "screener" in normalized or "selection" in normalized:
        return "티커 스크리너"
    if normalized.startswith("analyst_"):
        return "애널리스트"
    if normalized.startswith("tune_") or normalized == "param_tuner" or normalized.endswith("_tuner"):
        return "튜너"
    if normalized.startswith("postmortem"):
        return "포스트모템"
    if normalized.startswith("pathb") or "claude_price" in normalized:
        return "PathB 가격플랜"
    if normalized.startswith("preopen"):
        return "프리오픈"
    return "기타"


def model_prices(model: str) -> tuple[float, float]:
    model_l = str(model or "").lower()
    for key, prices in PRICE_BY_MODEL_PER_M.items():
        if key in model_l:
            return prices
    return PRICE_BY_MODEL_PER_M["sonnet"]


def calc_cost(input_tokens: int, output_tokens: int, model: str) -> float:
    input_per_m, output_per_m = model_prices(model)
    return round((input_tokens / 1_000_000 * input_per_m) + (output_tokens / 1_000_000 * output_per_m), 6)


def safe_int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except Exception:
        return 0


def safe_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except Exception:
        return 0.0


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def date_prefixes_from(start_at: datetime, *, end_date: date | None = None) -> list[str]:
    last_date = end_date or max(now_kst().date(), start_at.date())
    prefixes: list[str] = []
    cursor = start_at.date()
    while cursor <= last_date:
        prefixes.append(cursor.strftime("%Y%m%d"))
        cursor += timedelta(days=1)
    return prefixes


def raw_call_events(root: Path, *, start_at: datetime, mode: str) -> list[UsageEvent]:
    raw_dir = root / "logs" / "raw_calls"
    if not raw_dir.exists():
        return []
    events: list[UsageEvent] = []
    paths: list[Path] = []
    for prefix in date_prefixes_from(start_at):
        paths.extend(raw_dir.glob(f"{prefix}_*.json"))
    for path in paths:
        payload = read_json(path)
        if not isinstance(payload, dict):
            continue
        ts = parse_dt(payload.get("timestamp"))
        if ts is None or ts < start_at:
            continue
        label = str(payload.get("label") or "unknown")
        tokens = payload.get("tokens") if isinstance(payload.get("tokens"), dict) else {}
        input_tokens = safe_int(tokens.get("input") or tokens.get("input_tokens") or payload.get("input_tokens"))
        output_tokens = safe_int(tokens.get("output") or tokens.get("output_tokens") or payload.get("output_tokens"))
        model = str(payload.get("model") or "")
        cost = calc_cost(input_tokens, output_tokens, model)
        event_id = str(payload.get("call_id") or path.name)
        market = str(payload.get("market") or "").upper()
        events.append(
            UsageEvent(
                event_id=f"raw:{event_id}",
                timestamp=ts,
                market=market,
                label=label,
                category=category_for_label(label),
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost,
                source=str(path.relative_to(root)),
            )
        )
    return sorted(events, key=lambda event: (event.timestamp, event.event_id))


def usage_json_events(root: Path, *, start_at: datetime, mode: str) -> list[UsageEvent]:
    path = root / "state" / f"{mode}_api_usage.json"
    payload = read_json(path)
    if not isinstance(payload, dict):
        return []
    sessions = payload.get("sessions") if isinstance(payload.get("sessions"), list) else []
    events: list[UsageEvent] = []
    for index, row in enumerate(sessions):
        if not isinstance(row, dict):
            continue
        session_date = str(row.get("date") or "")
        session_time = str(row.get("ts") or "")
        ts = parse_dt(f"{session_date}T{session_time}+09:00")
        if ts is None or ts < start_at:
            continue
        label = str(row.get("label") or "unknown")
        input_tokens = safe_int(row.get("input_tokens"))
        output_tokens = safe_int(row.get("output_tokens"))
        model = str(row.get("model") or "")
        cost = safe_float(row.get("cost_usd")) or calc_cost(input_tokens, output_tokens, model)
        stable = f"{session_date}:{session_time}:{label}:{input_tokens}:{output_tokens}:{index}"
        events.append(
            UsageEvent(
                event_id=f"usage:{stable}",
                timestamp=ts,
                market="",
                label=label,
                category=category_for_label(label),
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=round(cost, 6),
                source=str(path.relative_to(root)),
            )
        )
    return sorted(events, key=lambda event: (event.timestamp, event.event_id))


def collect_usage_events(root: Path, *, start_at: datetime, mode: str) -> list[UsageEvent]:
    raw_events = raw_call_events(root, start_at=start_at, mode=mode)
    if raw_events:
        return raw_events
    return usage_json_events(root, start_at=start_at, mode=mode)


def empty_bucket() -> dict[str, Any]:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "cost_usd": 0.0}


def summarize(events: Iterable[UsageEvent]) -> dict[str, Any]:
    total = empty_bucket()
    by_category: dict[str, dict[str, Any]] = defaultdict(empty_bucket)
    by_label: dict[str, dict[str, Any]] = defaultdict(empty_bucket)
    by_market: dict[str, dict[str, Any]] = defaultdict(empty_bucket)
    by_model: dict[str, dict[str, Any]] = defaultdict(empty_bucket)

    for event in events:
        for bucket in (
            total,
            by_category[event.category],
            by_label[event.label],
            by_market[event.market or "UNKNOWN"],
            by_model[event.model or "unknown"],
        ):
            bucket["calls"] += 1
            bucket["input_tokens"] += event.input_tokens
            bucket["output_tokens"] += event.output_tokens
            bucket["total_tokens"] += event.total_tokens
            bucket["cost_usd"] = round(bucket["cost_usd"] + event.cost_usd, 6)

    return {
        "total": dict(total),
        "by_category": dict(sorted(by_category.items(), key=lambda item: (-item[1]["cost_usd"], item[0]))),
        "by_label": dict(sorted(by_label.items(), key=lambda item: (-item[1]["cost_usd"], item[0]))),
        "by_market": dict(sorted(by_market.items())),
        "by_model": dict(sorted(by_model.items(), key=lambda item: (-item[1]["cost_usd"], item[0]))),
    }


def fmt_tokens(value: int) -> str:
    return f"{int(value):,}"


def fmt_usd(value: float) -> str:
    return f"${float(value):.6f}"


def fmt_krw(value: float, usd_krw: float) -> str:
    return f"{int(round(float(value) * usd_krw)):,}원"


def format_change_line(*, ts: datetime, delta: dict[str, Any], cumulative: dict[str, Any], usd_krw: float) -> str:
    total = delta["total"]
    cumulative_total = cumulative["total"]
    lines = [
        (
            f"[{ts.strftime('%Y-%m-%d %H:%M:%S')} KST] Claude 사용량 변화 "
            f"+{total['calls']}회, +{fmt_tokens(total['total_tokens'])} tokens "
            f"(in {fmt_tokens(total['input_tokens'])} / out {fmt_tokens(total['output_tokens'])}), "
            f"+{fmt_usd(total['cost_usd'])} ({fmt_krw(total['cost_usd'], usd_krw)})"
        ),
        (
            f"현재 Claude 사용량(모니터 시작 이후): {cumulative_total['calls']}회, "
            f"{fmt_tokens(cumulative_total['total_tokens'])} tokens, "
            f"{fmt_usd(cumulative_total['cost_usd'])} ({fmt_krw(cumulative_total['cost_usd'], usd_krw)})"
        ),
    ]
    for category, bucket in cumulative["by_category"].items():
        lines.append(
            f"- {category}: {bucket['calls']}회, {fmt_tokens(bucket['total_tokens'])} tokens, "
            f"{fmt_usd(bucket['cost_usd'])} ({fmt_krw(bucket['cost_usd'], usd_krw)})"
        )
    return "\n".join(lines)


def append_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text.rstrip() + "\n")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def write_pid_file(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


def default_log_paths(mode: str, session_date: date) -> tuple[Path, Path]:
    compact = session_date.strftime("%Y%m%d")
    base = get_runtime_path("logs", "system", f"claude_usage_monitor_{compact}_{mode}.log")
    jsonl = base.with_suffix(".jsonl")
    return base, jsonl


def monitor(args: argparse.Namespace) -> int:
    root = get_runtime_path()
    mode = str(args.mode or "live").strip().lower()
    start_at = parse_start_at(args.start_at)
    end_at = parse_dt(args.end_at) if args.end_at else None
    interval = max(0.5, float(args.interval_sec or 2.0))
    usd_krw = safe_float(args.usd_krw) or safe_float(os.getenv("USD_KRW_RATE", "1350")) or 1350.0
    text_log, jsonl_log = default_log_paths(mode, start_at.date())
    if args.log_path:
        text_log = Path(args.log_path)
        if not text_log.is_absolute():
            text_log = root / text_log
    if args.jsonl_path:
        jsonl_log = Path(args.jsonl_path)
        if not jsonl_log.is_absolute():
            jsonl_log = root / jsonl_log

    pid_path = get_runtime_path("state", f"claude_usage_monitor_{mode}.json")
    write_pid_file(
        pid_path,
        {
            "pid": os.getpid(),
            "mode": mode,
            "start_at": start_at.isoformat(timespec="seconds"),
            "end_at": end_at.isoformat(timespec="seconds") if end_at else "",
            "interval_sec": interval,
            "text_log": str(text_log),
            "jsonl_log": str(jsonl_log),
            "started_at": now_kst().isoformat(timespec="seconds"),
        },
    )

    started_line = (
        f"[{now_kst().strftime('%Y-%m-%d %H:%M:%S')} KST] Claude 사용량 모니터 시작 "
        f"mode={mode} start_at={start_at.isoformat(timespec='seconds')} "
        f"text_log={text_log} jsonl={jsonl_log}"
    )
    append_text(text_log, started_line)
    print(started_line, flush=True)
    append_jsonl(
        jsonl_log,
        {
            "event_type": "monitor_started",
            "written_at": now_kst().isoformat(timespec="seconds"),
            "mode": mode,
            "start_at": start_at.isoformat(timespec="seconds"),
            "text_log": str(text_log),
            "jsonl_log": str(jsonl_log),
        },
    )

    seen: set[str] = set()
    all_events: list[UsageEvent] = []

    while True:
        current = now_kst()
        if current < start_at:
            time.sleep(min(interval, max(0.5, (start_at - current).total_seconds())))
            continue
        if end_at is not None and current >= end_at:
            stopped_line = f"[{current.strftime('%Y-%m-%d %H:%M:%S')} KST] Claude 사용량 모니터 종료 end_at 도달"
            append_text(text_log, stopped_line)
            print(stopped_line, flush=True)
            append_jsonl(
                jsonl_log,
                {
                    "event_type": "monitor_stopped",
                    "written_at": current.isoformat(timespec="seconds"),
                    "reason": "end_at",
                },
            )
            return 0

        events = collect_usage_events(root, start_at=start_at, mode=mode)
        new_events = [event for event in events if event.event_id not in seen]
        if new_events:
            for event in new_events:
                seen.add(event.event_id)
            all_events.extend(new_events)
            delta = summarize(new_events)
            cumulative = summarize(all_events)
            line = format_change_line(ts=current, delta=delta, cumulative=cumulative, usd_krw=usd_krw)
            append_text(text_log, line)
            print(line, flush=True)
            append_jsonl(
                jsonl_log,
                {
                    "event_type": "usage_delta",
                    "written_at": current.isoformat(timespec="seconds"),
                    "mode": mode,
                    "start_at": start_at.isoformat(timespec="seconds"),
                    "delta": delta,
                    "cumulative": cumulative,
                    "events": [
                        {
                            "event_id": event.event_id,
                            "timestamp": event.timestamp.isoformat(timespec="seconds"),
                            "market": event.market,
                            "label": event.label,
                            "category": event.category,
                            "model": event.model,
                            "input_tokens": event.input_tokens,
                            "output_tokens": event.output_tokens,
                            "total_tokens": event.total_tokens,
                            "cost_usd": event.cost_usd,
                            "source": event.source,
                        }
                        for event in new_events
                    ],
                },
            )

        time.sleep(interval)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Monitor Claude usage deltas by functional category.")
    parser.add_argument("--mode", choices=("live", "paper"), default="live")
    parser.add_argument("--start-at", default="10:00", help="KST time HH:MM or ISO timestamp. Default: 10:00 today.")
    parser.add_argument("--end-at", default="", help="Optional KST/ISO timestamp to stop monitoring.")
    parser.add_argument("--interval-sec", type=float, default=2.0)
    parser.add_argument("--log-path", default="", help="Optional text log path. Relative paths are under runtime root.")
    parser.add_argument("--jsonl-path", default="", help="Optional JSONL log path. Relative paths are under runtime root.")
    parser.add_argument("--usd-krw", type=float, default=0.0, help="Optional USD/KRW display rate.")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return monitor(args)


if __name__ == "__main__":
    raise SystemExit(main())
