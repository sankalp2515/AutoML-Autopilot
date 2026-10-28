"""Every sandbox template must COMPILE for every (metric, task) after the
scorer_prelude refactor (Phase 3.5).

The four templates used to carry four hand-copied metric→scorer maps. They now
interpolate `metric_registry.scorer_prelude(...)` instead. A template that fails
to render, or renders code that does not parse, is a run that dies inside the
sandbox with a traceback the user sees — so this compiles the emitted source for
the whole metric x task space rather than trusting one happy path.
"""
import ast

import pytest

from app.core import metric_registry as mreg

TASKS = ["binary_classification", "multiclass_classification",
         "multilabel_classification", "regression"]
ALL_METRICS = mreg.all_metrics()


def _render_model_selector(prelude: str, business_objective: dict | None = None) -> str:
    from app.agents.model_selector import TRAINING_CODE_TEMPLATE
    return TRAINING_CODE_TEMPLATE.format(
        enriched_path="/data/enriched.csv", task_type="binary_classification",
        primary_metric="auc_roc", class_imbalance="False",
        models_config=repr([{"name": "lr", "class": "LogisticRegression",
                             "initial_params": {}}]),
        fit_winner="True", label_columns="[]", label_delimiter="",
        imbalance_strategy="none", scorer_prelude=prelude,
        business_objective=repr(business_objective or {}),
    )


def _render_baseline(prelude: str) -> str:
    from app.agents.baseline_builder import BASELINE_CODE_TEMPLATE
    return BASELINE_CODE_TEMPLATE.format(
        target_column="y", task_type="binary_classification", exclude_cols="[]",
        scoring="roc_auc", scorer_prelude=prelude, label_columns="[]",
        label_delimiter="",
    )


def _render_feature_engineer(prelude: str) -> str:
    from app.agents.feature_engineer import FEATURE_CODE_TEMPLATE
    import inspect
    # Render with whatever kwargs the template actually declares, so this test
    # does not rot every time an unrelated field is added.
    import string
    fields = {f for _, f, _, _ in string.Formatter().parse(FEATURE_CODE_TEMPLATE) if f}
    kwargs = {f: "" for f in fields}
    kwargs["scorer_prelude"] = prelude
    for f in fields:
        if f.endswith(("_columns", "_cols", "features", "_list")) or f.startswith("engineered"):
            kwargs[f] = "[]"
    return FEATURE_CODE_TEMPLATE.format(**kwargs)


def _render_tuner(prelude: str) -> str:
    from app.agents.tuner import TUNING_CODE_TEMPLATE
    return (TUNING_CODE_TEMPLATE
            .replace("__ENRICHED_PATH__", repr("/data/e.csv"))
            .replace("__TASK_TYPE__", repr("binary_classification"))
            .replace("__PRIMARY_METRIC__", repr("auc_roc"))
            .replace("__SCORER_PRELUDE__", prelude)
            .replace("__WINNER_CLASS__", repr("LogisticRegression"))
            .replace("__WINNER_PATH__", repr("/data/w.pkl"))
            .replace("__N_TRIALS__", "10")
            .replace("__IMBALANCE_STRATEGY__", repr("none"))
            .replace("__SEARCH_SPACE__", repr({})))


def _render_model_selector_cost_sensitive(prelude: str) -> str:
    """The cost-sensitive branch is a DIFFERENT code path through the template
    (COST_WEIGHTS is not None), so it needs its own compile coverage."""
    return _render_model_selector(prelude, business_objective={
        "value_tp": 300.0, "cost_fp": 40.0, "cost_fn": 0.0, "value_tn": 0.0})


RENDERERS = {
    "model_selector": _render_model_selector,
    "model_selector_cost_sensitive": _render_model_selector_cost_sensitive,
    "baseline_builder": _render_baseline,
    "feature_engineer": _render_feature_engineer,
    "tuner": _render_tuner,
}


@pytest.mark.parametrize("template_name", sorted(RENDERERS))
@pytest.mark.parametrize("metric", ALL_METRICS)
@pytest.mark.parametrize("task", TASKS)
def test_template_compiles_for_every_metric_and_task(template_name, metric, task):
    prelude = mreg.scorer_prelude(metric, task)
    src = RENDERERS[template_name](prelude)
    ast.parse(src)                      # raises SyntaxError → test failure


@pytest.mark.parametrize("template_name", sorted(RENDERERS))
def test_no_template_carries_its_own_metric_vocabulary(template_name):
    """The whole point of the refactor: the scoring_map duplication is GONE.

    If someone re-adds a hand-written metric map to a template, the drift bug
    family is back and this fails.
    """
    import app.agents.baseline_builder as bb
    import app.agents.feature_engineer as fe
    import app.agents.model_selector as ms
    import app.agents.tuner as tn
    template = {
        "model_selector": ms.TRAINING_CODE_TEMPLATE,
        "model_selector_cost_sensitive": ms.TRAINING_CODE_TEMPLATE,
        "baseline_builder": bb.BASELINE_CODE_TEMPLATE,
        "feature_engineer": fe.FEATURE_CODE_TEMPLATE,
        "tuner": tn.TUNING_CODE_TEMPLATE,
    }[template_name]
    # Match the CODE pattern, not the word — the templates legitimately mention
    # "scoring_maps" in the comment explaining why they no longer have one.
    assert "scoring_map = {" not in template
    assert "roc_auc_ovr_weighted" not in template, \
        "multiclass remap must come from metric_registry, not a template copy"


@pytest.mark.parametrize("metric", ["precision_at_k", "recall_at_k", "lift_at_k"])
def test_at_k_prelude_defines_a_scorer_object_not_a_string(metric):
    """cross_val_score(scoring=...) accepts a string OR a scorer callable. The at-k
    preludes must produce the callable, or sklearn would look up a scorer named
    'precision_at_k' and raise."""
    src = mreg.scorer_prelude(metric, "binary_classification")
    ns: dict = {}
    exec(compile(src, "<prelude>", "exec"), ns)   # noqa: S102 - our own source
    assert not isinstance(ns["scoring"], str)
    assert callable(ns["scoring"])


_TEMPLATES_BY_ID = {
    "model_selector": "TRAINING_CODE_TEMPLATE",
    "model_selector_cost_sensitive": "TRAINING_CODE_TEMPLATE",
    "baseline_builder": "BASELINE_CODE_TEMPLATE",
    "feature_engineer": "FEATURE_CODE_TEMPLATE",
    "tuner": "TUNING_CODE_TEMPLATE",
}
_MODULES_BY_ID = {
    "model_selector": "app.agents.model_selector",
    "model_selector_cost_sensitive": "app.agents.model_selector",
    "baseline_builder": "app.agents.baseline_builder",
    "feature_engineer": "app.agents.feature_engineer",
    "tuner": "app.agents.tuner",
}

_STRING_METHODS = ("startswith", "endswith", "lower", "upper", "replace", "split", "strip")


@pytest.mark.parametrize("template_name", sorted(RENDERERS))
def test_no_template_calls_a_bare_string_method_on_scoring(template_name):
    """`scoring` is a make_scorer OBJECT for the at-k metrics, a str otherwise.

    A bare `scoring.startswith("neg_")` therefore raises inside the sandbox:
        AttributeError: '_Scorer' object has no attribute 'startswith'
    which is exactly what took down the tuner on the first live at-k run
    (2026-08-22, found by the e2e_full tier). Every such call must be guarded
    with isinstance(scoring, str).
    """
    import importlib

    mod = importlib.import_module(_MODULES_BY_ID[template_name])
    template = getattr(mod, _TEMPLATES_BY_ID[template_name])

    offenders = []
    for raw_line in template.splitlines():
        line = raw_line.strip()
        if "isinstance(" in line:
            continue
        for var in ("scoring", "cv_scoring"):
            for meth in _STRING_METHODS:
                if f"{var}.{meth}(" in line:
                    offenders.append(line)
    assert not offenders, (
        "Unguarded string method on `scoring` — raises for the at-k scorer objects:\n"
        + "\n".join(sorted(set(offenders))))
