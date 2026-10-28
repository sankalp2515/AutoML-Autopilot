"""Seeded incident catalog + fault injector.

Each scenario produces an IncidentPack: a reference window (what the incumbent
was trained on), a current window (production data, labels optional), the
deployed pipeline/serving configuration and, for some incidents, an error log or a
teammate's challenger model to review.

The ORACLE (which fault was injected, which decision is correct) is returned only
by `oracle()` and is read solely by the benchmark harness and tests. The engine,
planner and selector receive the pack without it, so the planner cannot "guess"
the injected cause from fixture metadata.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

TARGET = "defaulted"
NUMERIC = ["income", "loan_amount", "credit_score", "debt_to_income", "employment_years", "age"]
CATEGORICAL = ["region", "channel"]
FEATURES = NUMERIC + CATEGORICAL
# Declared in the data contract and allowed for challengers, but not used by the
# incumbent (bureau inquiries exist only for urban/suburban applicants).
OPTIONAL = ["inquiries_6m"]
# F1 (not recall) so a candidate cannot pass by flagging everyone in the segment.
CRITICAL_SEGMENT = {"column": "region", "value": "rural", "metric": "f1", "max_drop": 0.05}
BASE_PIPELINE = {
    "model": "logistic_regression",
    "numeric": NUMERIC,
    "categorical": CATEGORICAL,
    "onehot": {"handle_unknown": "ignore", "sparse_output": False},
    "threshold": 0.30,
    "class_weight": None,
    "optional_features": OPTIONAL,
}


# What the people around the incident said, as an on-call engineer would paste it in.
# Free text only a language model can use; it is context, not truth, and may mislead.
# The rules planner never reads it. No note names the injected fault directly.
INCIDENT_NOTES: dict[str, str] = {
    "S01": "#ml-oncall (Mon): weekly health check. No deploys to the scoring service since last month. "
           "Risk asks whether we should retrain 'just in case'.",
    "S02": "#ml-oncall (Wed): approvals team reports a spike in false alarms since Tuesday. Data platform "
           "shipped ingestion v2.3 on Tuesday; their changelog says 'standardised monetary fields across "
           "sources'. No model or threshold change on our side.",
    "S03": "#ml-oncall: collections says we now miss defaulters we used to catch. Last Thursday's deploy "
           "touched the scoring service config (PR 'tidy scoring config'). No data pipeline changes reported.",
    "S04": "#ml-oncall: scores look odd since the bureau vendor switch on Monday. Vendor onboarding notes: "
           "'thin-file applicants may come without employment history; region codes follow our taxonomy'.",
    "S05": "#ml-oncall: macro team flags a downturn; collections volume is up this quarter. Model quality on "
           "recent loans looks worse. Someone suggested retraining on last month.",
    "S07": "#ml-oncall: drift alert fired on the new marketing segment (campaign launched two weeks ago, "
           "younger applicants). Labels for these loans mature in 90 days. Business asks whether to retrain now.",
    "S08": "#model-review: gbm-v7 from a teammate, 'F1 0.81 on validation, adds a few engineered features "
           "from the collections system'.",
    "S09": "#model-review: logreg-v5 'beats prod by 4 points'; evaluated in the author's notebook.",
    "S10": "#model-review: logreg-v6 improves headline F1 and product wants it live this week. Trained on "
           "the 'cleaned' dataset.",
    "S12": "#ml-oncall: pilot branch says the model misses defaulters. Only a handful of pilot loans have "
           "matured so far.",
    "T01": "#ml-platform: nightly retrain failed after the platform team bumped scikit-learn in all images "
           "(ticket PLAT-218). Serving is unaffected; the retrain job error is attached.",
}


@dataclass
class IncidentPack:
    scenario_id: str
    kind: str                       # degradation | challenger_review | pipeline_failure
    title: str
    symptom: str                    # what the on-call engineer reports (no cause)
    reference: pd.DataFrame
    current: pd.DataFrame
    target: str = TARGET
    primary_metric: str = "f1"
    pipeline: dict[str, Any] = field(default_factory=lambda: dict(BASE_PIPELINE))
    serving: dict[str, Any] = field(default_factory=dict)
    environment: dict[str, Any] = field(default_factory=dict)
    error_log: str | None = None
    challenger: dict[str, Any] | None = None
    # Config the production model was trained with (defaults to `pipeline`). Differs
    # only when the incident is a failed retrain of an otherwise healthy model.
    incumbent_pipeline: dict[str, Any] | None = None
    critical_segment: dict[str, Any] | None = field(default_factory=lambda: dict(CRITICAL_SEGMENT))
    source: str = "catalog"                  # "catalog" (seeded demo) | "upload" (user data)
    notes: str = ""                          # free-text team context (uploads); catalog uses INCIDENT_NOTES
    docs_urls: list[str] = field(default_factory=list)   # public docs the user asked us to read


def _population(n: int, rng: np.random.Generator, prevalence_shift: float = 0.0) -> pd.DataFrame:
    region = rng.choice(["urban", "suburban", "rural"], n, p=[0.5, 0.32, 0.18])
    channel = rng.choice(["web", "branch", "partner"], n, p=[0.55, 0.25, 0.20])
    age = np.clip(rng.normal(41, 12, n), 19, 80).round()
    employment = np.clip(rng.gamma(2.2, 3.0, n), 0, 40).round(1)
    income = np.round(np.exp(rng.normal(11.0, 0.45, n)) * (1 + 0.012 * employment), -2)
    credit = np.clip(rng.normal(680, 70, n), 300, 850).round()
    loan = np.round(income * rng.uniform(0.15, 0.6, n), -2)
    dti = np.clip(rng.normal(0.32, 0.11, n), 0.02, 0.95).round(3)
    inquiries = rng.poisson(1.6, n).astype(float)
    logit = (-1.9 - 0.018 * (credit - 680) + 4.2 * (dti - 0.32) - 1.1 * (np.log(income) - 11.0)
             + 0.9 * (loan / income - 0.37) - 0.04 * employment
             + np.where(channel == "partner", 0.45, 0.0) + np.where(region == "rural", 0.25, 0.0)
             # non-linear effects a linear model only approximates: a DTI cliff, and thin-file
             # rural borrowers being riskier below a 640 score
             + 1.3 * (dti > 0.45) + np.where((region == "rural") & (credit < 640), 1.4, 0.0)
             + 0.8 * (inquiries - 1.6)
             + prevalence_shift)
    p = 1 / (1 + np.exp(-logit))
    y = (rng.uniform(size=n) < p).astype(int)
    inquiries[region == "rural"] = np.nan          # bureau feed has no rural coverage
    return pd.DataFrame({"income": income, "loan_amount": loan, "credit_score": credit,
                         "debt_to_income": dti, "employment_years": employment, "age": age,
                         "region": region, "channel": channel, "inquiries_6m": inquiries, TARGET: y})


def _windows(seed: int, n_ref: int = 6000, n_cur: int = 4000, prevalence_shift: float = 0.0):
    rng = np.random.default_rng(seed)
    return _population(n_ref, rng), _population(n_cur, rng, prevalence_shift)


def _env(previous_sklearn: str | None = None) -> dict[str, Any]:
    import sklearn
    env = {"python": "3.12", "scikit-learn": sklearn.__version__, "pandas": pd.__version__}
    if previous_sklearn:
        env["previous"] = {"scikit-learn": previous_sklearn}
    return env


# ── Scenario builders ─────────────────────────────────────────────────────────
def _s01(seed):
    ref, cur = _windows(seed)
    return IncidentPack("S01", "degradation", "Weekly model health check",
                        "Routine review: on-call wants to know whether the default model needs action.",
                        ref, cur, environment=_env())


def _s02(seed):
    ref, cur = _windows(seed)
    cur["income"] = (cur["income"] / 1000).round(2)       # upstream now sends k$
    return IncidentPack("S02", "degradation", "Loan-default F1 fell after a data platform release",
                        "F1 on last week's labelled loans dropped sharply; approvals team reports many false alarms.",
                        ref, cur, environment=_env())


def _s03(seed):
    ref, cur = _windows(seed)
    return IncidentPack("S03", "degradation", "Recall dropped after a config deploy",
                        "Collections says the model now misses defaulters it used to catch.",
                        ref, cur, serving={"threshold": 0.62}, environment=_env())


def _s04(seed):
    ref, cur = _windows(seed)
    rng = np.random.default_rng(seed + 1)
    cur.loc[rng.uniform(size=len(cur)) < 0.55, "credit_score"] = np.nan
    cur.loc[rng.uniform(size=len(cur)) < 0.2, "region"] = "remote"
    return IncidentPack("S04", "degradation", "Scores look strange after a vendor feed switch",
                        "Risk analysts see odd score distributions since the bureau feed changed vendors.",
                        ref, cur, environment=_env())


def _s05(seed):
    ref, cur = _windows(seed, prevalence_shift=1.15)     # downturn: more defaults
    return IncidentPack("S05", "degradation", "Default rate climbed and F1 slipped",
                        "Macro team flags a downturn; model quality on recent loans looks worse.",
                        ref, cur, environment=_env())


def _s07(seed):
    ref, cur = _windows(seed)
    cur["income"] = (cur["income"] * 1.35).round(-2)
    cur["age"] = (cur["age"] - 6).clip(19, 80)
    cur[TARGET] = np.nan                                   # outcomes not matured yet
    return IncidentPack("S07", "degradation", "Drift alert on a new marketing segment",
                        "Monitoring raised a drift alert; the business asks whether to retrain now.",
                        ref, cur, environment=_env())


def _challenger(kind: str) -> dict[str, Any]:
    if kind == "leak":
        return {"name": "gbm-v7 (teammate)", "model": "hist_gradient_boosting",
                "features": FEATURES + ["recovery_calls_90d"], "claimed_metric": {"f1": 0.81},
                "split": "same", "exclude_segment": None, "class_weight": None}
    if kind == "protocol":
        return {"name": "logreg-v5 (teammate)", "model": "logistic_regression", "features": FEATURES,
                "claimed_metric": {"f1": 0.66}, "split": "own_random_split",
                "exclude_segment": None, "class_weight": "balanced"}
    # Adds the bureau-inquiries feature and drops rural rows (where it is missing)
    # from training: a real aggregate gain that quietly abandons rural applicants.
    return {"name": "logreg-v6 (teammate)", "model": "logistic_regression", "features": FEATURES + OPTIONAL,
            "claimed_metric": {"f1": 0.62}, "split": "same",
            "exclude_segment": {"column": "region", "value": "rural"}, "class_weight": None}


def _with_leak(df: pd.DataFrame, seed: int) -> pd.DataFrame:
    # Collections calls happen AFTER a loan defaults: a post-outcome feature.
    rng = np.random.default_rng(seed + 2)
    out = df.copy()
    y = out[TARGET].fillna(0).to_numpy()
    out["recovery_calls_90d"] = np.where(y == 1, rng.poisson(4, len(out)), rng.poisson(0.3, len(out)))
    return out


def _s08(seed):
    ref, cur = _windows(seed)
    return IncidentPack("S08", "challenger_review", "Review: teammate's GBM claims F1 0.81",
                        "A teammate wants to ship gbm-v7, reporting a large F1 gain.",
                        _with_leak(ref, seed), _with_leak(cur, seed), environment=_env(),
                        challenger=_challenger("leak"))


def _s09(seed):
    ref, cur = _windows(seed)
    return IncidentPack("S09", "challenger_review", "Review: logreg-v5 metric from a different split",
                        "A challenger's reported F1 was computed on the author's own split.",
                        ref, cur, environment=_env(), challenger=_challenger("protocol"))


def _s10(seed):
    ref, cur = _windows(seed)
    return IncidentPack("S10", "challenger_review", "Review: logreg-v6 improves aggregate F1",
                        "A retrained candidate improves headline F1; product wants it live this week.",
                        ref, cur, environment=_env(), challenger=_challenger("segment"))


def _s12(seed):
    ref, cur = _windows(seed, n_cur=80)
    return IncidentPack("S12", "degradation", "Recall complaint on a tiny pilot portfolio",
                        "A pilot branch says the model misses defaulters; only a handful of loans have matured.",
                        ref, cur, serving={"threshold": 0.62}, environment=_env())


def _t01(seed):
    ref, cur = _windows(seed)
    pipeline = dict(BASE_PIPELINE)
    pipeline["onehot"] = {"handle_unknown": "ignore", "sparse": False}   # pre-1.2 spelling
    return IncidentPack(
        "T01", "pipeline_failure", "Nightly retrain fails after dependency upgrade",
        "The scheduled retrain crashed right after the platform team bumped scikit-learn.",
        ref, cur, pipeline=pipeline, environment=_env(previous_sklearn="1.1.3"),
        incumbent_pipeline=dict(BASE_PIPELINE))   # error log comes from a REAL failed fit


_BUILDERS = {"S01": _s01, "S02": _s02, "S03": _s03, "S04": _s04, "S05": _s05, "S07": _s07,
             "S08": _s08, "S09": _s09, "S10": _s10, "S12": _s12, "T01": _t01}

# Oracle — benchmark/tests only. expected = acceptable terminal decisions.
_ORACLE = {
    "S01": {"fault": None, "expected": {"INCONCLUSIVE"}},
    "S02": {"fault": "PREPROCESSING_MISMATCH", "expected": {"CORRECTION_VERIFIED"}},
    "S03": {"fault": "THRESHOLD_MISCALIBRATION", "expected": {"CORRECTION_VERIFIED"}},
    "S04": {"fault": "DATA_CONTRACT_BREAK", "expected": {"BLOCKED"}},
    "S05": {"fault": "LABEL_PRIOR_SHIFT", "expected": {"CORRECTION_VERIFIED", "INCONCLUSIVE"}},
    "S07": {"fault": "FEATURE_SHIFT_UNLABELED", "expected": {"INCONCLUSIVE"}},
    "S08": {"fault": "TARGET_LEAKAGE", "expected": {"REJECT"}},
    "S09": {"fault": "PROTOCOL_MISMATCH", "expected": {"REJECT"}},
    "S10": {"fault": "SEGMENT_REGRESSION", "expected": {"REJECT"}},
    "S12": {"fault": "THRESHOLD_MISCALIBRATION", "expected": {"INCONCLUSIVE"}},
    "T01": {"fault": "LIBRARY_API_CHANGE",
            "expected": {"CORRECTION_VERIFIED", "DIAGNOSIS_SUPPORTED", "INCONCLUSIVE"}},
}


def build(scenario_id: str, seed: int) -> IncidentPack:
    if scenario_id.startswith("upload:"):
        from app.autopilot import uploads
        return uploads.load_pack(scenario_id.split(":", 1)[1])
    try:
        return _BUILDERS[scenario_id](seed)
    except KeyError:
        raise ValueError(f"unknown scenario {scenario_id!r}") from None


def oracle(scenario_id: str) -> dict[str, Any]:
    return _ORACLE[scenario_id]


def catalog() -> list[dict[str, Any]]:
    """Public listing — titles and symptoms only, never the fault."""
    out = []
    for sid in _BUILDERS:
        p = build(sid, 7)
        out.append({"scenario_id": sid, "kind": p.kind, "title": p.title, "symptom": p.symptom,
                    "rows_reference": len(p.reference), "rows_current": len(p.current),
                    "labels_available": bool(p.current[p.target].notna().any()),
                    "has_challenger": p.challenger is not None})
    return out
