# -*- coding: utf-8 -*-
"""브로커 기간손익 조회(조회 전용) — 2026-04-01 ~ 2026-09-24. 캐시 토큰만 사용."""
import json
import sys
from pathlib import Path

ROOT = Path(r"E:/code/claudetrade")
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(str(ROOT / ".env.live"), override=True)
OUT = ROOT / "data" / "analysis" / "live_history_20260924" / "broker_period.json"

tok = str(json.loads((ROOT / "state" / "live_kis_token.json").read_text(encoding="utf-8")).get("access_token") or "")
assert len(tok) > 40, "캐시 토큰 없음"
import kis_api  # noqa: E402

print("IS_PAPER", getattr(kis_api, "IS_PAPER", None))
out = {}
try:
    kr = kis_api.inquire_period_trade_profit_kr(tok, "20260401", "20260924", max_pages=30)
    out["KR"] = kr
    print("KR rows", len(kr["rows"]), "summary", kr["summary"])
except Exception as e:
    print("KR 실패", repr(e)[:300])
for ex in ("NASD", "NYSE", "AMEX", ""):
    try:
        us = kis_api.inquire_period_profit_us(tok, "20260401", "20260924", exchange_code=ex, max_pages=30)
        out[f"US_{ex or 'ALL'}"] = us
        print("US", ex or "ALL", "rows", len(us["rows"]), "summary", us["summary"])
    except Exception as e:
        print("US", ex, "실패", repr(e)[:300])
OUT.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print("saved", OUT)
