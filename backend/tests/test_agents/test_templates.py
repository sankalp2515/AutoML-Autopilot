"""
Render every sandbox code template and compile the result.

Catches the recurring bug class: a brace mistake in a template that crashes at
runtime (e.g. an un-doubled `{}` in a .format() template, or a forgotten token).

Two render styles exist (both must render to compilable Python):
  - FORMAT templates use str.format() — every literal brace must be doubled `{{}}`.
  - TOKEN templates (tuner, evaluator) are dict/f-string heavy, so they use natural
    single braces and inject values via .replace("__TOKEN__", repr(value)) like the
    exporter. These cannot be .format()'d.
"""

import pytest

from app.core import metric_registry as _mreg
from app.agents.baseline_builder import BASELINE_CODE_TEMPLATE
from app.agents.data_splitter import SPLIT_CODE_TEMPLATE
from app.agents.eda_agent import EDA_CODE_TEMPLATE
from app.agents.evaluator import EVALUATION_CODE_TEMPLATE
from app.agents.feature_engineer import FEATURE_CODE_TEMPLATE
from app.agents.model_selector import TRAINING_CODE_TEMPLATE
from app.agents.preprocessor import PREPROCESSING_CODE_TEMPLATE
from app.agents.tuner import TUNING_CODE_TEMPLATE


def _fmt(template, **kwargs):
    return lambda: template.format(**kwargs)


def _tokens(template, _raw=None, **repl):
    """Token templates inject values via repr(). `_raw` is for tokens whose value
    is SOURCE CODE (the scorer prelude) and must not be repr()'d into a string."""
    def render():
        code = template
        for token, value in repl.items():
            code = code.replace(token, repr(value))
        for token, value in (_raw or {}).items():
            code = code.replace(token, value)
        return code
    return render


# Each case: (id, render_callable). render() returns the final sandbox code string.
RENDER_CASES = [
    ("eda", _fmt(EDA_CODE_TEMPLATE, target_column="t", task_type="regression")),
    ("baseline", _fmt(
        BASELINE_CODE_TEMPLATE, target_column="t", task_type="binary_classification",
        exclude_cols="[]", scoring="roc_auc", label_columns="[]", label_delimiter="",
        scorer_prelude=_mreg.scorer_prelude("auc_roc", "binary_classification"),
    )),
    ("preprocessing", _fmt(
        PREPROCESSING_CODE_TEMPLATE, target_column="t", task_type="multiclass_classification",
        drop_columns="[]", exclude_cols="[]", datetime_cols="[]",
        imputation_strategy="{}", num_imputer_default="median",
        encoding_strategy="{}", scaling_strategy="standard",
        imbalance_strategy="none", label_columns="[]", label_delimiter="",
        use_text_embed="False",
    )),
    # multilabel path through the preprocessing template (P19)
    ("preprocessing_multilabel", _fmt(
        PREPROCESSING_CODE_TEMPLATE, target_column="tags", task_type="multilabel_classification",
        drop_columns="[]", exclude_cols="[]", datetime_cols="[]",
        imputation_strategy="{}", num_imputer_default="median",
        encoding_strategy="{}", scaling_strategy="standard",
        imbalance_strategy="none", label_columns="[]", label_delimiter=";",
        use_text_embed="False",
    )),
    ("feature_engineering", _fmt(
        FEATURE_CODE_TEMPLATE, processed_path="/x.csv", target_column="t",
        task_type="regression", primary_metric="rmse", proposed_features="[]",
        imbalance_strategy="none",
        scorer_prelude=_mreg.scorer_prelude("rmse", "regression"),
    )),
    ("model_training", _fmt(
        TRAINING_CODE_TEMPLATE, enriched_path="/x.csv", task_type="binary_classification",
        primary_metric="recall", class_imbalance="True", models_config="[]",
        fit_winner="True",
        label_columns="[]", label_delimiter="", imbalance_strategy="smote",
        scorer_prelude=_mreg.scorer_prelude("recall", "binary_classification"),
        business_objective=repr({"value_tp": 300.0, "cost_fp": 40.0,
                                 "cost_fn": 0.0, "value_tn": 0.0}),
    )),
    ("tuning", _tokens(
        TUNING_CODE_TEMPLATE,
        __ENRICHED_PATH__="/x.csv", __TASK_TYPE__="regression", __PRIMARY_METRIC__="rmse",
        __WINNER_CLASS__="Ridge", __WINNER_PATH__="/w.pkl", __N_TRIALS__=5,
        __IMBALANCE_STRATEGY__="none",
        _raw={"__SCORER_PRELUDE__": _mreg.scorer_prelude("rmse", "regression")},
        __SEARCH_SPACE__={"alpha": {"type": "float", "low": 1e-4, "high": 100.0, "log": True}},
    )),
    ("evaluation", _tokens(
        EVALUATION_CODE_TEMPLATE,
        __ENRICHED_PATH__="/x.csv", __TUNED_PATH__="/t.pkl", __TASK_TYPE__="regression",
        __PRIMARY_METRIC__="rmse", __FP_FN_PREFERENCE__="",
        __HOLDOUT_PATH__="/h.csv", __PREPROCESSOR_PATH__="/p.pkl",
        __ENGINEERED_FEATURES__=[{"name": "f1", "formula": "a / b", "fill_value": 0.0}],
        __TARGET_CLASSES__=["a", "b"], __TARGET_COLUMN__="t",
        __LABEL_COLUMNS__=[], __LABEL_DELIMITER__="", __MLB_PATH__="/m.pkl",
    )),
    # multilabel-delimiter path through the evaluation holdout transform (P19)
    ("evaluation_multilabel", _tokens(
        EVALUATION_CODE_TEMPLATE,
        __ENRICHED_PATH__="/x.csv", __TUNED_PATH__="/t.pkl",
        __TASK_TYPE__="multilabel_classification",
        __PRIMARY_METRIC__="f1_macro", __FP_FN_PREFERENCE__="",
        __HOLDOUT_PATH__="/h.csv", __PREPROCESSOR_PATH__="/p.pkl",
        __ENGINEERED_FEATURES__=[], __TARGET_CLASSES__=[], __TARGET_COLUMN__="tags",
        __LABEL_COLUMNS__=[], __LABEL_DELIMITER__=";", __MLB_PATH__="/m.pkl",
    )),
    ("data_split", _tokens(
        SPLIT_CODE_TEMPLATE,
        __TARGET_COLUMN__="t", __TASK_TYPE__="binary_classification",
        __HOLDOUT_FRAC__=0.2, __MIN_ROWS__=60,
    )),
]


@pytest.mark.parametrize("name,render", RENDER_CASES, ids=[c[0] for c in RENDER_CASES])
def test_template_renders_and_compiles(name, render):
    # .format() raises KeyError/IndexError on any unescaped literal brace;
    # token templates must have every __TOKEN__ replaced.
    code = render()
    assert "__" + "ENRICHED_PATH__" not in code, f"{name}: unreplaced token remains"
    compile(code, f"<{name}_template>", "exec")
