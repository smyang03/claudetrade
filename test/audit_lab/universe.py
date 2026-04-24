"""Universe construction for isolated audit-lab collection.

The live runtime may build candidates from APIs and prompts. The audit lab only
reads already-written files and source literals, then tags why each symbol was
included so later reports can compare core/fallback/dynamic performance.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .config import ROOT


KR_CORE_TICKERS = ("005930", "068270", "035420", "035720", "005380", "051910")
US_CORE_TICKERS = ("NVDA", "TSLA", "AAPL", "GOOGL", "NFLX")


@dataclass(frozen=True)
class UniverseMember:
    market: str
    raw_symbol: str
    universe_group: str
    sources: tuple[str, ...]
    name: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_raw_symbol(symbol: object, market: str) -> str:
    value = str(symbol or "").strip().upper()
    if not value:
        return ""
    if market.upper() == "KR":
        value = value.replace(".KS", "").replace(".KQ", "")
        return value.zfill(6) if value.isdigit() else value
    return value


def _parse_literal_list(source: str, name: str) -> list[str]:
    match = re.search(rf"{re.escape(name)}\s*=\s*(\[.*?\])", source, re.S)
    if not match:
        return []
    try:
        parsed = ast.literal_eval(match.group(1))
    except Exception:
        return []
    return [str(item).strip().upper() for item in parsed if str(item).strip()]


def load_fallback_universe(market: str, root: Path = ROOT) -> list[str]:
    """Read fallback literals from the live API source file without executing it."""

    path = Path(root) / "kis_api.py"
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8", errors="replace")
    name = "_KR_FALLBACK_UNIVERSE" if market.upper() == "KR" else "_US_FALLBACK_UNIVERSE"
    return [normalize_raw_symbol(item, market) for item in _parse_literal_list(text, name)]


def _extract_universe_tickers(payload: dict) -> list[str]:
    candidates = [
        payload.get("universe_tickers"),
        payload.get("digest_raw", {}).get("universe_tickers") if isinstance(payload.get("digest_raw"), dict) else None,
        payload.get("tickers"),
    ]
    for value in candidates:
        if isinstance(value, list):
            return [str(item) for item in value]
    return []


def load_shared_judgment_universe(market: str, root: Path = ROOT, recent_files: int = 5) -> list[str]:
    state_dir = Path(root) / "state"
    files = sorted(state_dir.glob(f"shared_judgment_{market.upper()}_*.json"))[-recent_files:]
    out: list[str] = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.extend(_extract_universe_tickers(payload))
    return [normalize_raw_symbol(item, market) for item in out if normalize_raw_symbol(item, market)]


def load_dynamic_snapshot_universe(market: str, root: Path = ROOT, recent_files: int = 5) -> list[str]:
    snap_dir = Path(root) / "data" / "universe" / market.upper()
    files = sorted(snap_dir.glob("*.json"))[-recent_files:] if snap_dir.exists() else []
    out: list[str] = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.extend(_extract_universe_tickers(payload))
    return [normalize_raw_symbol(item, market) for item in out if normalize_raw_symbol(item, market)]


def _group_from_sources(sources: set[str]) -> str:
    if "core" in sources:
        return "core"
    if "fallback" in sources:
        return "fallback"
    if "shared_judgment" in sources and "dynamic_snapshot" in sources:
        return "multi_source"
    if "dynamic_snapshot" in sources:
        return "dynamic_only"
    if "shared_judgment" in sources:
        return "shared_judgment_only"
    return "custom"


def build_live_universe(market: str, root: Path = ROOT, recent_files: int = 5) -> list[UniverseMember]:
    market_u = market.upper()
    source_map: dict[str, set[str]] = {}

    def add(symbols: list[str] | tuple[str, ...], source: str) -> None:
        for symbol in symbols:
            raw = normalize_raw_symbol(symbol, market_u)
            if not raw:
                continue
            source_map.setdefault(raw, set()).add(source)

    add(KR_CORE_TICKERS if market_u == "KR" else US_CORE_TICKERS, "core")
    add(load_fallback_universe(market_u, root=root), "fallback")
    add(load_shared_judgment_universe(market_u, root=root, recent_files=recent_files), "shared_judgment")
    add(load_dynamic_snapshot_universe(market_u, root=root, recent_files=recent_files), "dynamic_snapshot")

    members = [
        UniverseMember(
            market=market_u,
            raw_symbol=raw,
            universe_group=_group_from_sources(sources),
            sources=tuple(sorted(sources)),
        )
        for raw, sources in source_map.items()
    ]
    return sorted(members, key=lambda item: (item.universe_group != "core", item.raw_symbol))


def write_universe_manifest(members: list[UniverseMember], path: Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "count": len(members),
        "markets": sorted({member.market for member in members}),
        "groups": {
            group: sum(1 for member in members if member.universe_group == group)
            for group in sorted({member.universe_group for member in members})
        },
        "members": [member.to_dict() for member in members],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
