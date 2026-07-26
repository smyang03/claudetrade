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


def test_headless_launcher_waits_for_broker_scheduler_writer_authority() -> None:
    text = (ROOT / "tools" / "start_live_stack_headless.ps1").read_text(encoding="utf-8")

    assert "function Wait-BrokerTruthSchedulerReady" in text
    assert "broker_truth_scheduler.lock.json" in text
    assert "broker_truth_scheduler_heartbeat.json" in text
    assert "[int]$lock.pid -eq $ProcessId" in text
    assert "[int]$heartbeat.pid -eq $ProcessId" in text
    assert "Wait-BrokerTruthSchedulerReady -ProcessId" in text


def test_headless_role_repair_does_not_race_running_broker_scheduler() -> None:
    text = (ROOT / "tools" / "start_live_stack_headless.ps1").read_text(encoding="utf-8")

    assert "$existingBotRunning" in text
    assert "reuse the running scheduler for role repair" in text
    assert "if ($existingBotRunning)" in text


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
    assert "--ensure-bot" in ps1
    # 콘솔 입력을 요구하는 대기(ping 지연 해킹·nobreak 없는 timeout)가 없어야 한다
    assert "ping 127.0.0.1" not in ps1
    assert "Read-Host" not in ps1


def test_headless_launcher_never_starts_bot_without_guardian_preflight() -> None:
    text = (ROOT / "tools" / "start_live_stack_headless.ps1").read_text(encoding="utf-8")

    assert 'if ($role.Name -eq "trading_bot")' in text
    # 런처는 봇을 직접 띄우지 않고 guardian에 위임한다 — guardian이 preflight를 돌려
    # BLOCK_START면 기동하지 않는다. (경로는 "tools\live_guardian.py" 형태다.)
    assert 'live_guardian.py" "--mode" "live" "--ensure-bot"' in text


def test_headless_live_stack_launcher_covers_all_runtime_roles() -> None:
    text = (ROOT / "tools" / "start_live_stack_headless.ps1").read_text(encoding="utf-8")

    assert "[switch]$DryRun" in text
    assert "Start-Process" in text
    assert "-WindowStyle Hidden" in text
    assert "OPENBLAS_NUM_THREADS" in text
    assert '$env:CLAUDETRADE_RUNTIME_DIR = $Root' in text
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
    """watchdog은 hidden VBS 래퍼를 거쳐 등록된다(2026-07-25 콘솔 깜빡임 제거).

    이전에는 bat이 start_live_stack_headless.ps1을 직접 등록했다. 지금은
    wscript.exe → watchdog_hidden.vbs → powershell(window-style 0) → ps1 순으로
    한 단계 간접이 생겼다. 그래서 등록 문자열만 보지 않고, 그 간접이 실제로
    headless ps1까지 도달하는지 VBS 내용까지 따라가 확인한다 — 래퍼가 엉뚱한
    스크립트를 가리키면 watchdog은 등록돼 있는데 스택은 안 살아난다.
    """
    text = (ROOT / "register_tasks.bat").read_text(encoding="utf-8")

    assert 'schtasks /delete /tn "claudetrade_kr_update"' in text
    assert 'schtasks /create /tn "claudetrade_live_stack_watchdog"' in text
    assert "/sc minute /mo 5" in text

    # 등록 대상이 hidden 래퍼인지
    assert "wscript.exe" in text
    assert "watchdog_hidden.vbs" in text

    # 래퍼가 실제로 headless 기동 스크립트를 부르는지 (간접 체인 종점 확인)
    vbs = (ROOT / "tools" / "watchdog_hidden.vbs").read_text(encoding="utf-8")
    assert "start_live_stack_headless.ps1" in vbs
    assert "powershell.exe" in vbs
    # window-style 0 = 숨김. 이 인자가 빠지면 5분마다 콘솔이 깜빡인다.
    assert ", 0," in vbs
