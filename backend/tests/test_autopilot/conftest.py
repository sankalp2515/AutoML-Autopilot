from types import SimpleNamespace

import pytest

from app.autopilot import engine


@pytest.fixture
def ap_settings():
    return SimpleNamespace(AUTOPILOT_EXPERIMENT_TIMEOUT_S=120, AUTOPILOT_RUNNER="inline",
                           AUTOPILOT_MAX_EXTRACTS_PER_RUN=3)


@pytest.fixture
def run_investigation(tmp_path, ap_settings):
    """Run one scenario end-to-end with the inline runner; returns the state."""
    def _run(scenario_id, seed=7, planner="deterministic", research="auto", tavily=None, nemotron=None,
             max_experiments=4, cancel=None, steps=None):
        cfg = dict(scenario_id=scenario_id, seed=seed, planner=planner, research=research,
                   max_experiments=max_experiments, max_cost_units=24.0, auto_run=True)
        st = engine.new_state(engine.new_run_id(), cfg, ap_settings)
        inv = engine.Investigator(st, str(tmp_path), ap_settings, cancel_requested=cancel,
                                  tavily=tavily, nemotron=nemotron)
        if steps:
            for _ in range(steps):
                inv.step()
        else:
            inv.run_to_end()
        return st, inv
    return _run
