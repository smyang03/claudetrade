from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_auto_start_launcher_targets_claudetrade_guardian() -> None:
    text = (ROOT / "auto_start_if_missing.bat").read_text(encoding="utf-8")

    assert "YourProgram.exe" not in text
    assert r"C:\Path\To\YourProgram.exe" not in text
    assert 'tasklist /FI "IMAGENAME' not in text
    assert r"tools\live_guardian.py" in text
    assert "--watch" in text
    assert "--mode" in text
    assert "--ensure-bot" in text


def test_live_stack_starts_broker_truth_scheduler() -> None:
    text = (ROOT / "start_live_stack.bat").read_text(encoding="utf-8")

    assert r'tools\broker_truth_scheduler.py" "broker_truth_scheduler' in text
    assert r"python tools\broker_truth_scheduler.py --mode live --markets KR,US --once --force --ttl-sec 180 --json" in text
    assert r"python tools\broker_truth_scheduler.py --mode live --markets KR,US --loop" in text
    assert "--refresh-interval-min 10" in text
    assert "--failure-retry-min 2" in text
    assert "--ttl-sec 180" in text
    assert "--no-refresh-on-start" in text
