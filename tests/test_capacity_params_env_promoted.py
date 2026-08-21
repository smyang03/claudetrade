# -*- coding: utf-8 -*-
"""용량 파라미터 env 승격 계약 테스트 (2026-08-21).

배경: 슬롯·일한도·주문금액이 세 층에 흩어져 있었다.
  - env에 있는 것        : US_SWING_ORDER_MAX_KRW 등
  - env 키는 읽는데 안 적힌 것: KR_FALLEN_ORDER_MAX_KRW(코드 기본 30만) 등
  - 코드 하드코딩         : us_swing_order_bridge dict 리터럴(실주문) +
                           us_swing_execution_contract 상수(shadow·integrity_check)

특히 US 슬롯은 **같은 값이 두 군데** 있어 한쪽만 고치면 실주문과 shadow 계약이
갈라진다(ULS·LCID 코호트 분기 사고 유형). env 한 곳으로 모은다.

이 테스트가 지키는 계약:
  1) env를 안 주면 계약 payload가 승격 이전과 **동일**하다(동작 변경 0).
  2) env를 주면 그 값이 반영된다.
  3) 실주문·shadow·integrity_check 세 경로가 **같은 env 키**를 본다.
"""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from runtime.us_swing_execution_contract import (
    OPERATOR_TRIAL_MAX_NEW_PER_DAY,
    OPERATOR_TRIAL_MAX_OPEN_SLOTS,
    resolve_execution_contract,
)

ROOT = Path(__file__).resolve().parents[1]
POLICY = json.loads((ROOT / "config" / "us_swing_accelerated.json").read_text(encoding="utf-8"))

_COMMON = dict(
    policy=POLICY,
    effective_mode="micro",
    configured_max_order_krw=760000.0,
    base_order_budget_krw=500000.0,
    absolute_order_cap_krw=760000.0,
    override_active=True,
)


class ContractDefaultsUnchangedTests(unittest.TestCase):
    def test_defaults_match_operator_constants(self):
        """인자를 안 넘기면 기존 상수 그대로 — 승격이 동작을 바꾸지 않는다."""
        out = resolve_execution_contract(**_COMMON)
        self.assertEqual(out["max_open_slots"], OPERATOR_TRIAL_MAX_OPEN_SLOTS)
        self.assertEqual(out["max_new_per_day"], OPERATOR_TRIAL_MAX_NEW_PER_DAY)
        self.assertEqual(out["max_open_slots"], 5)
        self.assertEqual(out["max_new_per_day"], 1)

    def test_contract_id_stable_when_defaults_passed_explicitly(self):
        """기본값을 명시적으로 넘겨도 contract_id가 같아야 한다.

        contract_id가 바뀌면 판정 코호트의 지문이 갈라진다 — 승격만으로
        지문이 바뀌면 안 된다.
        """
        implicit = resolve_execution_contract(**_COMMON)["contract_id"]
        explicit = resolve_execution_contract(
            **_COMMON,
            max_open_slots_override=OPERATOR_TRIAL_MAX_OPEN_SLOTS,
            max_new_per_day_override=OPERATOR_TRIAL_MAX_NEW_PER_DAY,
        )["contract_id"]
        self.assertEqual(implicit, explicit)

    def test_override_values_are_applied(self):
        out = resolve_execution_contract(
            **_COMMON, max_open_slots_override=3, max_new_per_day_override=2
        )
        self.assertEqual(out["max_open_slots"], 3)
        self.assertEqual(out["max_new_per_day"], 2)


class EnvSourcesAgreeTests(unittest.TestCase):
    """두 소스(.env.live / start-config)와 세 코드 경로가 같은 키를 봐야 한다."""

    KEYS = [
        "US_SWING_MAX_OPEN_SLOTS",
        "US_SWING_MAX_NEW_PER_DAY",
        "KR_FALLEN_ORDER_MAX_KRW",
        "KR_FALLEN_MAX_OPEN_SLOTS",
        "KR_FALLEN_MAX_OPEN_SLOTS_PHASE3",
        "KR_FALLEN_MAX_NEW_PER_DAY",
    ]

    def test_keys_present_in_both_sources(self):
        env_text = (ROOT / ".env.live").read_text(encoding="utf-8", errors="replace")
        start_cfg = json.loads((ROOT / "config" / "v2_start_config.json").read_text(encoding="utf-8"))
        overrides = start_cfg.get("env_overrides") or start_cfg
        for key in self.KEYS:
            with self.subTest(key=key):
                self.assertRegex(env_text, rf"(?m)^{key}=", f".env.live에 {key} 없음")
                self.assertIn(key, overrides, f"start-config에 {key} 없음")

    def test_values_agree_between_sources(self):
        env_text = (ROOT / ".env.live").read_text(encoding="utf-8", errors="replace")
        start_cfg = json.loads((ROOT / "config" / "v2_start_config.json").read_text(encoding="utf-8"))
        overrides = start_cfg.get("env_overrides") or start_cfg
        for key in self.KEYS:
            m = re.search(rf"(?m)^{key}=(.*)$", env_text)
            self.assertIsNotNone(m, f".env.live에 {key} 없음")
            with self.subTest(key=key):
                self.assertEqual(
                    str(m.group(1)).strip(), str(overrides[key]).strip(),
                    f"{key}가 두 소스에서 다르다 — 한쪽만 바꾸면 조용히 덮어써진다",
                )

    def test_live_bridge_and_shadow_read_same_keys(self):
        """실주문 브리지와 shadow 러너가 같은 env 키를 참조하는지 소스로 확인."""
        bridge = (ROOT / "runtime" / "us_swing_order_bridge.py").read_text(encoding="utf-8")
        shadow = (ROOT / "tools" / "us_swing_shadow_runner.py").read_text(encoding="utf-8")
        integrity = (ROOT / "tools" / "integrity_check.py").read_text(encoding="utf-8")
        for key in ("US_SWING_MAX_OPEN_SLOTS", "US_SWING_MAX_NEW_PER_DAY"):
            with self.subTest(key=key):
                self.assertIn(key, bridge, f"실주문 브리지가 {key}를 안 읽는다")
                self.assertIn(key, shadow, f"shadow 러너가 {key}를 안 읽는다")
                self.assertIn(key, integrity, f"integrity_check가 {key}를 안 읽는다")


if __name__ == "__main__":
    unittest.main()
