#!/usr/bin/env python3
"""brain.json 정책메모리 오염 정리 — 봇 정지 정비창 전용 turnkey 도구.

2026-06-23 무결성 감사 B영역 발견을 보수적으로 정리한다. **봇이 brain.json을 라이브로
쓰므로(version 실시간 증가), 반드시 봇 정지 상태에서만 --apply 한다.** 기본은 dry-run.

정리 범위(보수적·되돌릴 수 있는 것만 자동):
  1) KR issue_patterns 내 US종목 오염 제거 — KR brain에 US티커(SRPT/BRZE/PAYS 등)로
     기록된 항목은 시장 혼입이므로 제거(B형 오염).

리포트만(자동 변형 안 함 — 판단/주입게이트 영역):
  - issue_patterns count<=1 과적합 비율 (삭제가 아니라 주입측 count>=2 게이트로 다뤄야 함)
  - description == insight 동일(매칭 깨짐)
  - correction_guide generated_date 신선도

--apply 시 state/backups/brain_YYYYMMDD_HHMMSS.json 백업을 먼저 만든다. 멱등.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BRAIN_PATH = ROOT / "state" / "brain.json"
BACKUP_DIR = ROOT / "state" / "backups"

# KR brain에 들어오면 안 되는 US 티커(오염 탐지용). 무결성 도구와 동일 집합.
US_TICKER_RE = re.compile(
    r"\b(SRPT|BRZE|PAYS|NVDA|TSLA|AAPL|AMD|AVGO|INTC|MSFT|GOOGL|META|AMZN|QQQ|SPY|"
    r"PLTR|SOFI|IREN|IONQ|MRVL|KLAC|TXG|CWAN|SYRE)\b"
)


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _kr_us_pollution(brain: dict[str, Any]) -> list[tuple[int, dict]]:
    kr = brain.get("markets", {}).get("KR", {}).get("issue_patterns", []) or []
    hits = []
    for idx, p in enumerate(kr):
        txt = (p.get("description") or "") + " " + (p.get("insight") or "")
        if US_TICKER_RE.search(txt):
            hits.append((idx, p))
    return hits


def _report_only(brain: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for mk in ("KR", "US"):
        ips = brain.get("markets", {}).get(mk, {}).get("issue_patterns", []) or []
        if not ips:
            continue
        c1 = sum(1 for p in ips if int(p.get("count", 0) or 0) <= 1)
        lines.append(f"  [{mk}] issue_patterns count<=1: {c1}/{len(ips)} "
                     f"({100*c1/len(ips):.0f}%) — 주입측 count>=2 게이트로 처리 권장(삭제 X)")
        same = sum(1 for p in ips if (p.get("description") or "").strip()
                   and (p.get("description") or "").strip() == (p.get("insight") or "").strip())
        if same:
            lines.append(f"  [{mk}] description==insight 동일: {same}건")
    cg = brain.get("correction_guide", {}) or {}
    now = datetime.now(timezone.utc)
    for mk in ("KR", "US"):
        gd = (cg.get(mk, {}) or {}).get("generated_date")
        if gd:
            try:
                age = (now - datetime.fromisoformat(gd).replace(tzinfo=timezone.utc)).days
                lines.append(f"  [{mk}] correction_guide {age}일 전({gd}) — 신선도 게이트/날짜메타 검토")
            except ValueError:
                pass
    return lines


def main() -> int:
    ap = argparse.ArgumentParser(description="brain.json 오염 정리 (봇 정지 시 --apply)")
    ap.add_argument("--apply", action="store_true", help="실제 적용(미지정=dry-run). 봇 정지 상태에서만.")
    ap.add_argument("--brain", default=str(BRAIN_PATH))
    args = ap.parse_args()

    path = Path(args.brain)
    brain = _load(path)
    hits = _kr_us_pollution(brain)

    print("=== brain 오염 정리 (dry-run)" + (" → APPLY" if args.apply else "") + " ===")
    print(f"\n[자동 제거 대상] KR issue_patterns 내 US종목 오염: {len(hits)}건")
    for idx, p in hits:
        print(f"  - [{idx}] count={p.get('count')} desc={p.get('description','')[:70]}")

    print("\n[리포트만 — 자동 변형 안 함]")
    for line in _report_only(brain):
        print(line)

    if not args.apply:
        print("\n※ dry-run. 적용하려면 --apply (봇 정지 상태에서만).")
        return 0

    if not hits:
        print("\n제거 대상 없음 — 변경 없음(멱등).")
        return 0

    # 백업 후 제거
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"brain_{stamp}.json"
    shutil.copy2(path, backup)
    print(f"\n백업 생성: {backup}")

    remove_idx = {idx for idx, _ in hits}
    kr = brain["markets"]["KR"]["issue_patterns"]
    brain["markets"]["KR"]["issue_patterns"] = [p for i, p in enumerate(kr) if i not in remove_idx]
    meta = brain.setdefault("meta", {})
    meta["brain_pollution_cleaned_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    path.write_text(json.dumps(brain, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"적용 완료: KR issue_patterns {len(remove_idx)}건 제거 → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
