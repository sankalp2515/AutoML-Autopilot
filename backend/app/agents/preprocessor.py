import json
from typing import Any

from app.agents.base_agent import BaseAgent
from app.core.state import AgentState

SYSTEM_PROMPT = """You are an expert ML preprocessing engineer.
Based on the EDA insights, dataset audit, and prioritized issues, generate a sklearn Pipeline preprocessing strategy.

Respond with JSON:
{
  "decisions": [
    {
      "column": "<col_name>",
      "issue": "<what was found>",
      "strategy": "<what to do>",
      "reasoning": "<why this strategy>",
      "sklearn_step": "<imputer/encoder/scaler choice>"
    }
  ],
  "drop_columns": ["<cols to drop entirely with reason>"],
  "imputation_strategy": {
    "<col>": "median" | "mean" | "most_frequent" | "constant" | "knn"
  },
  "encoding_strategy": {
    "<col>": "onehot" | "ordinal" | "target" | "frequency" | "text_tfidf"
  },
  "scaling_strategy": "standard" | "minmax" | "robust" | "none",
  "handle_imbalance": true | false,
  "imbalance_strategy": "class_weight" | "smote" | "smote_tomek" | "none"
}

Use "text_tfidf" for FREE-TEXT columns (reviews, descriptions, messages — avg length > 30 chars).
TF-IDF extracts up to 200 n-gram features per text column. Never one-hot a free-text column.

Imbalance strategy guidance:
- "class_weight": use model's class_weight="balanced" (no resampling, safe for all models)
- "smote": SMOTE oversampling inside CV folds ONLY (via imblearn.pipeline.Pipeline) — best for severe imbalance (<5% minority)
- "smote_tomek": SMOTE + Tomek links cleaning inside CV folds — good for noisy imbalanced data
- "none": no imbalance handling

CRITICAL: SMOTE/SMOTE+Tomek are applied ONLY during training (inside CV folds via imblearn.pipeline.Pipeline).
The saved inference preprocessor.pkl MUST NOT include any resampler — resampling is train-time only.

For MULTILABEL classification:
- If label_delimiter is set: target column contains delimited strings (e.g. "tag1;tag2"). The preprocessor builds a MultiLabelBinarizer.
- If label_columns is set: multiple binary columns represent labels (e.g. "label_a", "label_b" all 0/1). The preprocessor passes them through as-is.
- The multilabel binarizer (or identity for binary columns) is saved for inference decoding.
"""

PREPROCESSING_CODE_TEMPLATE = '''
import pandas as pd
import numpy as np
import joblib
import json
import os
import warnings
warnings.filterwarnings("ignore")

from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer, KNNImputer
from sklearn.preprocessing import (StandardScaler, MinMaxScaler, RobustScaler,
                                    OneHotEncoder, OrdinalEncoder, LabelEncoder,
                                    MultiLabelBinarizer)
from sklearn.compose import ColumnTransformer
# imblearn pipeline applies resampling ONLY during fit() — never during transform()
# This ensures no data leakage: resampling happens inside each CV fold, never on validation data.
from imblearn.pipeline import Pipeline as ImbPipeline
from imblearn.over_sampling import SMOTE
from imblearn.combine import SMOTETomek

df = pd.read_csv(dataset_path)
target_col = "{target_column}"
task_type = "{task_type}"
drop_cols = {drop_columns}
exclude_cols = {exclude_cols}
imbalance_strategy = "{imbalance_strategy}"

# Drop rows with a MISSING TARGET — models cannot train on NaN labels
# ("Input y contains NaN"). Applied to every read of the source so X and y align.
if target_col and target_col in df.columns:
    df = df[df[target_col].notna()].reset_index(drop=True)
label_columns = {label_columns}
label_delimiter = "{label_delimiter}"

# Drop user-excluded and agent-flagged columns
all_drop = list(set(drop_cols + exclude_cols + [target_col]))
drop_existing = [c for c in all_drop if c in df.columns]
df = df.drop(columns=drop_existing, errors="ignore")
df = df.dropna(subset=[]) # keep all rows, imputer handles nulls

# Parse datetime columns
datetime_cols = {datetime_cols}
for c in datetime_cols:
    if c in df.columns:
        dt = pd.to_datetime(df[c], errors="coerce")
        df[c + "_year"] = dt.dt.year
        df[c + "_month"] = dt.dt.month
        df[c + "_day"] = dt.dt.day
        df[c + "_dayofweek"] = dt.dt.dayofweek
        df = df.drop(columns=[c])

# Final feature columns (excluding target and label_columns)
feature_cols = [c for c in df.columns if c != target_col and c not in label_columns]
num_cols = df[feature_cols].select_dtypes(include=["number"]).columns.tolist()
cat_cols = df[feature_cols].select_dtypes(exclude=["number"]).columns.tolist()

# Imputation
imputation = {imputation_strategy}
num_imputer_default = "{num_imputer_default}"
cat_imputer_default = "most_frequent"

num_imputers = []
cat_imputers = []

# Build column-level imputers
for c in num_cols:
    strategy = imputation.get(c, num_imputer_default)
    if strategy == "knn":
        num_imputers.append((c, KNNImputer(n_neighbors=5), [c]))
    # else handled in global imputer

num_global_imputer = SimpleImputer(strategy=num_imputer_default)
cat_global_imputer = SimpleImputer(strategy=cat_imputer_default, fill_value="missing")

# Encoding
encoding = {encoding_strategy}

# Free-text columns → TF-IDF (handled as dedicated ColumnTransformer entries).
# Must be removed from cat_cols BEFORE the onehot/ordinal splits below.
tfidf_cols = [c for c in cat_cols if encoding.get(c) == "text_tfidf"]
for c in tfidf_cols:
    df[c] = df[c].fillna("").astype(str)   # TfidfVectorizer rejects NaN
    cat_cols = [x for x in cat_cols if x != c]

onehot_cols = [c for c in cat_cols if encoding.get(c, "onehot") == "onehot"]
ordinal_cols = [c for c in cat_cols if encoding.get(c) == "ordinal"]
freq_cols = [c for c in cat_cols if encoding.get(c) == "frequency"]

# Frequency encoding applied before ColumnTransformer
for c in freq_cols:
    freq_map = df[c].value_counts(normalize=True).to_dict()
    df[c + "_freq"] = df[c].map(freq_map).fillna(0)
    df = df.drop(columns=[c])
    num_cols.append(c + "_freq")
    cat_cols = [x for x in cat_cols if x != c]

onehot_cols = [c for c in cat_cols if encoding.get(c, "onehot") == "onehot"]
ordinal_cols_remaining = [c for c in cat_cols if encoding.get(c) == "ordinal"]

# Scaling
scaling = "{scaling_strategy}"
if scaling == "standard":
    scaler = StandardScaler()
elif scaling == "minmax":
    scaler = MinMaxScaler()
elif scaling == "robust":
    scaler = RobustScaler()
else:
    scaler = "passthrough"

# Build ColumnTransformer (preprocessor for inference — NO resampler)
transformers = []
if num_cols:
    transformers.append((
        "num",
        Pipeline([("imp", num_global_imputer), ("scale", scaler)]),
        num_cols
    ))
if onehot_cols:
    transformers.append((
        "cat_onehot",
        Pipeline([("imp", cat_global_imputer),
                  ("enc", OneHotEncoder(handle_unknown="ignore", sparse_output=False, max_categories=50))]),
        onehot_cols
    ))
if ordinal_cols_remaining:
    transformers.append((
        "cat_ordinal",
        Pipeline([("imp", cat_global_imputer),
                  ("enc", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1))]),
        ordinal_cols_remaining
    ))

# Free-text columns → dense MiniLM embeddings (DL-2c) when the baked-in model is
# present, else TF-IDF. Same string (1-D) column selector either way.
from sklearn.feature_extraction.text import TfidfVectorizer
_use_embed = {use_text_embed}
_embedder_cls = None
if _use_embed:
    try:
        from automl_dl.text_embed import MiniLMEmbedder, available as _emb_available
        if _emb_available():
            _embedder_cls = MiniLMEmbedder
    except Exception:
        _embedder_cls = None
for c in tfidf_cols:
    if _embedder_cls is not None:
        transformers.append((f"emb_{{c}}", _embedder_cls(), c))
    else:
        transformers.append((
            f"tfidf_{{c}}",
            TfidfVectorizer(max_features=200, ngram_range=(1, 2), min_df=2,
                            sublinear_tf=True, strip_accents="unicode"),
            c
        ))

preprocessor = ColumnTransformer(transformers, remainder="drop", sparse_threshold=0.0)

# Read original df with target for saving
df_full = pd.read_csv(dataset_path)
if target_col and target_col in df_full.columns:
    df_full = df_full[df_full[target_col].notna()].reset_index(drop=True)
df_full = df_full.drop(columns=[c for c in drop_existing if c != target_col], errors="ignore")

# Handle multilabel targets
multilabel_binarizer = None
target_classes = []

if task_type == "multilabel_classification":
    if label_delimiter:
        # Target is a delimited string (e.g. "tag1;tag2;tag3")
        # Split into list of labels per row
        y_raw = df_full[target_col].astype(str).apply(lambda x: x.split(label_delimiter) if x else [])
        mlb = MultiLabelBinarizer()
        y = mlb.fit_transform(y_raw)
        multilabel_binarizer = mlb
        target_classes = mlb.classes_.tolist()
    elif label_columns:
        # Multiple binary columns represent labels
        y = df_full[label_columns].values.astype(int)
        multilabel_binarizer = "identity"  # marker for inference
        target_classes = label_columns
    else:
        # Fallback: treat as single-label
        y = df_full[target_col]
        if not pd.api.types.is_numeric_dtype(y):
            le_target = LabelEncoder()
            y = pd.Series(le_target.fit_transform(y.astype(str)))
            target_classes = [str(c) for c in le_target.classes_]
else:
    # Single-label classification or regression
    y = df_full[target_col]
    if task_type != "regression" and not pd.api.types.is_numeric_dtype(y):
        le_target = LabelEncoder()
        y = pd.Series(le_target.fit_transform(y.astype(str)))
        target_classes = [str(c) for c in le_target.classes_]

# Fit preprocessor (for inference pipeline — no resampling)
X_transformed = preprocessor.fit_transform(df)

# Save preprocessor (inference-safe: no resampler)
os.makedirs(artifacts_dir, exist_ok=True)
preprocessor_path = os.path.join(artifacts_dir, "preprocessor.pkl")
joblib.dump(preprocessor, preprocessor_path)

# Save multilabel binarizer if multilabel
multilabel_binarizer_path = ""
if task_type == "multilabel_classification" and multilabel_binarizer is not None:
    multilabel_binarizer_path = os.path.join(artifacts_dir, "multilabel_binarizer.pkl")
    joblib.dump(multilabel_binarizer, multilabel_binarizer_path)

# Build TRAINING pipeline with resampler (imblearn.pipeline.Pipeline)
# This is used by downstream agents for CV — resampling happens INSIDE each fold.
# The resampler is NEVER saved to preprocessor.pkl.
# Note: SMOTE not directly applicable to multilabel; skip resampling for multilabel
if task_type != "regression" and task_type != "multilabel_classification" and imbalance_strategy in ("smote", "smote_tomek"):
    if imbalance_strategy == "smote":
        # Guard: SMOTE needs k_neighbors < minority class count
        # If minority is too small (< 6), fall back to class_weight
        try:
            minority_count = int(pd.Series(y).value_counts().min())
            if minority_count < 6:
                resampler = "fallback_class_weight"
            else:
                resampler = SMOTE(k_neighbors=min(5, minority_count - 1), random_state=42)
        except Exception:
            resampler = "fallback_class_weight"
    else:  # smote_tomek
        try:
            minority_count = int(pd.Series(y).value_counts().min())
            if minority_count < 6:
                resampler = "fallback_class_weight"
            else:
                resampler = SMOTETomek(random_state=42)
        except Exception:
            resampler = "fallback_class_weight"

    # Training pipeline with resampler (for downstream CV use)
    if resampler != "fallback_class_weight":
        training_pipeline = ImbPipeline([
            ("prep", preprocessor),
            ("resample", resampler),
        ])
        # Save training pipeline for reference (not used for inference)
        training_pipeline_path = os.path.join(artifacts_dir, "training_pipeline.pkl")
        joblib.dump(training_pipeline, training_pipeline_path)
    else:
        # Fallback: use preprocessor only, class_weight handled by model
        training_pipeline_path = ""
else:
    training_pipeline_path = ""

# Save processed dataset for next agents (preprocessed ONLY, no resampling)
# Downstream agents will use training_pipeline for CV if it exists
feature_names = preprocessor.get_feature_names_out().tolist() if hasattr(preprocessor, "get_feature_names_out") else [f"f_{{i}}" for i in range(X_transformed.shape[1])]
df_processed = pd.DataFrame(X_transformed, columns=feature_names)

# For multilabel, store the BINARIZED matrix rows (fixed-width 0/1 vectors) as JSON —
# NOT the variable-length label-name lists, which np.array() can't stack into a 2-D
# array downstream. The saved MultiLabelBinarizer maps positions back to label names.
if task_type == "multilabel_classification":
    df_processed["__target__"] = [json.dumps([int(v) for v in row]) for row in y]
else:
    df_processed["__target__"] = y.values

processed_path = os.path.join(artifacts_dir, "processed.csv")
df_processed.to_csv(processed_path, index=False)

# Exact transformed-name → raw-column map (for the trust gate's leak→raw mapping;
# prefix parsing mis-maps when category VALUES contain underscores). Built from the
# fitted ColumnTransformer itself, so it is exact by construction.
feature_source_map = {{}}
try:
    for tname, trans, tcols in preprocessor.transformers_:
        if tname == "remainder" or trans == "drop":
            continue
        if isinstance(tcols, str):          # tfidf: single string column selector
            in_cols = [tcols]
        else:
            in_cols = list(tcols)
        enc = None
        if hasattr(trans, "named_steps"):
            enc = trans.named_steps.get("enc")
        if enc is not None and hasattr(enc, "categories_"):   # one-hot: per-column category fan-out
            for col, cats in zip(in_cols, enc.categories_):
                n_cats = len(cats)
                if hasattr(enc, "infrequent_categories_"):
                    # max_categories collapses infrequents into one output
                    try:
                        infreq = enc.infrequent_categories_[in_cols.index(col)]
                        if infreq is not None and len(infreq) > 0:
                            n_cats = n_cats - len(infreq) + 1
                    except Exception:
                        pass
                for cat in cats:
                    feature_source_map[f"{{tname}}__{{col}}_{{cat}}"] = col
                feature_source_map[f"{{tname}}__{{col}}_infrequent_sklearn"] = col
        elif len(in_cols) == 1 and not hasattr(trans, "named_steps"):
            # tfidf: every output of this transformer comes from the one text column
            try:
                for out_name in trans.get_feature_names_out():
                    feature_source_map[f"{{tname}}__{{out_name}}"] = in_cols[0]
            except Exception:
                pass
        else:                                # num / ordinal: 1:1 name passthrough
            for col in in_cols:
                feature_source_map[f"{{tname}}__{{col}}"] = col
    # frequency-encoded columns were renamed col → col_freq before the CT
    for c in freq_cols:
        feature_source_map[f"num__{{c}}_freq"] = c
except Exception:
    feature_source_map = {{}}

feature_source_map_path = os.path.join(artifacts_dir, "feature_source_map.json")
with open(feature_source_map_path, "w") as fh:
    json.dump(feature_source_map, fh)

RESULT = {{
    "preprocessor_path": preprocessor_path,
    "training_pipeline_path": training_pipeline_path,
    "processed_path": processed_path,
    "n_features_out": X_transformed.shape[1],
    "n_samples": X_transformed.shape[0],
    "feature_names_sample": feature_names[:20],
    "dropped_columns": drop_existing,
    "datetime_cols_expanded": datetime_cols,
    "target_classes": target_classes,
    "imbalance_strategy": imbalance_strategy,
    "resampler_used": "smote" if imbalance_strategy == "smote" else ("smote_tomek" if imbalance_strategy == "smote_tomek" else "none"),
    "multilabel_binarizer_path": multilabel_binarizer_path,
    "label_columns": label_columns,
    "label_delimiter": label_delimiter,
    "feature_source_map_path": feature_source_map_path,
}}
'''


class PreprocessorAgent(BaseAgent):
    name = "preprocessor"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(run_id, "Building sklearn preprocessing pipeline — decisions per column...")

        audit = state.get("data_audit", {})
        eda = state.get("eda_insights", {})
        issues = state.get("prioritized_issues", [])

        user_message = f"""
Task: {state['task_type']}
Target: {state['target_column']}
Primary metric: {state['primary_metric']}

Null percentages: {json.dumps(dict(sorted(audit.get('null_pct', {}).items(), key=lambda x: -x[1])[:15]))}
Numeric columns: {json.dumps(eda.get('num_cols', [])[:20])}
Categorical columns: {json.dumps(eda.get('cat_cols', [])[:20])}
High cardinality categoricals: {json.dumps(eda.get('high_cardinality_cols', {}))}
Skewed columns: {json.dumps({k:v for k,v in eda.get('skewness', {}).items() if abs(v) > 1})}
Outlier pct (top): {json.dumps(dict(sorted(eda.get('outlier_pct', {}).items(), key=lambda x:-x[1])[:8]))}
Class imbalance: {json.dumps(eda.get('class_imbalance'))}
Datetime columns: {json.dumps(eda.get('datetime_cols', []))}
Free-text columns (use text_tfidf for these): {json.dumps(eda.get('text_cols', []))}

Prioritized issues from EDA:
{chr(10).join(f'- {i}' for i in issues[:8])}

Design the preprocessing pipeline. For each column specify the strategy and reasoning.
Use RobustScaler if significant outliers detected. Use median imputation for skewed numerics.
"""
        response = await self.llm.complete_json(SYSTEM_PROMPT, user_message)

        # Boundary validation: the LLM's JSON is untrusted — normalize every field
        # once so downstream template rendering + iteration can't detonate.
        if not isinstance(response, dict):
            response = {}
        decisions = self.norm_dicts(response.get("decisions"))
        drop_columns = self.norm_str_list(response.get("drop_columns"))
        imputation = self.norm_str_map(response.get("imputation_strategy"))
        encoding = self.norm_str_map(response.get("encoding_strategy"))
        scaling = self.norm_choice(response.get("scaling_strategy"),
                                   {"standard", "minmax", "robust", "none"}, "standard")
        handle_imbalance = bool(response.get("handle_imbalance", False))
        imbalance_strategy = self.norm_choice(response.get("imbalance_strategy"),
                                              {"class_weight", "smote", "smote_tomek", "none"}, "none")

        # Infer default imputer for numeric cols
        skewed = {k: v for k, v in eda.get("skewness", {}).items() if abs(v) > 0.5}
        num_imputer_default = "median" if skewed else "mean"

        from app.config import settings as _settings
        _text_embed_enabled = bool(getattr(_settings, "TEXT_EMBED_ENABLED", False))

        # Repairable decision params — the LLM may revise these on failure.
        # Fixed params (target, datetime cols, etc.) are closed over in render().
        def render(p: dict) -> str:
            return PREPROCESSING_CODE_TEMPLATE.format(
                target_column=state["target_column"],
                task_type=state["task_type"],
                drop_columns=repr(p["drop_columns"]),
                exclude_cols=repr(state.get("exclude_columns", [])),
                datetime_cols=repr(eda.get("datetime_cols", [])),
                imputation_strategy=repr(p["imputation"]),
                num_imputer_default=num_imputer_default,
                encoding_strategy=repr(p["encoding"]),
                scaling_strategy=p["scaling"],
                imbalance_strategy=p["imbalance_strategy"],
                label_columns=repr(state.get("label_columns", [])),
                label_delimiter=state.get("label_delimiter", ""),
                use_text_embed=repr(_text_embed_enabled),
            )

        repairable = {
            "drop_columns": drop_columns,
            "imputation": imputation,
            "encoding": encoding,
            "scaling": scaling,
            "imbalance_strategy": imbalance_strategy,
        }
        result = await self.execute_code_with_repair(
            run_id, render, repairable,
            repair_goal=(
                "Build a sklearn ColumnTransformer. drop_columns=list of columns to drop; "
                "imputation={col: median|mean|most_frequent|constant|knn}; "
                "encoding={col: onehot|ordinal|frequency|text_tfidf}; "
                "scaling=standard|minmax|robust|none. Fix errors like unknown columns, "
                "wrong encoder for a column type, or text columns sent to onehot."
            ),
            timeout=180,
        )
        if not result["success"]:
            # E3: template + param-repair exhausted → let the agent WRITE its own fix.
            eda = state.get("eda_insights", {})
            tags = ["preprocessor"]
            if eda.get("text_cols"): tags.append("has_text")
            if eda.get("datetime_cols"): tags.append("has_datetime")
            if state.get("task_type") == "multilabel_classification": tags.append("multilabel")
            failed_code = render(result.get("final_params", repairable))
            agentic = await self.execute_code_agentic(
                run_id, failed_code, result["error"], agent_role="preprocessor",
                task_type=state.get("task_type", "unknown"), tags=tags,
                goal=("Preprocess dataset_path into processed.csv (features + a '__target__' column) "
                      "and save a fitted sklearn preprocessor to artifacts_dir/preprocessor.pkl."),
                result_keys=["preprocessor_path", "processed_path", "n_features_out",
                             "n_samples", "dropped_columns"],
                timeout=180,
            )
            if not agentic["success"]:
                await self._mark_step(run_id, "failed", agentic["error"])
                return {"error": f"Preprocessing failed: {agentic['error']}", "status": "failed"}
            result = agentic

        # Adopt any repaired params so downstream logging reflects what actually ran
        final_params = result.get("final_params", repairable)
        scaling = final_params.get("scaling", scaling)
        code = render(final_params)  # the exact code that ran (post-repair) — for the notebook cell
        data = self.result_dict(result)
        # Contract guard: without these the pipeline cannot continue — fail the
        # step cleanly (never a KeyError downstream).
        if not data.get("processed_path") or not data.get("preprocessor_path"):
            msg = "Preprocessing produced no processed_path/preprocessor_path (malformed sandbox RESULT)"
            await self._mark_step(run_id, "failed", msg)
            return {"error": msg, "status": "failed"}
        data.setdefault("n_features_out", 0)
        data.setdefault("n_samples", 0)
        data.setdefault("dropped_columns", [])

        decision_log_entries = []
        for d in decisions:
            entry = await self._log_decision(
                run_id=run_id,
                decision=f"Column '{d.get('column')}': {d.get('strategy')}",
                reasoning=d.get("reasoning", ""),
                code_executed=f"sklearn strategy: {d.get('sklearn_step')}",
                result_summary=f"n_features_out={data['n_features_out']}",
            )
            decision_log_entries.append(entry)

        from app.core import mlflow_tracker as mlflow
        mlflow.log_params({
            "scaling_strategy": scaling,
            "n_features_after_preprocessing": data["n_features_out"],
            "handle_imbalance": handle_imbalance,
            "imbalance_strategy": data.get("imbalance_strategy", "none"),
            "resampler_used": data.get("resampler_used", "none"),
        })

        await self.emit(
            run_id,
            f"Preprocessing complete: {data['n_samples']} samples × {data['n_features_out']} features (imbalance: {data.get('imbalance_strategy', 'none')})",
            {"n_features": data["n_features_out"], "dropped_columns": data["dropped_columns"], "imbalance_strategy": data.get("imbalance_strategy", "none")},
        )
        await self._mark_step(run_id, "completed")

        existing_log = state.get("decision_log", [])
        existing_cells = state.get("notebook_cells", [])
        new_cell = {
            "agent": self.name,
            "title": "Data Preprocessing Pipeline",
            "iteration": 0,
            "code": code,
            "stdout": result.get("stdout", ""),
            "result_summary": {
                "n_samples": data["n_samples"],
                "n_features_out": data["n_features_out"],
                "feature_names_sample": data.get("feature_names_sample", [])[:15],
                "dropped_columns": data["dropped_columns"],
                "datetime_cols_expanded": data.get("datetime_cols_expanded", []),
                "scaling_strategy": scaling,
                "handle_imbalance": handle_imbalance,
                "imbalance_strategy": data.get("imbalance_strategy", "none"),
                "resampler_used": data.get("resampler_used", "none"),
                "per_column_decisions": [
                    {"column": d.get("column"), "strategy": d.get("strategy"),
                     "reasoning": d.get("reasoning", "")[:100]}
                    for d in decisions[:10]
                ],
            },
        }
        return {
            "preprocessor_path": data["preprocessor_path"],
            "training_pipeline_path": data.get("training_pipeline_path", ""),
            "preprocessing_decisions": decisions,
            "processed_data_path": data["processed_path"],
            "target_classes": data.get("target_classes", []),
            # imbalance_strategy must reach model_selector/tuner/feature_engineer (Phase C)
            "imbalance_strategy": data.get("imbalance_strategy", "none"),
            "resampler_used": data.get("resampler_used", "none"),
            "multilabel_binarizer_path": data.get("multilabel_binarizer_path", ""),
            "feature_source_map_path": data.get("feature_source_map_path", ""),
            "label_columns": data.get("label_columns", []),
            "label_delimiter": data.get("label_delimiter", ""),
            "decision_log": existing_log + decision_log_entries,
            "notebook_cells": existing_cells + [new_cell],
        }
