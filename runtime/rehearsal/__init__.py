"""Live operations rehearsal harness.

The rehearsal package runs live-mode semantics behind fixture backends and a
sandboxed runtime root. It must not be imported by production startup paths.
"""

from runtime.rehearsal.context import (
    RehearsalContext,
    RehearsalGuardError,
    assert_repo_live_state_unchanged,
    assert_runtime_objects_sandboxed,
    assert_sandbox_runtime_root,
    create_rehearsal_context,
    install_no_network_guard,
    install_write_guard,
    snapshot_repo_live_state,
)
from runtime.rehearsal.simulation import (
    all_simulation_scenarios,
    run_guarded_simulation,
    run_simulation_case,
    run_simulation_suite,
    simulation_case_for_scenario,
)

__all__ = [
    "RehearsalContext",
    "RehearsalGuardError",
    "assert_repo_live_state_unchanged",
    "assert_runtime_objects_sandboxed",
    "assert_sandbox_runtime_root",
    "create_rehearsal_context",
    "install_no_network_guard",
    "install_write_guard",
    "all_simulation_scenarios",
    "run_guarded_simulation",
    "run_simulation_case",
    "run_simulation_suite",
    "simulation_case_for_scenario",
    "snapshot_repo_live_state",
]
