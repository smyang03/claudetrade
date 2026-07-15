from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "restart_live_stack_safely.ps1"


def test_safe_restart_reuses_only_fresh_snapshot_from_running_scheduler() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    assert "function Get-RunningBrokerTruthScheduler" in text
    assert "function Invoke-BrokerTruthChecked" in text
    assert '$ErrorActionPreference = "Continue"' in text
    assert "$refreshExitCode = $LASTEXITCODE" in text
    assert "$scheduler.running -and $bothFresh" in text
    assert "[double]$inventory.age_sec -le 90.0" in text
    assert "Invoke-BrokerTruthChecked" in text


def test_safe_restart_still_checkpoints_before_stop() -> None:
    text = SCRIPT.read_text(encoding="utf-8")

    checkpoint = text.index('"tools\\live_maintenance.py" "backup"')
    stop = text.index('"tools\\stop_live_stack.py"', checkpoint)
    assert checkpoint < stop
