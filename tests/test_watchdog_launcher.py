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


def test_live_stack_refreshes_broker_truth_before_start() -> None:
    """bat은 스택 기동 전에 broker truth 스냅샷을 선갱신해야 한다."""
    text = (ROOT / "start_live_stack.bat").read_text(encoding="utf-8")

    assert r"python tools\broker_truth_scheduler.py --mode live --markets KR,US --once --force --ttl-sec 180 --json" in text


def test_broker_truth_scheduler_loop_lives_in_headless_launcher() -> None:
    """--loop 스케줄러는 headless 런처가 띄운다(bat이 아니다)."""
    text = (ROOT / "tools" / "start_live_stack_headless.ps1").read_text(encoding="utf-8")

    assert "broker_truth_scheduler.py" in text
    for flag in ("--loop", "--refresh-interval-min", "--failure-retry-min", "--ttl-sec", "--no-refresh-on-start"):
        assert flag in text


def test_live_stack_bat_must_not_launch_its_own_stack() -> None:
    """★중복 봇 사고(2026-07-13) 재발 방지.

    bat이 wt 탭으로 자체 스택을 띄우면 headless watchdog이 띄운 스택과 겹쳐
    라이브 봇이 2개가 된다(텔레그램 getUpdates 409 충돌로 실측). bat은 정지만 하고
    기동은 watchdog(headless ps1)에 위임해야 한다.
    """
    text = (ROOT / "start_live_stack.bat").read_text(encoding="utf-8")

    # 자체 프로세스 기동 금지 — wt 탭은 로그 tail(읽기 전용)만 허용된다.
    for launched in (
        r"python trading_bot.py --live",
        r"python dashboard\dashboard_server.py",
        r"python tools\live_guardian.py",
        r"python tools\preopen_scheduler.py",
        r"python tools\run_counterfactual_pipeline.py",
    ):
        assert launched not in text, f"bat이 자체 스택을 띄운다 — 중복 봇 위험: {launched}"

    # 정지는 cwd 기준 도구로, 기동은 headless 런처에 위임
    assert r"python tools\stop_live_stack.py" in text
    assert "start_live_stack_headless.ps1" in text
    # 정지~기동 사이에 watchdog이 되살리지 못하게 잠근다
    assert "/disable" in text and "/enable" in text


def test_guardian_starts_headless_without_console_input() -> None:
    """guardian은 headless 런처가 창 없이 띄운다 — 콘솔 입력 대기가 없어야 한다."""
    ps1 = (ROOT / "tools" / "start_live_stack_headless.ps1").read_text(encoding="utf-8")

    assert "live_guardian.py" in ps1
    assert "-WindowStyle Hidden" in ps1
    assert "--watch" in ps1
    # 콘솔 입력을 요구하는 대기(ping 지연 해킹·nobreak 없는 timeout)가 없어야 한다
    assert "ping 127.0.0.1" not in ps1
    assert "Read-Host" not in ps1


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
