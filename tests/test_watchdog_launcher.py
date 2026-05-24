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
