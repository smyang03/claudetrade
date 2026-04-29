"""Simulation policies derived from audit results.

Policies are intentionally conservative and local to audit_lab. They are not
live-trading switches; they define which combinations deserve further shadow
testing before any production change is considered.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PolicyRule:
    market: str
    strategy: str
    entry_model: str
    universe_groups: tuple[str, ...]
    reason: str


POLICIES: dict[str, tuple[PolicyRule, ...]] = {
    "none": (),
    "profit_guard_v1": (
        PolicyRule(
            market="KR",
            strategy="momentum",
            entry_model="gap_filter",
            universe_groups=("core",),
            reason="KR core momentum showed positive PF with tradable gap-filter entry.",
        ),
        PolicyRule(
            market="KR",
            strategy="momentum",
            entry_model="confirmation_next_open",
            universe_groups=("core",),
            reason="KR core momentum remained positive after delayed confirmation entry.",
        ),
        PolicyRule(
            market="US",
            strategy="mean_reversion",
            entry_model="gap_filter",
            universe_groups=("fallback",),
            reason="US fallback mean-reversion was stable across official/stress windows.",
        ),
        PolicyRule(
            market="US",
            strategy="mean_reversion",
            entry_model="confirmation_next_open",
            universe_groups=("fallback", "dynamic_only"),
            reason="US mean-reversion survived confirmation filter in fallback/dynamic-only groups.",
        ),
        PolicyRule(
            market="US",
            strategy="gap_pullback",
            entry_model="confirmation_next_open",
            universe_groups=("multi_source",),
            reason="US gap-pullback improved materially when failed entry-day structure was filtered.",
        ),
    ),
    "profit_guard_v2": (
        PolicyRule(
            market="US",
            strategy="mean_reversion",
            entry_model="gap_filter",
            universe_groups=("fallback",),
            reason="Strict candidate: PF stayed above 1 across stable/official/post-covid/stress/max windows.",
        ),
        PolicyRule(
            market="US",
            strategy="mean_reversion",
            entry_model="confirmation_next_open",
            universe_groups=("fallback", "dynamic_only"),
            reason="Strict candidate: confirmation entry reduced entry-day failure and stayed above PF 1 across windows.",
        ),
    ),
    "shadow_candidate_v1": (
        PolicyRule(
            market="KR",
            strategy="momentum",
            entry_model="gap_filter",
            universe_groups=("core",),
            reason="Shadow only: strong after 2020 but weak in 2018-2019 and max history.",
        ),
        PolicyRule(
            market="US",
            strategy="gap_pullback",
            entry_model="confirmation_next_open",
            universe_groups=("multi_source",),
            reason="Shadow only: improved official window but failed stress/max robustness.",
        ),
    ),
}


def policy_names() -> tuple[str, ...]:
    return tuple(POLICIES.keys())


def allowed_universe_groups(policy_name: str, *, market: str, strategy: str, entry_model: str) -> tuple[str, ...] | None:
    policy = POLICIES.get(str(policy_name or "none"))
    if policy is None:
        raise ValueError(f"unknown policy: {policy_name}")
    if not policy:
        return None
    groups: list[str] = []
    for rule in policy:
        if (
            rule.market == market.upper()
            and rule.strategy == strategy
            and rule.entry_model == entry_model
        ):
            groups.extend(rule.universe_groups)
    return tuple(dict.fromkeys(groups))


def policy_reason_rows(policy_name: str) -> list[dict]:
    policy = POLICIES.get(str(policy_name or "none"), ())
    return [
        {
            "market": rule.market,
            "strategy": rule.strategy,
            "entry_model": rule.entry_model,
            "universe_groups": ",".join(rule.universe_groups),
            "reason": rule.reason,
        }
        for rule in policy
    ]
