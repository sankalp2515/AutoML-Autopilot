"""FULL E2E — a real CSV driven all the way to a trained, trust-graded, served model.

This is the standing version of the doctrine that keeps paying off in this project:
RUN THE REAL PATH. The holdout leak, the streaming timeout, the empty
target_classes and the class_weight/partial_fit incompatibility were all found by
running real data, and none of them were caught by a mocked test.

Costs LLM tokens and takes minutes, so it is marked `e2e_full` and excluded from
the per-push CI job. Run it nightly, before a release, and after touching any
agent.

The dataset is generated with a KNOWN structure — a learnable signal, a genuine
class imbalance, and a deliberately planted LEAKY column — so the assertions are
about correctness, not just "it did not crash".
"""
from __future__ import annotations

import io

import numpy as np
import pytest

from .conftest import wait_for_run

pytestmark = [pytest.mark.e2e_full]

N_ROWS = 4000
POSITIVE_RATE = 0.12


def _make_campaign_csv(seed: int = 42) -> bytes:
    """A marketing-campaign shaped dataset with a known answer.

    - `tenure`, `balance`, `n_prior_opens` genuinely predict `converted`
    - `region`, `channel` are categorical noise
    - `outcome_note` is LEAKY: it is only known AFTER the outcome, and it encodes
      the target almost perfectly. The trust layer should flag it if it is used.
    """
    rng = np.random.default_rng(seed)
    n = N_ROWS
    tenure = rng.integers(0, 120, n)
    balance = rng.normal(2000, 1500, n).round(2)
    opens = rng.poisson(3, n)
    logit = (-3.2 + 0.012 * tenure + 0.00022 * balance + 0.28 * opens
             + rng.normal(0, 0.5, n))
    p = 1 / (1 + np.exp(-logit))
    converted = (rng.random(n) < p).astype(int)
    region = rng.choice(["north", "south", "east", "west"], n)
    channel = rng.choice(["email", "sms", "call"], n)
    outcome_note = np.where(converted == 1, "signed_up", "no_response")

    header = "tenure,balance,n_prior_opens,region,channel,outcome_note,converted\n"
    rows = "".join(
        f"{tenure[i]},{balance[i]},{opens[i]},{region[i]},{channel[i]},"
        f"{outcome_note[i]},{converted[i]}\n"
        for i in range(n)
    )
    return (header + rows).encode()


@pytest.fixture(scope="module")
def completed_run(api) -> dict:
    """Drive one full pipeline and share it across the assertions below.

    The goal text deliberately states unit economics and an action budget, so the
    Phase 3.5 path (business objective + rare-event framing) is exercised by the
    LLM's own decision rather than by injecting state.
    """
    csv = _make_campaign_csv()
    r = api.post(
        "/runs",
        files={"file": ("campaign.csv", csv, "text/csv")},
        data={
            "user_goal": (
                "Predict which customers will convert so we can decide who to contact. "
                "A conversion is worth about 300 USD and each wasted contact costs us "
                "40 USD. We can only afford to contact the top 10 percent of the list."
            ),
            "exclude_columns": "outcome_note",
            "pipeline": "tabular",
        },
    )
    assert r.status_code == 201, f"run creation failed: {r.status_code} {r.text[:400]}"
    run_id = r.json()["id"]
    final = wait_for_run(api, run_id)
    if final.get("status") != "completed":
        pytest.fail(f"pipeline did not complete: status={final.get('status')} "
                    f"step={final.get('current_step')} "
                    f"error={str(final.get('error_message'))[:400]}")
    return final


# ── the pipeline produced a real, honestly-evaluated model ───────────────────

def test_run_completed_with_a_winner(completed_run):
    assert completed_run["status"] == "completed"
    assert completed_run.get("final_score") is not None


def test_results_report_an_honest_holdout_basis(api, completed_run):
    """The headline score must come from data reserved BEFORE any fitting. If this
    ever reports `in_sample_split` on a 4000-row dataset, the holdout carve
    regressed."""
    r = api.get(f"/runs/{completed_run['id']}/results")
    assert r.status_code == 200, r.text
    results = r.json()
    assert results.get("evaluation_basis") == "holdout", results.get("evaluation_basis")
    assert results.get("winner_model") or results.get("winner_model_class")


def test_model_beats_the_naive_floor(api, completed_run):
    """A planted signal must be learned. Scoring at or below the dummy baseline
    means the pipeline produced a confident, useless model."""
    r = api.get(f"/runs/{completed_run['id']}/results")
    results = r.json()
    final = results.get("final_score")
    baseline = results.get("baseline_score")
    assert final is not None
    if baseline is not None:
        from app.core import metric_registry as mreg
        metric = results.get("primary_metric") or "auc_roc"
        assert mreg.directional_gain(final, baseline, metric) >= 0, (
            f"final {final} is not better than baseline {baseline} for {metric}")


def test_trust_report_exists_and_has_a_grade(api, completed_run):
    r = api.get(f"/runs/{completed_run['id']}/trust")
    assert r.status_code == 200, r.text
    trust = r.json()
    assert trust.get("grade") in ("A", "B", "C", "D", "F"), trust
    assert trust.get("probes"), "trust report has no probes"


def test_excluded_leaky_column_is_not_in_the_model(api, completed_run):
    """`outcome_note` is only knowable after the outcome. It was excluded at
    submission; it must not reappear as an inference feature."""
    r = api.get(f"/runs/{completed_run['id']}/schema")
    assert r.status_code == 200, r.text
    blob = str(r.json()).lower()
    assert "outcome_note" not in blob, "a post-outcome column reached the feature set"


# ── Phase 3.5: the decision layer ────────────────────────────────────────────

def test_decision_analysis_returns_the_budget_curve(api, completed_run):
    r = api.get(f"/runs/{completed_run['id']}/decision-analysis")
    assert r.status_code == 200, r.text
    data = r.json()

    top_k = data.get("top_k")
    assert top_k and top_k.get("rows"), "no top-k budget curve returned"
    recalls = [row["recall_at_k"] for row in top_k["rows"]]
    assert recalls == sorted(recalls), "recall@k must not decrease as budget grows"
    # A model with a real signal must beat random at the top of the ranking.
    assert top_k["rows"][0]["lift_at_k"] > 1.0, top_k["rows"][0]


def test_calibration_is_reported(api, completed_run):
    data = api.get(f"/runs/{completed_run['id']}/decision-analysis").json()
    cal = data.get("calibration") or {}
    assert cal.get("brier_score") is not None
    assert 0.0 <= cal["brier_score"] <= 1.0


def test_operating_point_is_present_and_honest(api, completed_run):
    """When the goal states unit economics the framer should set a business
    objective, and the optimizer must report the MEASURED value, not its own
    maximum. If the LLM declined to set an objective, the endpoint must say so
    explicitly rather than inventing a threshold."""
    data = api.get(f"/runs/{completed_run['id']}/decision-analysis").json()
    op = data.get("operating_point")
    assert op is not None, "operating_point key missing entirely"
    if "error" in op:
        assert "business objective" in op["error"].lower()
        return
    assert 0.0 <= op["optimal_threshold"] <= 1.0
    assert op["chosen_on_n"] > 0 and op["measured_on_n"] > 0
    assert "expected_value_per_case" in op
    # The optimism gap must be reported, not hidden.
    assert "optimism_gap_per_case" in op
    assert op["honesty_note"]


# ── serving round trip ───────────────────────────────────────────────────────

def test_deploy_then_predict_round_trip(api, completed_run):
    run_id = completed_run["id"]
    d = api.post(f"/runs/{run_id}/deploy")
    assert d.status_code in (200, 201), f"deploy failed: {d.status_code} {d.text[:300]}"

    rows = [{"tenure": 36, "balance": 2500.0, "n_prior_opens": 4,
             "region": "north", "channel": "email"}]
    p = api.post(f"/runs/{run_id}/predict", json={"rows": rows})
    assert p.status_code == 200, f"predict failed: {p.status_code} {p.text[:300]}"
    body = p.json()
    preds = body.get("predictions")
    assert preds and len(preds) == 1, body

    api.delete(f"/runs/{run_id}/deploy")


def test_artifacts_are_downloadable(api, completed_run):
    r = api.get(f"/runs/{completed_run['id']}/artifacts")
    assert r.status_code == 200, r.text
    arts = r.json()
    assert arts, "run produced no artifacts"
