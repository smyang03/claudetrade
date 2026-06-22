"""brain 오염 정리 도구 회귀 — KR에 US티커 오염 탐지(2026-06-23 무결성 감사 B)."""
import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "clean_brain_pollution",
    Path(__file__).resolve().parents[1] / "tools" / "clean_brain_pollution.py",
)
cbp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cbp)


def _brain(kr_patterns):
    return {"markets": {"KR": {"issue_patterns": kr_patterns}, "US": {"issue_patterns": []}}}


def test_detects_us_ticker_pollution_in_kr():
    brain = _brain([
        {"description": "SRPT·BRZE·PAYS각2회체결", "count": 1},
        {"description": "삼성전자 momentum 진입 실패", "count": 2},
        {"description": "INTC 관련 노이즈", "insight": "AVGO도", "count": 1},
    ])
    hits = cbp._kr_us_pollution(brain)
    assert {i for i, _ in hits} == {0, 2}


def test_clean_kr_only_keeps_legit_korean_patterns():
    brain = _brain([
        {"description": "코스닥 gap_pullback 손절 군집", "count": 3},
        {"description": "TSLA 중복체결", "count": 1},
    ])
    hits = cbp._kr_us_pollution(brain)
    assert {i for i, _ in hits} == {1}


def test_no_pollution_returns_empty():
    brain = _brain([{"description": "장초반 진입 사이즈 축소", "count": 2}])
    assert cbp._kr_us_pollution(brain) == []
