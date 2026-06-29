"""minority_report/consensus.py - analyst consensus engine."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
from logger import get_minority_logger

load_dotenv()
log = get_minority_logger()

MIN_DATA = 10
ANALYST_ROLES = ("bull", "bear", "neutral")

STANCE_SCORE = {
    "AGGRESSIVE": 1.00,
    "MODERATE_BULL": 0.70,
    "MILD_BULL": 0.40,
    "CAUTIOUS": 0.15,
    "NEUTRAL": 0.00,
    "MILD_BEAR": -0.40,
    "CAUTIOUS_BEAR": -0.70,
    "DEFENSIVE": -0.90,
    "HALT": -1.00,
}


def _e(key: str, default: int) -> int:
    return int(os.getenv(key, str(default)))


def _env_float(key: str, default: float) -> float:
    try:
        return float(str(os.getenv(key, str(default))).strip())
    except Exception:
        return float(default)


def _score_to_mode(score: float) -> tuple:
    if score >= 0.85:
        return "AGGRESSIVE", _e("SIZE_AGGRESSIVE", 100)
    if score >= 0.55:
        return "MODERATE_BULL", _e("SIZE_MODERATE_BULL", 80)
    if score >= 0.28:
        return "MILD_BULL", _e("SIZE_MILD_BULL", 50)
    if score >= 0.08:
        return "CAUTIOUS", _e("SIZE_CAUTIOUS", 40)
    if score >= -0.20:
        return "NEUTRAL", _e("SIZE_NEUTRAL", 50)
    if score >= -0.55:
        return "MILD_BEAR", _e("SIZE_MILD_BEAR", 30)
    if score >= -0.80:
        return "CAUTIOUS_BEAR", _e("SIZE_CAUTIOUS_BEAR", 20)
    if score >= -0.95:
        return "DEFENSIVE", _e("SIZE_DEFENSIVE", 10)
    return "HALT", 0


CONSENSUS_MAP = {
    ("bull", "bull", "bull"): {"mode": "AGGRESSIVE", "size": _e("SIZE_AGGRESSIVE", 100), "tp_mult": 1.2},
    ("bull", "bull", "neutral"): {"mode": "MODERATE_BULL", "size": _e("SIZE_MODERATE_BULL", 80), "tp_mult": 1.1},
    ("bear", "bull", "bull"): {"mode": "CAUTIOUS", "size": _e("SIZE_CAUTIOUS", 40), "tp_mult": 1.0},
    ("bull", "neutral", "neutral"): {"mode": "MILD_BULL", "size": _e("SIZE_MILD_BULL", 50), "tp_mult": 1.0},
    ("bear", "bull", "neutral"): {"mode": "NEUTRAL", "size": _e("SIZE_NEUTRAL", 50), "tp_mult": 1.0},
    ("neutral", "neutral", "neutral"): {"mode": "NEUTRAL", "size": _e("SIZE_NEUTRAL", 50), "tp_mult": 1.0},
    ("bear", "neutral", "neutral"): {"mode": "MILD_BEAR", "size": _e("SIZE_MILD_BEAR", 30), "tp_mult": 0.9},
    ("bear", "bear", "neutral"): {"mode": "CAUTIOUS_BEAR", "size": _e("SIZE_CAUTIOUS_BEAR", 20), "tp_mult": 0.8},
    ("bear", "bear", "bull"): {"mode": "DEFENSIVE", "size": _e("SIZE_DEFENSIVE", 10), "tp_mult": 0.8},
    ("bear", "bear", "bear"): {"mode": "HALT", "size": 0, "tp_mult": 0.0},
}


def _cat(stance: str) -> str:
    stance_key = str(stance or "").strip().upper()
    if stance_key == "UNAVAILABLE":
        return "unavailable"
    if stance_key in ("AGGRESSIVE", "MODERATE_BULL", "MILD_BULL", "CAUTIOUS"):
        return "bull"
    if stance_key in ("HALT", "DEFENSIVE", "CAUTIOUS_BEAR", "MILD_BEAR"):
        return "bear"
    return "neutral"


def is_available_judgment(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    stance = str(item.get("stance") or "").strip().upper()
    if stance == "UNAVAILABLE":
        return False
    if item.get("available") is False:
        return False
    if item.get("analyst_unavailable") is True:
        return False
    return stance in STANCE_SCORE


def _availability_meta(judgments: dict) -> dict:
    source = judgments or {}
    available = [role for role in ANALYST_ROLES if is_available_judgment(source.get(role) or {})]
    unavailable = [role for role in ANALYST_ROLES if role not in available]
    count = len(available)
    if count == 3:
        quality = "full_consensus"
    elif count == 2:
        quality = "partial_consensus"
    elif count == 1:
        quality = "partial_consensus_only"
    else:
        quality = "fail_closed"
    return {
        "available_analyst_count": count,
        "available_analyst_roles": available,
        "unavailable_analyst_roles": unavailable,
        "analyst_unavailable_count": len(unavailable),
        "analyst_unavailable_roles": unavailable,
        "quorum_met": count >= 2,
        "consensus_quality": quality,
    }


def _agreement_meta(vote_cats: list, weighted_score: float) -> dict:
    """분석가 입장 일치도(가용성과 별개)와 dead-band wash를 측정하는 읽기전용 메타.

    consensus_quality(가용성 quorum count)는 분열을 못 보므로 — distinct=1(만장일치)과
    distinct=3(완전분열)이 같은 full_consensus — 입장 distinct 기반 agreement_quality를
    별도 산출한다. dead_band_wash는 weighted_score가 NEUTRAL 밴드라 stance 과반과
    어긋난 방향으로 세탁됐는지 표시(측정만, mode/size 변경 없음).
    """
    cats = [c for c in (vote_cats or []) if c]
    n = len(cats)
    distinct = len(set(cats))
    if n == 0:
        agreement = "none"
    elif distinct == 1:
        agreement = "unanimous"
    elif distinct == n:
        agreement = "split"
    else:
        agreement = "contested"
    score_mode, _ = _score_to_mode(weighted_score)
    bear_n = cats.count("bear")
    bull_n = cats.count("bull")
    majority_dir = None
    if bear_n * 2 > n:
        majority_dir = "bear"
    elif bull_n * 2 > n:
        majority_dir = "bull"
    dead_band_wash = bool(score_mode == "NEUTRAL" and majority_dir is not None)
    return {
        "agreement_quality": agreement,
        "stance_distinct_count": distinct,
        "dead_band_wash": dead_band_wash,
        "dead_band_wash_dir": majority_dir if dead_band_wash else None,
    }


def detect_consensus_judgment_desync(judgments: dict, consensus: dict) -> dict:
    """judgments(분석가 stance)와 consensus.mode가 서로 다른 시점 스냅샷으로 섞였는지
    (시점혼합 오염) 감지하는 읽기전용 헬퍼. 판단/주문 변경 없음(측정·플래그만).

    가용 분석가 stance 평균이 함의하는 방향과 저장된 consensus.mode 방향이 정반대
    (bull↔bear)거나 mode 라벨이 taxonomy에 없을 때 desync로 본다. unanimous override·
    minority 발동은 정당하게 방향을 바꾸므로 제외한다. NEUTRAL 경계 1단계 차이는
    dead-band 산물일 수 있어 desync로 보지 않는다(_agreement_meta가 별도 측정).
    """
    result = {"consensus_judgment_desync": False, "consensus_judgment_desync_reason": None}
    if not isinstance(judgments, dict) or not isinstance(consensus, dict):
        return result
    stored_mode = str(consensus.get("mode") or "").strip().upper()
    if not stored_mode:
        return result
    if stored_mode not in STANCE_SCORE:
        result["consensus_judgment_desync"] = True
        result["consensus_judgment_desync_reason"] = "unknown_mode:%s" % stored_mode
        return result
    if consensus.get("unanimous_override_applied") or consensus.get("minority_triggered"):
        return result
    avail = [r for r in ANALYST_ROLES if is_available_judgment(judgments.get(r) or {})]
    if len(avail) < 2:
        return result
    stances = [str((judgments.get(r) or {}).get("stance") or "").strip().upper() for r in avail]
    # 무가중 단순평균은 build_consensus의 가중 mode와 어긋나(역할 weight 편향 시) false-positive를
    # 낸다 → stance '과반 방향'이 stored mode와 정반대(bull↔bear)일 때만 desync로 본다.
    # 분열·약한 신호(과반 없음)는 desync 아님.
    cats = [_cat(s) for s in stances]
    stored_cat = _cat(stored_mode)
    opposite = {"bull": "bear", "bear": "bull"}.get(stored_cat)
    # desync: stored mode 방향을 지지하는 분석가가 한 명도 없고 반대 방향 지지자가 있을 때.
    # (과반 기준은 2-분석가 정족수의 혼합쌍(bull+neutral 등)을 놓쳐 false-negative였다 —
    #  '지지자 전무 + 반대 존재'가 시점혼합의 더 명확한 신호이고 약한 신호의 false-positive도 안 늘린다)
    if opposite and cats.count(stored_cat) == 0 and cats.count(opposite) > 0:
        result["consensus_judgment_desync"] = True
        result["consensus_judgment_desync_reason"] = (
            "dir_opposite:judg_no_%s/has_%s vs cons=%s" % (stored_cat, opposite, stored_cat)
        )
    return result


def _quorum_fail_closed_result(judgments: dict, *, quality: str, vote_cats: list[str]) -> dict:
    meta = _availability_meta(judgments)
    meta["consensus_quality"] = quality
    meta["quorum_met"] = False
    return {
        "mode": "NEUTRAL",
        "size": 0,
        "tp_mult": 1.0,
        "weighted_score": 0.0,
        "weights": {},
        "minority_triggered": False,
        "vote": list(vote_cats),
        "new_buy_permission": "block",
        "new_buy_permission_votes": [],
        "new_buy_permission_votes_by_role": {},
        "max_gross_exposure_pct": 0,
        "max_gross_exposure_pct_by_role": {},
        "analyst_outage_fail_closed": quality == "fail_closed",
        **meta,
    }


def _dir_label_from_name(name: str) -> str:
    name_key = str(name or "").strip().upper()
    score = STANCE_SCORE.get(name_key)
    if score is None:
        return "NA"
    cat = _cat(name_key)
    if cat == "bull":
        return "UP"
    if cat == "bear":
        return "DOWN"
    return "FLAT"


def _dir_label_from_change(value) -> str:
    try:
        v = float(value)
    except Exception:
        return "NA"
    if v != v:
        return "NA"
    if v > 0.15:
        return "UP"
    if v < -0.15:
        return "DOWN"
    return "FLAT"


def _analyst_new_buy_constraints(judgment_items: list[dict], roles=None) -> dict:
    permissions: list[str] = []
    caps: list[int] = []
    permission_by_role: dict[str, str] = {}
    cap_by_role: dict[str, int] = {}
    role_names = list(roles or [])
    for idx, item in enumerate(judgment_items):
        if not is_available_judgment(item or {}):
            continue
        role = role_names[idx] if idx < len(role_names) else str(idx)
        permission = str(item.get("new_buy_permission", "") or "").strip().lower()
        if permission in {"allow", "selective", "block"}:
            permissions.append(permission)
            permission_by_role[role] = permission
        try:
            cap = int(float(item.get("max_gross_exposure_pct", 0) or 0))
        except Exception:
            cap = 0
        if cap > 0:
            normalized_cap = max(0, min(100, cap))
            caps.append(normalized_cap)
            cap_by_role[role] = normalized_cap
    if "block" in permissions:
        resolved_permission = "block"
    elif permissions and all(p == "allow" for p in permissions):
        resolved_permission = "allow"
    else:
        resolved_permission = "selective"
    return {
        "new_buy_permission": resolved_permission,
        "new_buy_permission_votes": permissions,
        "new_buy_permission_votes_by_role": permission_by_role,
        "max_gross_exposure_pct": min(caps) if caps else 0,
        "max_gross_exposure_pct_by_role": cap_by_role,
    }


MODE_NEW_BUY_POLICY = {
    "MILD_BEAR": {"permission": "selective", "max_gross_exposure_pct": 30},
    "CAUTIOUS_BEAR": {"permission": "selective", "max_gross_exposure_pct": 15},
}


def _apply_mode_new_buy_policy(consensus: dict) -> dict:
    result = dict(consensus or {})
    mode = str(result.get("mode") or "").strip().upper()
    policy = MODE_NEW_BUY_POLICY.get(mode)
    if not policy:
        return result
    try:
        available_count = int(result.get("available_analyst_count", 0) or 0)
    except Exception:
        available_count = 0
    quality = str(result.get("consensus_quality") or "").strip()
    quorum_met = bool(result.get("quorum_met")) and available_count >= 2
    if not quorum_met or quality in {"fail_closed", "partial_consensus_only"}:
        return result

    permission_before = str(result.get("new_buy_permission", "") or "").strip().lower()
    mode_permission = str(policy.get("permission") or "selective").strip().lower()
    if mode_permission not in {"allow", "selective", "block"}:
        mode_permission = "selective"
    result["mode_new_buy_policy_applied"] = True
    result["mode_new_buy_policy_mode"] = mode
    result["mode_new_buy_policy_reason"] = "risk_off_limited_new_buy"
    result["mode_new_buy_policy_permission_before"] = permission_before
    result["mode_new_buy_policy_permission"] = mode_permission
    result["new_buy_permission_before_mode_policy"] = permission_before
    result["new_buy_permission"] = mode_permission
    result["new_buy_permission_relaxed_by_mode_policy"] = permission_before == "block" and mode_permission != "block"
    result["new_buy_permission_tightened_by_mode_policy"] = permission_before == "allow" and mode_permission != "allow"

    try:
        analyst_cap = int(float(result.get("max_gross_exposure_pct", 0) or 0))
    except Exception:
        analyst_cap = 0
    try:
        mode_cap = int(float(policy.get("max_gross_exposure_pct", 0) or 0))
    except Exception:
        mode_cap = 0
    if mode_cap > 0:
        effective_cap = min(analyst_cap, mode_cap) if analyst_cap > 0 else mode_cap
        result["max_gross_exposure_pct_before_mode_policy"] = analyst_cap
        result["mode_max_gross_exposure_pct"] = mode_cap
        result["mode_new_buy_policy_cap"] = mode_cap
        result["max_gross_exposure_pct"] = max(0, min(100, effective_cap))
    return result


def apply_unanimous_override(judgments: dict, consensus: dict) -> dict:
    if not judgments or not consensus:
        return dict(consensus or {})

    availability = _availability_meta(judgments)
    if availability["available_analyst_count"] < 3:
        result = dict(consensus)
        result.setdefault("unanimous_direction", None)
        result.setdefault("unanimous_override_applied", False)
        result.update({k: v for k, v in availability.items() if k not in result})
        return result

    bull = judgments.get("bull") or {}
    bear = judgments.get("bear") or {}
    neut = judgments.get("neutral") or {}
    vote_cats = [_cat(bull.get("stance")), _cat(bear.get("stance")), _cat(neut.get("stance"))]
    if len(set(vote_cats)) != 1:
        result = dict(consensus)
        result.setdefault("unanimous_direction", None)
        result.setdefault("unanimous_override_applied", False)
        return result

    unanimous_cat = vote_cats[0]
    current_cat = _cat(str(consensus.get("mode") or "NEUTRAL"))
    result = dict(consensus)
    result["unanimous_direction"] = unanimous_cat
    result["unanimous_override_applied"] = False
    if unanimous_cat == current_cat:
        return result

    avg_score = (
        STANCE_SCORE.get(bull.get("stance"), 0.0)
        + STANCE_SCORE.get(bear.get("stance"), 0.0)
        + STANCE_SCORE.get(neut.get("stance"), 0.0)
    ) / 3.0
    floor_mode, floor_size = _score_to_mode(avg_score)
    prev_mode = result.get("mode")
    prev_size = result.get("size")
    result["pre_unanimous_override_mode"] = prev_mode
    result["pre_unanimous_override_size"] = prev_size
    result["mode"] = floor_mode
    result["size"] = floor_size
    result["unanimous_override_applied"] = True
    if unanimous_cat == "bear" and float(result.get("tp_mult", 1.0) or 1.0) > 0.8:
        result["tp_mult"] = 0.8
    log.warning(
        f"[unanimous override] {prev_mode}->{floor_mode} "
        f"dir={unanimous_cat} size={prev_size}->{floor_size}"
    )
    return result


def build_judgment_eval(judgments: dict, consensus: dict, market_change) -> dict:
    judgments = judgments or {}
    availability = {
        role: is_available_judgment(judgments.get(role) or {})
        for role in ANALYST_ROLES
    }
    analyst_stances = {role: (judgments.get(role) or {}).get("stance") for role in ANALYST_ROLES}
    analyst_dirs = {
        role: (_dir_label_from_name(stance) if availability.get(role) else "NA")
        for role, stance in analyst_stances.items()
    }
    actual_dir = _dir_label_from_change(market_change)
    consensus_dir = _dir_label_from_name((consensus or {}).get("mode"))
    analyst_hits = {
        role: (actual_dir != "NA" and availability.get(role) and analyst_dirs[role] == actual_dir)
        for role in analyst_dirs
    }
    consensus_hit = actual_dir != "NA" and consensus_dir == actual_dir
    available_vote_cats = [
        _cat((judgments.get(role) or {}).get("stance"))
        for role in ANALYST_ROLES
        if availability.get(role)
    ]
    unanimous_cat = available_vote_cats[0] if len(available_vote_cats) == 3 and len(set(available_vote_cats)) == 1 else None
    unanimous_dir = {"bull": "UP", "bear": "DOWN", "neutral": "FLAT", None: None}[unanimous_cat]
    unanimous_mismatch = bool(unanimous_cat and unanimous_dir != consensus_dir)
    return {
        "actual_dir": actual_dir,
        "consensus_dir": consensus_dir,
        "consensus_hit": consensus_hit,
        "analyst_dirs": analyst_dirs,
        "analyst_hits": analyst_hits,
        "analyst_available": availability,
        "unavailable_analyst_roles": [role for role, available in availability.items() if not available],
        "best_analyst_hit": any(analyst_hits.values()),
        "best_analyst_outperformed_consensus": any(analyst_hits.values()) and not consensus_hit,
        "unanimous_direction": unanimous_cat,
        "unanimous_consensus_mismatch": unanimous_mismatch,
        "unanimous_actual_match": bool(unanimous_cat and actual_dir != "NA" and unanimous_dir == actual_dir),
    }


TRIGGER_WORDS_KR = ["공시", "급락", "세력", "서킷", "이탈", "장중 -"]
TRIGGER_WORDS_US = ["halt", "circuit", "crash", "sec", "fraud", "bankrupt", "plunge"]


def _get_weights(market: str) -> dict:
    try:
        from claude_memory import brain as BrainDB

        brain = BrainDB.load()
        perf = brain["markets"][market]["analyst_performance"]
        weights = {}
        for atype in ANALYST_ROLES:
            total = perf[atype]["total"]
            rate = perf[atype]["rate"]
            r30 = perf[atype]["recent_30d"]
            r7 = perf[atype]["recent_7d"]
            r30_n = r30["total"]
            r7_n = r7["total"]
            if r7_n >= 5 and r30_n >= 5:
                blended = rate * 0.30 + r30["rate"] * 0.35 + r7["rate"] * 0.35
            else:
                if r30_n >= 20:
                    blend_w = 0.60
                elif r30_n >= 10:
                    blend_w = 0.45
                elif r30_n >= 5:
                    blend_w = 0.30
                else:
                    blend_w = 0.0
                blended = rate * (1 - blend_w) + r30["rate"] * blend_w if blend_w > 0 else rate
            weights[atype] = max(0.1, min(0.8, blended)) if total >= MIN_DATA else None
        if any(v is None for v in weights.values()):
            log.debug("weights: insufficient data -> equal")
            return {"bull": 1.0, "bear": 1.0, "neutral": 1.0}
        total_w = sum(weights.values())
        for key in weights:
            weights[key] = weights[key] / total_w * 3
        log.debug(
            f"weights: Bull={weights['bull']:.2f} "
            f"Bear={weights['bear']:.2f} Neutral={weights['neutral']:.2f}"
        )
        return weights
    except Exception as e:
        log.warning(f"weights load failed: {e} -> equal")
        return {"bull": 1.0, "bear": 1.0, "neutral": 1.0}


def build_consensus(judgments: dict, check_minority: bool = True,
                    market: str = "KR") -> dict:
    judgments = judgments or {}
    availability = _availability_meta(judgments)
    available_roles = list(availability["available_analyst_roles"])
    valid_items = [(role, judgments.get(role) or {}) for role in available_roles]
    vote_cats = [_cat(item.get("stance")) for _, item in valid_items]
    available_count = int(availability["available_analyst_count"])

    if available_count == 0:
        result = _quorum_fail_closed_result(judgments, quality="fail_closed", vote_cats=vote_cats)
        log.warning(f"[analyst quorum] {market} fail_closed unavailable={availability['unavailable_analyst_roles']}")
        return result
    if available_count == 1:
        result = _quorum_fail_closed_result(judgments, quality="partial_consensus_only", vote_cats=vote_cats)
        log.warning(
            f"[analyst quorum] {market} partial_consensus_only "
            f"available={available_roles} unavailable={availability['unavailable_analyst_roles']}"
        )
        return result

    bear = judgments.get("bear") or {}
    minority_triggered = False
    if check_minority and is_available_judgment(bear):
        bear_reason = str(bear.get("key_reason", "")).lower()
        try:
            bear_conf = float(bear.get("confidence", 0) or 0)
        except Exception:
            bear_conf = 0.0
        trigger_words = TRIGGER_WORDS_US if market == "US" else TRIGGER_WORDS_KR
        if any(w in bear_reason for w in trigger_words) and bear_conf > 0.7:
            minority_triggered = True
            log.warning(f"[minority rule] triggered [{market}]: {bear_reason[:60]}")

    weights = _get_weights(market)
    scores = {
        role: STANCE_SCORE.get(str((judgments.get(role) or {}).get("stance") or "").strip().upper(), 0.0)
        for role in available_roles
    }
    weight_sum = sum(float(weights.get(role, 1.0) or 1.0) for role in available_roles)
    weighted_score = (
        sum(scores[role] * float(weights.get(role, 1.0) or 1.0) for role in available_roles) / weight_sum
        if weight_sum > 0 else 0.0
    )
    mode, size = _score_to_mode(weighted_score)

    confidences = []
    for _, item in valid_items:
        try:
            confidences.append(max(0.0, min(1.0, float(item.get("confidence", 0.5) or 0.5))))
        except Exception:
            confidences.append(0.5)
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    size = max(0, min(100, int(size * (0.7 + avg_conf * 0.3))))

    analyst_sizes = []
    for _, item in valid_items:
        if item.get("suggested_size_pct") is None:
            continue
        try:
            suggested = max(0.0, min(100.0, float(item.get("suggested_size_pct") or 0.0)))
            conf = max(0.0, min(1.0, float(item.get("confidence", 0.5) or 0.5)))
            analyst_sizes.append((suggested, conf))
        except Exception:
            continue
    if analyst_sizes:
        w_sum = sum(w for _, w in analyst_sizes)
        avg_sug = sum(s * w for s, w in analyst_sizes) / w_sum if w_sum else None
        if avg_sug is not None:
            size = max(0, min(100, int(size * 0.5 + avg_sug * 0.5)))

    if available_count == 3:
        n_unique = len(set(vote_cats))
        if n_unique == 1:
            size = max(0, min(100, int(size * 1.3)))
        elif n_unique == 3:
            size = max(0, min(100, int(size * 0.75)))
        else:
            size = max(0, min(100, int(size * 0.85)))

    cats = tuple(sorted(vote_cats))
    tp_mult = CONSENSUS_MAP.get(cats, {}).get("tp_mult", 1.0)
    if minority_triggered and STANCE_SCORE.get(mode, 0) > STANCE_SCORE["DEFENSIVE"]:
        mode = "DEFENSIVE"
        size = max(10, size // 2)
        tp_mult = 0.8

    result = {
        "mode": mode,
        "size": size,
        "tp_mult": tp_mult,
        "weighted_score": round(weighted_score, 3),
        "weights": {role: round(float(weights.get(role, 1.0) or 1.0), 2) for role in available_roles},
        "minority_triggered": minority_triggered,
        "vote": list(cats),
        **availability,
        **_agreement_meta(vote_cats, weighted_score),
    }
    result = apply_unanimous_override(judgments, result)
    new_buy_constraints = _analyst_new_buy_constraints(
        [item for _, item in valid_items],
        [role for role, _ in valid_items],
    )
    result.update(new_buy_constraints)
    result = _apply_mode_new_buy_policy(result)
    if result.get("new_buy_permission") == "block":
        result["size_before_new_buy_block"] = result.get("size", 0)
        result["size"] = 0
    elif int(result.get("max_gross_exposure_pct", 0) or 0) > 0:
        result["size_before_max_gross_cap"] = result.get("size", 0)
        result["size"] = min(
            int(result.get("size", 0) or 0),
            int(result.get("max_gross_exposure_pct", 0) or 0),
        )
    if available_count == 2 and int(result.get("size", 0) or 0) > 0:
        partial_mult = max(0.0, min(1.0, _env_float("ANALYST_PARTIAL_CONSENSUS_SIZE_MULT", 0.75)))
        result["size_before_partial_consensus_penalty"] = result.get("size", 0)
        result["partial_consensus_size_mult"] = partial_mult
        result["size"] = max(0, min(100, int(result.get("size", 0) * partial_mult)))

    log.info(
        f"consensus: {result['mode']} size={result['size']}% "
        f"score={weighted_score:+.3f} weights={result.get('weights')} "
        f"minority={minority_triggered} quality={result.get('consensus_quality')}"
    )
    return result
