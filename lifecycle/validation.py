from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
import json

from decision.registry import DecisionRegistry
from lifecycle.event_store import EventStore
from lifecycle.models import LifecycleEventType
from lifecycle.quality import evaluate_decision_quality, live_clean_learning_allowed
from review.daily_review import DailyReviewWriter


@dataclass
class PhaseIssue:
    severity: str
    code: str
    message: str


@dataclass
class PhaseResult:
    phase: int
    ok: bool = True
    checks: list[str] = field(default_factory=list)
    issues: list[PhaseIssue] = field(default_factory=list)

    def fail(self, code: str, message: str) -> None:
        self.ok = False
        self.issues.append(PhaseIssue("error", code, message))

    def warn(self, code: str, message: str) -> None:
        self.issues.append(PhaseIssue("warning", code, message))


class V2PhaseValidator:
    def __init__(self, root: str | Path | None = None):
        self.root = Path(root) if root else Path(__file__).resolve().parent.parent

    def validate(self, target_phase: int, *, qa: bool = False, simulation_report: bool = False) -> dict[str, Any]:
        if target_phase < 1 or target_phase > 6:
            raise ValueError("target_phase must be 1..6")
        phase_results = [self._validate_phase(phase) for phase in range(1, target_phase + 1)]
        if qa:
            phase_results.append(self._validate_final_qa(simulation_report=simulation_report))
        ok = all(result.ok for result in phase_results)
        report = {
            "target_phase": target_phase,
            "ok": ok,
            "qa": qa,
            "simulation_report": simulation_report,
            "phases": [_result_to_dict(result) for result in phase_results],
        }
        try:
            EventStore().record_phase_validation(
                phase=target_phase,
                ok=ok,
                qa=qa,
                simulation_report=simulation_report,
                report=report,
            )
        except Exception:
            pass
        return report

    def _validate_phase(self, phase: int) -> PhaseResult:
        if phase == 1:
            return self._validate_phase1()
        if phase == 2:
            return self._validate_phase2()
        if phase == 3:
            return self._validate_phase3()
        if phase == 4:
            return self._validate_phase4()
        if phase == 5:
            return self._validate_phase5()
        if phase == 6:
            return self._validate_phase6()
        result = PhaseResult(phase=phase)
        result.fail(
            f"PHASE_{phase}_NOT_IMPLEMENTED",
            f"Phase {phase} gate is intentionally failing until its production code is implemented.",
        )
        return result

    def _validate_phase5(self) -> PhaseResult:
        result = PhaseResult(phase=5)
        required_paths = [
            "runtime/risk_profile.py",
            "runtime/risk_factory.py",
            "runtime/v2_lifecycle_runtime.py",
            "tools/v2_archive_guard.py",
            "tools/v2_live_smoke.py",
            "research/audit_lab/__init__.py",
        ]
        for rel in required_paths:
            if not (self.root / rel).exists():
                result.fail("MISSING_FILE", f"Missing required Phase 5 file: {rel}")
            else:
                result.checks.append(f"file:{rel}")

        try:
            from config.v2 import DEFAULT_V2_CONFIG
            from runtime.risk_factory import create_risk_manager
            from runtime.risk_profile import build_risk_profile
            from runtime.v2_lifecycle_runtime import V2LifecycleRuntime, v2_close_reason
            from tools.v2_archive_guard import scan_archive_imports
            from tools.v2_live_smoke import run_live_smoke
        except Exception as exc:
            result.fail("IMPORT_FAILED", f"Phase 5 module import failed: {exc}")
            return result

        kr_live = build_risk_profile("KR", "live", usd_krw=1400, config=DEFAULT_V2_CONFIG)
        us_paper = build_risk_profile("US", "paper", usd_krw=1400, config=DEFAULT_V2_CONFIG)
        if (
            kr_live.fixed_order_krw != DEFAULT_V2_CONFIG.kr_fixed_order_krw
            or kr_live.max_positions != DEFAULT_V2_CONFIG.kr_max_positions
        ):
            result.fail("KR_RISK_PROFILE", f"Unexpected KR risk profile: {kr_live}")
        expected_us_fixed = DEFAULT_V2_CONFIG.us_fixed_order_krw or DEFAULT_V2_CONFIG.us_fixed_order_usd * 1_400
        expected_us_min = DEFAULT_V2_CONFIG.us_min_order_krw or DEFAULT_V2_CONFIG.us_min_order_usd * 1_400
        if us_paper.fixed_order_krw != expected_us_fixed or us_paper.min_order_krw != expected_us_min:
            result.fail("US_RISK_PROFILE", f"Unexpected US risk profile: {us_paper}")
        result.checks.append("risk_profile:kr_us_live_paper")

        manager = create_risk_manager(kr_live, init_cash_krw=1_000_000)
        if (
            getattr(manager, "market", "") != "KR"
            or float(getattr(manager, "max_order_krw", 0) or 0) != kr_live.fixed_order_krw
        ):
            result.fail("RISK_MANAGER_FACTORY", "RiskManager factory did not apply V2 KR profile")
        if not getattr(manager, "v2_risk_profile", None):
            result.fail("RISK_MANAGER_PROFILE", "RiskManager is missing attached V2 profile")
        result.checks.append("risk_factory:risk_manager_profile")

        if v2_close_reason("stop_loss") != "CLOSED_HARD_STOP" or v2_close_reason("broker_sync") != "CLOSED_BROKER_SYNC":
            result.fail("V2_CLOSE_REASON", "V2 close reason mapping is incorrect")
        if not hasattr(V2LifecycleRuntime, "record_event") or not hasattr(V2LifecycleRuntime, "safety_decision"):
            result.fail("V2_RUNTIME_FACADE", "V2LifecycleRuntime facade is missing required methods")
        result.checks.append("v2_runtime:facade_available")

        findings = scan_archive_imports(self.root)
        if findings:
            rendered = ", ".join(f"{item.path}:{item.line}:{item.target}" for item in findings[:5])
            result.fail("ARCHIVE_IMPORT", f"Live code imports archive modules: {rendered}")
        result.checks.append("archive_guard:ast_scan")

        for market in ("KR", "US"):
            smoke = run_live_smoke(market=market, runtime_mode="live", root=self.root, usd_krw=1400)
            if not smoke.get("ok"):
                result.fail("LIVE_SMOKE", f"{market} live smoke failed: {smoke}")
        result.checks.append("live_smoke:kr_us_no_broker")

        trading_bot_text = (self.root / "trading_bot.py").read_text(encoding="utf-8", errors="ignore")
        if "V2LifecycleRuntime" not in trading_bot_text or "self.v2 =" not in trading_bot_text:
            result.fail("TRADING_BOT_V2_FACADE", "trading_bot.py is missing V2 lifecycle facade wiring")
        direct_v2_markers = ("_V2BuildRiskProfile", "_V2SafetyContext", "_V2FixedSizer", "_V2DecisionRegistry")
        leaked = [marker for marker in direct_v2_markers if marker in trading_bot_text]
        if leaked:
            result.fail("TRADING_BOT_V2_DIRECT_IMPORT", f"trading_bot.py still imports direct V2 internals: {leaked}")
        result.checks.append("trading_bot:v2_runtime_facade_wiring")
        return result

    def _validate_phase6(self) -> PhaseResult:
        result = PhaseResult(phase=6)
        required_paths = [
            "interface/v2_ops_summary.py",
            "interface/v2_telegram.py",
            "dashboard/dashboard_server.py",
            "telegram_commander.py",
        ]
        for rel in required_paths:
            if not (self.root / rel).exists():
                result.fail("MISSING_FILE", f"Missing required Phase 6 file: {rel}")
            else:
                result.checks.append(f"file:{rel}")

        try:
            from interface.v2_ops_summary import V2_DASHBOARD_TABS, V2_TELEGRAM_COMMANDS, build_v2_ops_summary
            from interface.v2_telegram import handle_v2_command
            from lifecycle.models import LifecycleEvent
        except Exception as exc:
            result.fail("IMPORT_FAILED", f"Phase 6 module import failed: {exc}")
            return result

        expected_tabs = [
            "Account",
            "System Health",
            "Claude Picks",
            "B플랜 실시간",
            "Lifecycle",
            "Positions",
            "Brain",
            "Daily Review",
        ]
        if list(V2_DASHBOARD_TABS) != expected_tabs:
            result.fail("DASHBOARD_TABS", f"Unexpected V2 dashboard tabs: {V2_DASHBOARD_TABS}")
        for command in (
            "/status", "/health", "/picks", "/positions", "/errors", "/halt", "/resume", "/panic",
            "/brain_pending", "/pathb_status", "/pathb_on", "/pathb_off", "/pathb_kill",
            "/pathb_closeall",
        ):
            if command not in V2_TELEGRAM_COMMANDS:
                result.fail("TELEGRAM_COMMANDS", f"Missing Telegram command: {command}")
        result.checks.append("interface:dashboard_tabs_telegram_commands")

        with TemporaryDirectory() as tmp:
            store = EventStore(Path(tmp) / "events.db")
            decision_id = "dec_phase6"
            store.create_decision(
                decision_id=decision_id,
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_phase6",
            )
            base = {
                "market": "KR",
                "runtime_mode": "live",
                "session_date": "2026-04-26",
                "ticker": "005930",
                "decision_id": decision_id,
                "prompt_version": "v2",
                "brain_snapshot_id": "brain_phase6",
            }
            store.append(LifecycleEvent(event_type="CLAUDE_TRADE_READY", **base))
            store.append(LifecycleEvent(event_type="SAFETY_BLOCKED", reason_code="STALE_MARKET_DATA", **base))
            store.append(LifecycleEvent(event_type="ORDER_UNKNOWN", execution_id="exec_phase6", **base))
            summary = build_v2_ops_summary(
                store=store,
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
            )
            if summary.get("system_health", {}).get("order_unknown_count") != 1:
                result.fail("OPS_ORDER_UNKNOWN", f"Unexpected ops summary: {summary}")
            if len(summary.get("claude_picks") or []) != 1:
                result.fail("OPS_PICKS", f"Claude picks missing from ops summary: {summary}")
            result.checks.append("ops_summary:lifecycle_health_picks")

        class _Risk:
            def __init__(self):
                self.cash = 1_000_000
                self.positions = []
                self.daily_pnl = 0
                self.halted = False
                self.halt_reason = ""

            def equity(self):
                return self.cash

            def daily_return(self):
                return 0.0

        class _Bot:
            def __init__(self):
                self.risk = _Risk()

        bot = _Bot()
        health_text = handle_v2_command("/health", bot)
        if "ORDER_UNKNOWN" not in health_text:
            result.fail("TELEGRAM_HEALTH", f"Unexpected /health response: {health_text}")
        handle_v2_command("/halt", bot)
        if not bot.risk.halted or bot.risk.halt_reason != "manual_halt":
            result.fail("TELEGRAM_HALT", "/halt did not set manual halt")
        handle_v2_command("/resume", bot)
        if bot.risk.halted:
            result.fail("TELEGRAM_RESUME", "/resume did not clear manual halt")
        result.checks.append("telegram:health_halt_resume")

        dashboard_text = (self.root / "dashboard" / "dashboard_server.py").read_text(encoding="utf-8", errors="ignore")
        if "/api/v2/ops" not in dashboard_text or "build_v2_ops_summary" not in dashboard_text:
            result.fail("DASHBOARD_ROUTE", "dashboard_server.py is missing V2 ops route")
        telegram_text = (self.root / "telegram_commander.py").read_text(encoding="utf-8", errors="ignore")
        for command in ("/health", "/picks", "/errors", "/brain_pending", "/halt", "/resume", "/panic"):
            if command not in telegram_text:
                result.fail("TELEGRAM_WIRING", f"telegram_commander.py is missing command wiring: {command}")
        result.checks.append("dashboard_telegram:route_and_command_wiring")
        return result

    def _validate_phase4(self) -> PhaseResult:
        result = PhaseResult(phase=4)
        required_paths = [
            "learning/brain_snapshot.py",
            "learning/approval_queue.py",
            "learning/patterns.py",
            "learning/candidate_builder.py",
            "lifecycle/quality_marker.py",
        ]
        for rel in required_paths:
            if not (self.root / rel).exists():
                result.fail("MISSING_FILE", f"Missing required Phase 4 file: {rel}")
            else:
                result.checks.append(f"file:{rel}")

        try:
            from learning.approval_queue import BrainApprovalQueue
            from learning.brain_snapshot import BrainSnapshotStore
            from learning.candidate_builder import BrainCandidateBuilder
            from learning.patterns import grade_pattern, mark_expired_patterns, prompt_eligible_patterns
            from lifecycle.models import LifecycleEvent
            from lifecycle.quality_marker import DataQualityMarker
        except Exception as exc:
            result.fail("IMPORT_FAILED", f"Phase 4 module import failed: {exc}")
            return result

        with TemporaryDirectory() as tmp:
            snapshot_store = BrainSnapshotStore(Path(tmp) / "snapshots")
            snapshot = snapshot_store.create_snapshot(
                prompt_version="v2",
                market="KR",
                session_date="2026-04-26",
                runtime_mode="live",
                patterns=[{"name": "p1", "sample_count": 30}],
            )
            loaded = snapshot_store.load(snapshot.brain_snapshot_id)
            if loaded.get("brain_hash") != snapshot.brain_hash:
                result.fail("BRAIN_SNAPSHOT_HASH", "Brain snapshot hash did not round-trip")
            result.checks.append("brain_snapshot:filelock_hash")

            if grade_pattern(9) != "observation_only" or grade_pattern(30) != "trusted_candidate" or grade_pattern(50) != "operating_principle_candidate":
                result.fail("PATTERN_GRADE", "Pattern grading thresholds are incorrect")
            expired = mark_expired_patterns([{"sample_count": 12, "last_verified_at": "2026-01-01"}], as_of=datetime.strptime("2026-04-26", "%Y-%m-%d").date())
            if expired[0].get("quality") != "SUSPECT":
                result.fail("PATTERN_EXPIRY", "Pattern expiry did not mark SUSPECT")
            eligible = prompt_eligible_patterns([
                {"grade": "weak_reference", "quality": "CLEAN"},
                {"grade": "trusted_candidate", "quality": "CLEAN"},
                {"grade": "observation_only", "quality": "CLEAN"},
            ])
            if len(eligible) != 2:
                result.fail("PROMPT_ELIGIBLE", "Prompt eligible pattern filtering is incorrect")
            result.checks.append("patterns:grade_expiry_prompt_filter")

            queue = BrainApprovalQueue(Path(tmp) / "approval.jsonl")
            if not queue.submit(candidate={"id": "c1"}, runtime_mode="live", data_quality="CLEAN", forward_complete=True):
                result.fail("APPROVAL_ACCEPT", "Clean live candidate was not queued")
            if queue.submit(candidate={"id": "paper"}, runtime_mode="paper", data_quality="CLEAN", forward_complete=True):
                result.fail("APPROVAL_PAPER_REJECT", "Paper candidate was accepted into live learning queue")
            if len(queue.read_all()) != 1:
                result.fail("APPROVAL_QUEUE_COUNT", "Approval queue count is incorrect")
            result.checks.append("approval_queue:clean_live_only")

            db_path = Path(tmp) / "events.db"
            store = EventStore(db_path)
            decision_id = "dec_phase4"
            store.create_decision(
                decision_id=decision_id,
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id=snapshot.brain_snapshot_id,
            )
            base = {
                "market": "KR",
                "runtime_mode": "live",
                "session_date": "2026-04-26",
                "ticker": "005930",
                "decision_id": decision_id,
                "prompt_version": "v2",
                "brain_snapshot_id": snapshot.brain_snapshot_id,
            }
            store.append(LifecycleEvent(event_type="CLAUDE_TRADE_READY", **base))
            store.append(LifecycleEvent(event_type="ORDER_SENT", execution_id="exec1", **base))
            store.append(LifecycleEvent(event_type="FILLED", execution_id="exec1", position_id="pos1", **base))
            store.append(LifecycleEvent(event_type="CLOSED", execution_id="exec1", position_id="pos1", payload={"pnl_pct": 1.0}, **base))
            store.append(LifecycleEvent(event_type="FORWARD_MEASURED", payload={"forward_3d": 2.0, "benchmark_3d": 1.0}, **base))
            grade = DataQualityMarker(store).mark_decision(decision_id)
            if grade != "CLEAN":
                result.fail("QUALITY_MARKER", f"Expected CLEAN, got {grade}")
            candidate = BrainCandidateBuilder().build_candidate(store.events_for_decision(decision_id))
            if not candidate or candidate.get("data_quality") != "CLEAN" or not candidate.get("learning_allowed"):
                result.fail("CANDIDATE_BUILDER", f"Unexpected candidate: {candidate}")
            result.checks.append("quality_marker:candidate_builder")

        trading_bot_text = (self.root / "trading_bot.py").read_text(encoding="utf-8", errors="ignore")
        runtime_path = self.root / "runtime" / "v2_lifecycle_runtime.py"
        v2_runtime_text = runtime_path.read_text(encoding="utf-8", errors="ignore") if runtime_path.exists() else ""
        phase4_wiring_text = trading_bot_text + "\n" + v2_runtime_text
        if "BrainSnapshotStore" not in phase4_wiring_text or "create_snapshot" not in phase4_wiring_text:
            result.fail("TRADING_BOT_SNAPSHOT", "V2 runtime does not create real brain snapshots")
        result.checks.append("trading_bot:v2_brain_snapshot_facade_wiring")
        return result

    def _validate_phase3(self) -> PhaseResult:
        result = PhaseResult(phase=3)
        required_paths = [
            "performance/decomposition.py",
            "review/daily_review.py",
        ]
        for rel in required_paths:
            if not (self.root / rel).exists():
                result.fail("MISSING_FILE", f"Missing required Phase 3 file: {rel}")
            else:
                result.checks.append(f"file:{rel}")

        try:
            from lifecycle.models import LifecycleEvent
            from performance.decomposition import PerformanceDecomposer, decompose_decision_events
            from review.daily_review import DailyReviewWriter
        except Exception as exc:
            result.fail("IMPORT_FAILED", f"Phase 3 module import failed: {exc}")
            return result

        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "v2_event_store.db"
            store = EventStore(db_path)
            decision_id = "dec_phase3"
            store.create_decision(
                decision_id=decision_id,
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_phase3",
            )
            base = {
                "market": "KR",
                "runtime_mode": "live",
                "session_date": "2026-04-26",
                "ticker": "005930",
                "decision_id": decision_id,
                "prompt_version": "v2",
                "brain_snapshot_id": "brain_phase3",
            }
            store.append(LifecycleEvent(event_type="CLAUDE_TRADE_READY", occurred_at="2026-04-26T00:00:00+00:00", **base))
            store.append(LifecycleEvent(event_type="ORDER_SENT", execution_id="exec1", occurred_at="2026-04-26T00:05:00+00:00", **base))
            store.append(LifecycleEvent(event_type="FILLED", execution_id="exec1", position_id="pos1", occurred_at="2026-04-26T00:10:00+00:00", **base))
            store.append(
                LifecycleEvent(
                    event_type="CLOSED",
                    execution_id="exec1",
                    position_id="pos1",
                    occurred_at="2026-04-26T01:00:00+00:00",
                    payload={"pnl_pct": 2.0, "pnl_krw": 1000, "mfe_pct": 4.0, "mae_pct": -1.0, "claude_cost_krw": 100},
                    **base,
                )
            )
            store.append(
                LifecycleEvent(
                    event_type="FORWARD_MEASURED",
                    occurred_at="2026-04-29T00:00:00+00:00",
                    payload={"forward_1d": 1.0, "benchmark_1d": 0.5, "forward_3d": 5.0, "benchmark_3d": 2.0, "forward_5d": 6.0, "benchmark_5d": 3.0},
                    **base,
                )
            )
            events = store.events_for_decision(decision_id)
            perf = decompose_decision_events(events)
            if perf.selection_alpha["3d"] != 3.0:
                result.fail("SELECTION_ALPHA", f"Expected alpha 3.0, got {perf.selection_alpha['3d']}")
            if perf.entry_delay_minutes != 10.0:
                result.fail("ENTRY_DELAY", f"Expected entry delay 10.0, got {perf.entry_delay_minutes}")
            if perf.exit_efficiency != 0.5:
                result.fail("EXIT_EFFICIENCY", f"Expected exit efficiency 0.5, got {perf.exit_efficiency}")
            if perf.net_pnl_after_claude_krw != 900.0:
                result.fail("NET_AFTER_CLAUDE", f"Expected 900, got {perf.net_pnl_after_claude_krw}")
            result.checks.append("performance:decision_decomposition")

            session_perf = PerformanceDecomposer(store).session_performance(
                session_date="2026-04-26",
                runtime_mode="live",
                market="KR",
            )
            if session_perf["selection_alpha"]["avg_3d"] != 3.0:
                result.fail("SESSION_PERFORMANCE", f"Unexpected session performance: {session_perf}")
            review = DailyReviewWriter(store, output_dir=Path(tmp) / "review").build_summary(
                session_date="2026-04-26",
                runtime_mode="live",
                market="KR",
            )
            if (review.get("performance") or {}).get("net_pnl_after_claude", {}).get("total_krw") != 900.0:
                result.fail("DAILY_REVIEW_PERFORMANCE", "Daily review did not include performance decomposition")
            result.checks.append("daily_review:performance_decomposition")

        return result

    def _validate_phase2(self) -> PhaseResult:
        result = PhaseResult(phase=2)
        required_paths = [
            "execution/sizing.py",
            "execution/safety_gate.py",
            "execution/order_state.py",
            "runtime/rate_limiter.py",
        ]
        for rel in required_paths:
            if not (self.root / rel).exists():
                result.fail("MISSING_FILE", f"Missing required Phase 2 file: {rel}")
            else:
                result.checks.append(f"file:{rel}")

        try:
            from config.v2 import DEFAULT_V2_CONFIG
            from execution.order_state import OrderUnknownEscalator, PartialFillPolicy
            from execution.safety_gate import SafetyContext, SafetyGate
            from execution.sizing import FixedSizer
            from runtime.rate_limiter import V2RateLimiter
        except Exception as exc:
            result.fail("IMPORT_FAILED", f"Phase 2 module import failed: {exc}")
            return result

        sizer = FixedSizer(DEFAULT_V2_CONFIG)
        kr_size = sizer.size(market="KR", price_krw=25_000, cash_krw=1_000_000)
        expected_kr_budget = min(float(DEFAULT_V2_CONFIG.kr_fixed_order_krw), 1_000_000.0)
        if kr_size.budget_krw != expected_kr_budget or kr_size.order_cost_krw != kr_size.qty * 25_000:
            result.fail("FIXED_SIZING_KR", f"Unexpected KR fixed sizing: {kr_size}")
        us_size = sizer.size(market="US", price_krw=35_000, usd_krw=1_400, cash_krw=1_000_000)
        expected_us_budget = float(
            DEFAULT_V2_CONFIG.us_fixed_order_krw or DEFAULT_V2_CONFIG.us_fixed_order_usd * 1_400
        )
        if us_size.budget_krw != expected_us_budget or us_size.order_cost_krw != us_size.qty * 35_000:
            result.fail("FIXED_SIZING_US", f"Unexpected US fixed sizing: {us_size}")
        result.checks.append("fixed_sizing:kr_us")

        gate = SafetyGate(DEFAULT_V2_CONFIG)
        base_ctx = dict(
            market="KR",
            runtime_mode="live",
            ticker="005930",
            price_krw=25_000,
            qty=kr_size.qty,
            order_cost_krw=kr_size.order_cost_krw,
            cash_krw=1_000_000,
            min_order_krw=50_000,
            market_open=True,
        )
        if not gate.evaluate(SafetyContext(**base_ctx)).passed:
            result.fail("SAFETY_BASE", "Base safety context should pass")
        if gate.evaluate(SafetyContext(**{**base_ctx, "pending_orders": [{"market": "KR", "ticker": "005930"}]})).reason_code != "PENDING_ORDER_EXISTS":
            result.fail("SAFETY_PENDING", "Pending order did not block")
        if gate.evaluate(SafetyContext(**{**base_ctx, "stopped_tickers": {"005930"}})).reason_code != "SAME_DAY_REENTRY_AFTER_STOP":
            result.fail("SAFETY_STOP_REENTRY", "Same-day stop re-entry did not block")
        if gate.evaluate(SafetyContext(**{**base_ctx, "order_unknown_blocked": True})).reason_code != "ORDER_UNKNOWN_UNRESOLVED":
            result.fail("SAFETY_ORDER_UNKNOWN", "ORDER_UNKNOWN did not block")
        result.checks.append("safety_gate:standard_reasons")

        with TemporaryDirectory() as tmp:
            escalator = OrderUnknownEscalator(Path(tmp) / "unknown.json")
            first = escalator.record_unknown(market="KR", ticker="005930", execution_id="e1")
            if not first.get("blocked") or first.get("scope") != "ticker":
                result.fail("UNKNOWN_TICKER_BLOCK", f"Unexpected first unknown state: {first}")
            second = escalator.record_unknown(market="KR", ticker="000660", execution_id="e2")
            if not second.get("blocked") or second.get("scope") != "market":
                result.fail("UNKNOWN_MARKET_BLOCK", f"Unexpected second unknown state: {second}")
            escalator.record_unknown(market="US", ticker="NVDA", execution_id="e3")
            escalator.record_unknown(market="US", ticker="MSFT", execution_id="e4")
            global_state = escalator.block_state(market="US", ticker="AAPL")
            if not global_state.get("blocked") or global_state.get("scope") != "global":
                result.fail("UNKNOWN_GLOBAL_BLOCK", f"Unexpected global unknown state: {global_state}")
        result.checks.append("order_unknown:escalation")

        partial = PartialFillPolicy(DEFAULT_V2_CONFIG).apply(
            market="KR",
            original_qty=10,
            newly_filled_qty=4,
        )
        if partial.status != "PARTIAL_FILLED" or partial.remaining_qty != 6 or partial.ttl_sec != 120:
            result.fail("PARTIAL_POLICY", f"Unexpected partial fill policy result: {partial}")
        result.checks.append("partial_fill:ttl_policy")

        limiter = V2RateLimiter(max_calls=2, per_seconds=60)
        if not limiter.allow("KR", 0.0) or not limiter.allow("KR", 1.0) or limiter.allow("KR", 2.0):
            result.fail("RATE_LIMITER", "Rate limiter did not enforce max calls")
        result.checks.append("rate_limiter:window")

        trading_bot_text = (self.root / "trading_bot.py").read_text(encoding="utf-8", errors="ignore")
        runtime_path = self.root / "runtime" / "v2_lifecycle_runtime.py"
        v2_runtime_text = runtime_path.read_text(encoding="utf-8", errors="ignore") if runtime_path.exists() else ""
        phase2_wiring_text = trading_bot_text + "\n" + v2_runtime_text
        for needle in ("safety_decision", "V2_FIXED_SIZING_ENABLED", "PARTIAL_FILLED", "ORDER_UNKNOWN"):
            if needle not in phase2_wiring_text:
                result.fail("TRADING_BOT_WIRING", f"trading_bot.py missing Phase 2 wiring marker: {needle}")
        result.checks.append("trading_bot:v2_phase2_facade_wiring_markers")

        return result

    def _validate_phase1(self) -> PhaseResult:
        result = PhaseResult(phase=1)
        required_paths = [
            "v2.md",
            "config/v2.py",
            "decision/registry.py",
            "lifecycle/event_store.py",
            "lifecycle/models.py",
            "lifecycle/quality.py",
            "review/daily_review.py",
            "tools/v2_phase_gate.py",
        ]
        for rel in required_paths:
            if not (self.root / rel).exists():
                result.fail("MISSING_FILE", f"Missing required V2 file: {rel}")
            else:
                result.checks.append(f"file:{rel}")

        with TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "v2_event_store.db"
            store = EventStore(db_path)
            registry = DecisionRegistry(store)
            decision_id = registry.register_trade_ready(
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                prompt_version="v2",
                brain_snapshot_id="brain_test",
                strategy_hint="momentum",
                timing_style="momentum_timing",
                payload={"source": "phase_gate"},
            )
            if not decision_id:
                result.fail("DECISION_ID_EMPTY", "register_trade_ready did not return decision_id")
            result.checks.append("decision_id:trade_ready")

            registry.record_event(
                event_type=LifecycleEventType.SAFETY_BLOCKED,
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                decision_id=decision_id,
                prompt_version="v2",
                brain_snapshot_id="brain_test",
                reason_code="INSUFFICIENT_CASH",
            )
            registry.record_event(
                event_type=LifecycleEventType.TIMING_EXPIRED,
                market="KR",
                runtime_mode="live",
                session_date="2026-04-26",
                ticker="005930",
                decision_id=decision_id,
                prompt_version="v2",
                brain_snapshot_id="brain_test",
                payload={"forward_measure_required": True},
            )
            result.checks.append("events:safety_blocked,timing_expired")

            reopened = EventStore(db_path)
            events = reopened.events_for_decision(decision_id)
            if len(events) != 3:
                result.fail("EVENT_STORE_RESTART_LOSS", f"Expected 3 events after reopen, got {len(events)}")
            else:
                result.checks.append("event_store:restart_persisted")

            event_types = {event["event_type"] for event in events}
            if "CLAUDE_TRADE_READY" not in event_types:
                result.fail("MISSING_TRADE_READY_EVENT", "CLAUDE_TRADE_READY was not persisted")
            if "TIMING_EXPIRED" not in event_types:
                result.fail("MISSING_TIMING_EXPIRED", "TIMING_EXPIRED was not persisted")

            writer = DailyReviewWriter(reopened, output_dir=Path(tmp) / "review")
            paths = writer.write(session_date="2026-04-26", runtime_mode="live", market="KR")
            if not all(Path(path).exists() for path in paths.values()):
                result.fail("DAILY_REVIEW_NOT_WRITTEN", "Daily review files were not generated")
            else:
                result.checks.append("daily_review:generated")

            quality = evaluate_decision_quality(events)
            if quality.grade.value != "LEGACY_UNKNOWN":
                result.fail("QUALITY_UNEXPECTED", f"Phase 1 timing-expired sample should be LEGACY_UNKNOWN, got {quality.grade.value}")
            if live_clean_learning_allowed(runtime_mode="paper", quality="CLEAN", forward_complete=True):
                result.fail("PAPER_LEARNING_ALLOWED", "Paper data must not be accepted into live learning")
            result.checks.append("quality:paper_live_separation")

        return result

    def _validate_final_qa(self, *, simulation_report: bool) -> PhaseResult:
        result = PhaseResult(phase=99)
        text = (self.root / "v2.md").read_text(encoding="utf-8") if (self.root / "v2.md").exists() else ""
        required_terms = [
            "CLAUDE_TRADE_READY",
            "SAFETY_BLOCKED",
            "TIMING_EXPIRED",
            "ORDER_UNKNOWN",
            "FORWARD_MEASURED",
            "QUALITY_MARKED",
            "paper data is never automatically merged",
        ]
        for term in required_terms:
            if term not in text:
                result.fail("V2_MD_COVERAGE", f"v2.md is missing required term: {term}")
        result.checks.append("qa:v2_md_reread")

        archive_path = self.root / "archive" / "legacy_v1"
        if archive_path.exists():
            for py_path in self.root.rglob("*.py"):
                if "archive" in py_path.parts:
                    continue
                try:
                    body = py_path.read_text(encoding="utf-8", errors="ignore")
                except Exception:
                    continue
                if "archive.legacy_v1" in body or "archive/legacy_v1" in body:
                    result.fail("ARCHIVE_IMPORT", f"Live code imports archive legacy: {py_path}")
        result.checks.append("qa:archive_import_scan")

        if simulation_report:
            report_dir = self.root / "data" / "v2_reports"
            reports = list(report_dir.glob("v2_simulation_report_*.json")) if report_dir.exists() else []
            if not reports:
                result.fail("SIMULATION_REPORT_MISSING", "No V2 simulation report found under data/v2_reports")
            else:
                result.checks.append("qa:simulation_report_present")
        return result


def _result_to_dict(result: PhaseResult) -> dict[str, Any]:
    return {
        "phase": result.phase,
        "ok": result.ok,
        "checks": result.checks,
        "issues": [issue.__dict__ for issue in result.issues],
    }


def render_report(report: dict[str, Any]) -> str:
    lines = [f"V2 phase gate target={report['target_phase']} ok={report['ok']}"]
    for phase in report["phases"]:
        label = "QA" if phase["phase"] == 99 else f"Phase {phase['phase']}"
        status = "PASS" if phase["ok"] else "FAIL"
        lines.append(f"{label}: {status}")
        for check in phase["checks"]:
            lines.append(f"  ok: {check}")
        for issue in phase["issues"]:
            lines.append(f"  {issue['severity']}: {issue['code']} - {issue['message']}")
    return "\n".join(lines)


def report_to_json(report: dict[str, Any]) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)
