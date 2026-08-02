"""claudetrade-audit MCP 재현 가능한 smoke 테스트 (2026-08-02 외부 리뷰 지적 반영).

서버 프로세스를 실제 stdio JSON-RPC로 띄워 다음을 검증한다:
  1. initialize 핸드셰이크
  2. tools/list 7도구 노출
  3. check_buy_gate — verdict·authority·broker 컴포넌트 구조
  4. get_effective_config — redacted 스냅샷(비밀키 마스킹 확인)
  5. get_open_positions — 브로커 truth 구조
  6. get_recent_db_health — DB read-only 조회
  7. 알 수 없는 tool 거부(JSON-RPC error)
  8. 손상된 JSON 입력 라인 무시(서버 생존)

사용: python tools/claudetrade_audit_mcp_smoke.py  (exit 0=전부 통과)
pytest에서도 실행된다: tests 쪽 러너 없이 파일 단독.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SERVER = ROOT / "tools" / "claudetrade_audit_mcp.py"


def _run_session(messages: list[str]) -> list[dict]:
    proc = subprocess.run(
        [sys.executable, str(SERVER)],
        input="".join(m + "\n" for m in messages),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        cwd=str(ROOT),
    )
    out = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def main() -> int:
    msgs = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                               "clientInfo": {"name": "smoke", "version": "0"}}}),
        json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
        "{corrupted json line",  # 8. 손상 입력 — 서버가 무시하고 계속 살아야 한다
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "check_buy_gate", "arguments": {}}}),
        json.dumps({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "get_effective_config",
                               "arguments": {"keys": ["ANTHROPIC_API_KEY", "LEGACY_NEW_BUY_DISABLED"]}}}),
        json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call",
                    "params": {"name": "get_open_positions", "arguments": {}}}),
        json.dumps({"jsonrpc": "2.0", "id": 6, "method": "tools/call",
                    "params": {"name": "get_recent_db_health", "arguments": {}}}),
        json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                    "params": {"name": "no_such_tool", "arguments": {}}}),
    ]
    replies = {m.get("id"): m for m in _run_session(msgs)}
    failures: list[str] = []

    def check(cond: bool, label: str) -> None:
        print(("PASS  " if cond else "FAIL  ") + label)
        if not cond:
            failures.append(label)

    # 1. initialize
    init = (replies.get(1) or {}).get("result") or {}
    check(init.get("serverInfo", {}).get("name") == "claudetrade-audit", "initialize handshake")

    # 2. tools/list — 7도구
    tools = [t["name"] for t in ((replies.get(2) or {}).get("result") or {}).get("tools", [])]
    check(len(tools) == 7 and "check_buy_gate" in tools, f"tools/list 7개 노출 (got {len(tools)})")

    def body(rid: int) -> dict:
        try:
            return json.loads(replies[rid]["result"]["content"][0]["text"])
        except Exception:
            return {}

    # 3. check_buy_gate 구조 — 분리된 컴포넌트와 authority/broker 첨부
    gate = body(3)
    us = (gate.get("verdict") or {}).get("US") or {}
    check(
        all(k in us for k in ("us_swing_configured", "us_swing_authority_allowed",
                              "us_swing_broker_truth_ready", "legacy_paths_blocked"))
        and "us_swing_authority" in gate and "broker_truth" in gate,
        "check_buy_gate 컴포넌트 분리 구조",
    )
    check((gate.get("meta") or {}).get("schema_version") == "audit_mcp_v1", "meta schema_version")

    # 4. redacted 스냅샷 — 비밀키가 마스킹된 형태(***)로만 나와야 한다
    eff = (body(4).get("effective") or {})
    api_key = str(eff.get("ANTHROPIC_API_KEY") or "")
    check(api_key == "" or "*" in api_key, "API 키 마스킹")
    check(str(eff.get("LEGACY_NEW_BUY_DISABLED") or "") in {"true", "false"}, "effective 키 조회")

    # 5. 보유 구조
    pos = body(5).get("positions") or {}
    check(set(pos.keys()) == {"KR", "US"}, "open_positions KR/US 구조")

    # 6. DB read-only 조회
    dbs = body(6).get("databases") or {}
    check("decisions" in dbs and "us_swing_shadow" in dbs, "db health 대상 포함")

    # 7. 미지 도구 거부
    check("error" in (replies.get(7) or {}), "unknown tool 거부")

    if failures:
        print(f"\nSMOKE FAILED: {len(failures)}건 — {failures}")
        return 1
    print("\nALL_SMOKE_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
