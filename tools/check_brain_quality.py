from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / "state"

PLACEHOLDER_MARKERS = (
    "오류로 자동 판정",
    "응답 실패",
    "자동 판정",
    "자동 생성 실패",
)
MOJIBAKE_MARKERS = (
    "\ufffd",
    "?섏",
    "?ㅽ",
    "?좏",
    "?먯",
    "?꾩",
    "?댁",
    "泥",
    "紐",
    "筌",
    "揶",
    "醫",
    "諛",
    "袁",
    "癰",
    "疫",
    "野",
    "瑗",
    "濡",
    "遺",
    "鍮",
)
EMPTY_VALUES = {"", "-", "none", "n/a", "null", "없음", "해당 없음"}


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def _bad_text(value, *, empty_bad: bool = False) -> bool:
    text = str(value or "").strip()
    if empty_bad and text.lower() in EMPTY_VALUES:
        return True
    return any(marker in text for marker in PLACEHOLDER_MARKERS + MOJIBAKE_MARKERS)


def _check_brain(errors: list[str]) -> None:
    brain = _load_json(STATE_DIR / "brain.json")
    for market in ("KR", "US"):
        payload = brain.get("markets", {}).get(market, {})

        for idx, pattern in enumerate(payload.get("issue_patterns", []) or []):
            if _bad_text(pattern.get("description", ""), empty_bad=True):
                errors.append(f"{market}.issue_patterns[{idx}] has empty/corrupt description")
            if _bad_text(pattern.get("type", "")):
                errors.append(f"{market}.issue_patterns[{idx}] has corrupt type")
            if _bad_text(pattern.get("insight", "")):
                errors.append(f"{market}.issue_patterns[{idx}] has corrupt insight")

        for idx, row in enumerate(payload.get("recent_days", []) or []):
            if _bad_text(row.get("key_lesson", "")):
                errors.append(f"{market}.recent_days[{idx}].key_lesson is corrupt")

        for field in ("execution_lessons",):
            for idx, lesson in enumerate(payload.get(field, []) or []):
                if _bad_text(lesson, empty_bad=True):
                    errors.append(f"{market}.{field}[{idx}] is empty/corrupt")

        lessons = payload.get("current_beliefs", {}).get("learned_lessons", []) or []
        for idx, lesson in enumerate(lessons):
            if _bad_text(lesson, empty_bad=True):
                errors.append(f"{market}.current_beliefs.learned_lessons[{idx}] is empty/corrupt")

        for key, item in (payload.get("tuning_patterns") or {}).items():
            if item.get("metric_semantics") != "adjusted_not_accuracy":
                errors.append(f"{market}.tuning_patterns.{key} missing adjusted_not_accuracy semantics")
            if _bad_text(item.get("insight", "")):
                errors.append(f"{market}.tuning_patterns.{key}.insight is corrupt")
            count = int(item.get("count", 0) or 0)
            adjusted = int(item.get("adjusted", item.get("correct", 0)) or 0)
            if adjusted > count:
                errors.append(f"{market}.tuning_patterns.{key}.adjusted exceeds count")


def _check_lesson_candidates(errors: list[str]) -> None:
    path = STATE_DIR / "lesson_candidates.json"
    if not path.exists():
        return
    payload = _load_json(path)
    for market, rows in (payload.get("markets") or {}).items():
        for idx, row in enumerate(rows or []):
            if _bad_text(row.get("summary", ""), empty_bad=True):
                errors.append(f"{market}.lesson_candidates[{idx}].summary is empty/corrupt")


def _check_candidate_source(errors: list[str]) -> None:
    source = ROOT / "trading_bot.py"
    text = source.read_text(encoding="utf-8")
    start = text.find("def _build_lesson_candidates")
    end = text.find("def _load_lesson_candidate_summary", start)
    if start == -1 or end == -1:
        errors.append("trading_bot.py lesson candidate source block not found")
        return
    block = text[start:end]
    for marker in PLACEHOLDER_MARKERS + MOJIBAKE_MARKERS:
        if marker in block:
            errors.append(f"trading_bot.py lesson candidate source contains corrupt marker {marker!r}")
            break


def _check_prompt_summary(errors: list[str]) -> None:
    sys.path.insert(0, str(ROOT))
    from claude_memory import brain

    for market in ("KR", "US"):
        summary = brain.generate_prompt_summary(market)
        for marker in PLACEHOLDER_MARKERS + MOJIBAKE_MARKERS:
            if marker in summary:
                errors.append(f"{market} prompt summary contains corrupt marker {marker!r}")
                break
        if "건 중" in summary and "적중" in summary.split("Tuning patterns", 1)[-1].split("Recent 5 sessions", 1)[0]:
            errors.append(f"{market} tuning summary still presents adjustment count as hit accuracy")


def main() -> int:
    errors: list[str] = []
    _check_brain(errors)
    _check_lesson_candidates(errors)
    _check_candidate_source(errors)
    _check_prompt_summary(errors)

    if errors:
        print("Brain quality check failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print("Brain quality check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
