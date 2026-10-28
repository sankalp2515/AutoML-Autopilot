"""Single source of truth for evaluation metrics.

Doctrine (docs/analysis/11-dynamic-doctrine.md): the *choice* of metric is an ML
DECISION the LLM makes; how a chosen metric maps to an sklearn scorer is a
MECHANIC that lives in code. This registry is that mechanic — and it replaces the
metric vocabulary that used to be hand-typed in the problem-framer prompt AND
duplicated across the baseline/model_selector/tuner/feature_engineer/evaluator
scoring maps (a documented source of drift bugs).

The framer's allowed-metric menu is now DERIVED from this registry, so adding a
metric is a one-line registry change — never a prompt edit. Mirrors model_registry.
"""

from __future__ import annotations

# metric_id → spec. `sklearn` is the base scorer; `multiclass` overrides it for
# multiclass tasks (binary-only scorers silently NaN otherwise — a real bug we hit).
# `tasks` is the set of task_types the metric is OFFERED for (the framer menu).
# `higher_is_better=False` marks "lower is better" metrics (error/loss).
_METRICS: dict[str, dict] = {
    "auc_roc":      {"sklearn": "roc_auc",  "multiclass": "roc_auc_ovr_weighted", "tasks": {"binary_classification"}, "higher_is_better": True},
    "pr_auc":       {"sklearn": "average_precision", "multiclass": "average_precision", "tasks": {"binary_classification"}, "higher_is_better": True},
    "recall":       {"sklearn": "recall",    "multiclass": "recall_weighted",    "tasks": {"binary_classification"}, "higher_is_better": True},
    "precision":    {"sklearn": "precision", "multiclass": "precision_weighted", "tasks": {"binary_classification"}, "higher_is_better": True},
    "f1":           {"sklearn": "f1",        "multiclass": "f1_weighted",        "tasks": {"binary_classification", "multiclass_classification"}, "higher_is_better": True},
    "accuracy":     {"sklearn": "accuracy",  "multiclass": "accuracy",           "tasks": {"binary_classification", "multiclass_classification"}, "higher_is_better": True},
    "rmse":         {"sklearn": "neg_root_mean_squared_error", "multiclass": None, "tasks": {"regression"}, "higher_is_better": False},
    "mae":          {"sklearn": "neg_mean_absolute_error",     "multiclass": None, "tasks": {"regression"}, "higher_is_better": False},
    "r2":           {"sklearn": "r2",         "multiclass": None,                "tasks": {"regression"}, "higher_is_better": True},
    "f1_micro":     {"sklearn": "f1_micro",   "multiclass": None,                "tasks": {"multilabel_classification"}, "higher_is_better": True},
    "f1_macro":     {"sklearn": "f1_macro",   "multiclass": None,                "tasks": {"multilabel_classification"}, "higher_is_better": True},
    "f1_samples":   {"sklearn": "f1_samples", "multiclass": None,                "tasks": {"multilabel_classification"}, "higher_is_better": True},
    "hamming_loss": {"sklearn": "hamming_loss", "multiclass": None,              "tasks": {"multilabel_classification"}, "higher_is_better": False},
    # ── Rare-event / needle-in-haystack (Phase 3.5) ───────────────────────────
    # These are NOT sklearn scorer names. `sklearn` holds a sentinel id and
    # `custom_k` marks them as budget-at-k metrics that scorer_prelude must build
    # with make_scorer. They stay plain strings so the direction property tests
    # (which call .startswith("neg_")) keep working unchanged.
    "precision_at_k": {"sklearn": "precision_at_k", "multiclass": None, "tasks": {"binary_classification"}, "higher_is_better": True,  "custom_k": True},
    "recall_at_k":    {"sklearn": "recall_at_k",    "multiclass": None, "tasks": {"binary_classification"}, "higher_is_better": True,  "custom_k": True},
    "lift_at_k":      {"sklearn": "lift_at_k",      "multiclass": None, "tasks": {"binary_classification"}, "higher_is_better": True,  "custom_k": True},
}

# Default budget for the at-k metrics when the framer names no k. 10% of the
# population is the conventional direct-marketing decile.
DEFAULT_K = 0.10


def is_custom_k(metric: str) -> bool:
    """True for the at-k metrics, which need a make_scorer prelude rather than a
    plain sklearn scoring string."""
    return bool(_METRICS.get(metric, {}).get("custom_k"))

# The default the framer should pick when the goal gives no strong signal.
_DEFAULT_BY_TASK = {
    "regression": "rmse",
    "multiclass_classification": "f1",       # → f1_weighted scorer
    "multilabel_classification": "f1_micro",
    "binary_classification": "auc_roc",
}


def all_metrics() -> list[str]:
    return list(_METRICS)


def allowed_for(task_type: str) -> list[str]:
    """Metrics offered to the LLM for a task (the framer menu)."""
    return [m for m, spec in _METRICS.items() if task_type in spec["tasks"]]


def default_for(task_type: str) -> str:
    return _DEFAULT_BY_TASK.get(task_type, "f1")


def higher_is_better(metric: str) -> bool:
    spec = _METRICS.get(metric)
    return True if spec is None else spec["higher_is_better"]


def sklearn_scorer(metric: str, task_type: str) -> str:
    """The sklearn scoring string for a (metric, task) — applies the multiclass
    remap so binary-only scorers don't silently NaN on multiclass."""
    spec = _METRICS.get(metric)
    if spec is None:
        return default_scorer(task_type)
    if task_type == "multiclass_classification" and spec.get("multiclass"):
        return spec["multiclass"]
    return spec["sklearn"]


def default_scorer(task_type: str) -> str:
    return sklearn_scorer(default_for(task_type), task_type)


def directional_gain(new_score: float, old_score: float, metric: str) -> float:
    """How much BETTER `new_score` is than `old_score`, as a signed number where
    POSITIVE always means improvement — for higher-better AND lower-better metrics.

    Reporting used to do a bare `final - baseline`, which printed a genuine rmse
    improvement (3.5 → 0.9) as "-2.6" in the model card, notebook and Overview —
    telling the customer the model got worse when it got better (2026-07-04 fix).
    """
    try:
        new_f, old_f = float(new_score), float(old_score)
    except (TypeError, ValueError):
        return 0.0
    return (new_f - old_f) if higher_is_better(metric) else (old_f - new_f)


def stored_score_lower_is_better(metric: str, task_type: str) -> bool:
    """SINGLE SOURCE OF TRUTH for winner selection direction.

    The training template stores `cv_score` as a POSITIVE error only when the run
    is regression AND the sklearn scorer is a `neg_*` one (it flips the sign);
    every other case is stored as the raw sklearn score, which is higher-better
    by sklearn convention. So this is the one and only lower-is-better case.

    Getting this wrong is silent and expensive: assuming "regression ⇒ lower is
    better" made r2 runs select the WORST candidate (2026-07-04 post-mortem).
    """
    return task_type == "regression" and sklearn_scorer(metric, task_type).startswith("neg_")


def scorer_prelude(metric: str, task_type: str, k: float = DEFAULT_K) -> str:
    """Sandbox-side Python that defines a `scoring` variable for cross_val_score.

    WHY THIS EXISTS. The metric→scorer map used to be hand-copied into three
    separate sandbox templates (model_selector, tuner, feature_engineer) plus a
    hardcoded fourth in baseline_builder, each with its own copy of the multiclass
    remap. That is the exact duplication this registry's docstring warns about, and
    it is the shape of the direction-bug family already in the post-mortems: four
    copies drift, one of them silently scores the wrong thing.

    Emitting the resolution as CODE from the single source of truth means the
    templates hold no metric vocabulary at all.

    For plain sklearn metrics this is a one-line assignment. For the at-k metrics
    it inlines the implementation, because the sandbox is a separate container
    that cannot import `app.core.rare_event`. The inlined maths is kept
    deliberately identical to that module, and `tests/test_agents/
    test_rare_event_scorers.py` asserts the two agree on random inputs.
    """
    resolved = sklearn_scorer(metric, task_type)

    if not is_custom_k(metric):
        return f"scoring = {resolved!r}"

    k = float(k) if 0.0 < float(k) <= 1.0 else DEFAULT_K
    return f'''
# ── at-k scorer, emitted by metric_registry.scorer_prelude ({metric} @ k={k}) ──
import math as _math
import numpy as _np
from sklearn.metrics import make_scorer as _make_scorer

_AT_K = {k!r}
_AT_K_KIND = {metric!r}

def _at_k_metric(_y_true, _y_score):
    _y = (_np.asarray(_y_true).ravel() > 0).astype(int)
    _s = _np.asarray(_y_score, dtype=float)
    if _s.ndim > 1:                 # make_scorer may hand us the full (n, 2) proba
        _s = _s[:, -1]              # ...so select BEFORE ravel, never after
    _s = _s.ravel()
    _n = _y.shape[0]
    if _n == 0:
        return 0.0
    _order = _np.argsort(-_s, kind="stable")
    _cut = max(1, min(_n, _math.ceil(_n * _AT_K)))
    _captured = float(_y[_order][:_cut].sum())
    _n_pos = float(_y.sum())
    if _AT_K_KIND == "precision_at_k":
        return _captured / float(_cut)
    if _AT_K_KIND == "recall_at_k":
        return (_captured / _n_pos) if _n_pos > 0 else 0.0
    _base = _n_pos / _n
    return ((_captured / float(_cut)) / _base) if _base > 0 else 0.0

# sklearn >=1.4 uses response_method; older versions use needs_proba.
try:
    scoring = _make_scorer(_at_k_metric, response_method="predict_proba")
except TypeError:
    scoring = _make_scorer(_at_k_metric, needs_proba=True)
'''.strip()


def framer_menu_text() -> str:
    """Per-task allowed-metric menu injected into the problem-framer prompt, so
    the vocabulary is never hand-typed/duplicated in the prompt."""
    lines = []
    for task in ("binary_classification", "multiclass_classification",
                 "multilabel_classification", "regression"):
        allowed = " | ".join(allowed_for(task))
        lines.append(f"- {task}: {allowed} (default: {default_for(task)})")
    return "\n".join(lines)
