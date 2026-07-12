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
    assert "--refresh-interval-min 2" in text
    assert "--failure-retry-min 2" in text
    assert "--ttl-sec 180" in text
    assert "--no-refresh-on-start" in text


def test_live_stack_guardian_delay_does_not_require_console_input() -> None:
    text = (ROOT / "start_live_stack.bat").read_text(encoding="utf-8")
    guardian_line = next(
        line for line in text.splitlines() if 'new-tab --title "live_guardian"' in line
    )

    assert "timeout /t" not in guardian_line
    assert "timeout /t" not in text
    assert "ping 127.0.0.1 -n 46 >nul" in guardian_line
    assert r"python tools\live_guardian.py --mode live --watch" in guardian_line


def test_headless_live_stack_launcher_covers_all_runtime_roles() -> None:
    text = (ROOT / "tools" / "start_live_stack_headless.ps1").read_text(encoding="utf-8")

    assert "[switch]$DryRun" in text
    assert "Start-Process" in text
    assert "-WindowStyle Hidden" in text
    assert "OPENBLAS_NUM_THREADS" in text
    assert "-RedirectStandardOutput" in text
    assert "-RedirectStandardError" in text
    for command in (
        "trading_bot.py",
        "dashboard_server.py",
        "live_guardian.py",
        "broker_truth_scheduler.py",
        "preopen_scheduler.py",
        "run_counterfactual_pipeline.py",
        "integrity_check.py",
    ):
        assert command in text


def test_registered_tasks_include_headless_live_stack_watchdog() -> None:
    text = (ROOT / "register_tasks.bat").read_text(encoding="utf-8")

    assert 'schtasks /delete /tn "claudetrade_kr_update"' in text
    assert 'schtasks /create /tn "claudetrade_live_stack_watchdog"' in text
    assert "start_live_stack_headless.ps1" in text
    assert "/sc minute /mo 5" in text
