"""Phase 0.2 — significance-gated iteration.

route_after_evaluator must only loop back to feature engineering when the score
change clears BOTH the configured threshold AND the run's measured noise floor
(CV std). A gain smaller than fold-to-fold noise is not real improvement.
"""

from app.agents.orchestrator import route_after_evaluator
from app.config import settings


def _state(**kw):
    base = {
        "status": "running", "iteration": 1, "max_iterations": 3,
        "current_score": 0.0, "prev_score": 0.0, "score_std": 0.0,
    }
    base.update(kw)
    return base


def test_failure_routes_to_exporter():
    assert route_after_evaluator(_state(status="failed")) == "exporter"


def test_improvement_below_noise_floor_stops():
    # Change exceeds the configured threshold but is within CV noise → don't chase it.
    delta = settings.IMPROVEMENT_THRESHOLD + 0.01
    s = _state(prev_score=0.80, current_score=0.80 + delta, score_std=0.10)
    # Stops iterating → goes to the inline trust gate (which then exports).
    assert route_after_evaluator(s) == "trust_gate"


def test_improvement_above_noise_floor_iterates():
    s = _state(prev_score=0.50, current_score=0.80, score_std=0.02)  # 0.30 >> noise
    assert route_after_evaluator(s) == "feature_engineer"


def test_max_iterations_caps_the_loop():
    s = _state(iteration=3, max_iterations=3, prev_score=0.10,
               current_score=0.90, score_std=0.0)
    assert route_after_evaluator(s) == "trust_gate"


def test_zero_noise_falls_back_to_threshold():
    # With no measured noise, the configured threshold still gates.
    tiny = settings.IMPROVEMENT_THRESHOLD / 2
    s = _state(prev_score=0.80, current_score=0.80 + tiny, score_std=0.0)
    assert route_after_evaluator(s) == "trust_gate"
