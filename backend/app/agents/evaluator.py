from typing import Any

from app.agents.base_agent import BaseAgent
from app.core import metric_registry
from app.core.state import AgentState

EVALUATION_CODE_TEMPLATE = '''
import pandas as pd
import numpy as np
import joblib
import json
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings("ignore")

from sklearn.model_selection import train_test_split, cross_val_predict, StratifiedKFold, KFold
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score, roc_auc_score,
    average_precision_score, precision_recall_curve, roc_curve,
    confusion_matrix, classification_report, mean_absolute_error,
    mean_squared_error, r2_score, hamming_loss, log_loss
)
from sklearn.calibration import calibration_curve

enriched_path = __ENRICHED_PATH__
tuned_path = __TUNED_PATH__
task_type = __TASK_TYPE__
primary_metric = __PRIMARY_METRIC__
fp_fn_preference = __FP_FN_PREFERENCE__
holdout_path = __HOLDOUT_PATH__
preprocessor_path = __PREPROCESSOR_PATH__
engineered_features = __ENGINEERED_FEATURES__
target_classes = __TARGET_CLASSES__
target_column = __TARGET_COLUMN__
label_columns = __LABEL_COLUMNS__
label_delimiter = __LABEL_DELIMITER__
mlb_path = __MLB_PATH__

# enriched.csv is TRAIN-ONLY — the holdout was carved from raw data before any
# agent fit/selected/tuned, so scoring on it is a true generalization estimate.
df = pd.read_csv(enriched_path)

def _parse_target(frame):
    if task_type == "multilabel_classification":
        import ast
        yr = frame["__target__"].apply(lambda x: ast.literal_eval(x) if isinstance(x, str) else x)
        return np.array(yr.tolist())
    return frame["__target__"].values

y_tr = _parse_target(df)
X_tr = df.drop(columns=["__target__"])
X = X_tr
model = joblib.load(tuned_path)


def _build_holdout():
    """Transform the RAW, never-seen holdout EXACTLY as inference does
    (api/routes/inference.py): align → preprocess → reproduce engineered
    features from raw columns → align to the trained feature space."""
    raw = pd.read_csv(holdout_path)
    # --- target: encode identically to how training built __target__ ---
    if task_type == "multilabel_classification":
        if label_columns:
            lc = [c for c in label_columns if c in raw.columns]
            raw = raw.dropna(subset=lc)
            yte = raw[lc].values.astype(int)
            raw = raw.drop(columns=lc)
        else:
            mlb = joblib.load(mlb_path)
            raw = raw.dropna(subset=[target_column])
            yy = raw[target_column].astype(str).apply(lambda v: v.split(label_delimiter) if v else [])
            yte = mlb.transform(yy)
            raw = raw.drop(columns=[target_column])
    else:
        raw = raw.dropna(subset=[target_column])
        traw = raw[target_column]
        raw = raw.drop(columns=[target_column])
        if task_type == "regression":
            yte = pd.to_numeric(traw, errors="coerce").values
        else:
            cls = list(target_classes) if target_classes else sorted(traw.astype(str).unique())
            idx = {c: i for i, c in enumerate(cls)}
            yte = traw.astype(str).map(idx).values
    # --- features: preprocess + engineered, exactly like inference ---
    X_raw = raw.copy()
    Xh = raw.copy()
    pre = joblib.load(preprocessor_path)
    if hasattr(pre, "feature_names_in_"):
        expected = list(pre.feature_names_in_)
        for c in expected:
            if c not in Xh.columns:
                Xh[c] = np.nan
        Xh = Xh[expected]
    for c in Xh.columns:
        if Xh[c].dtype == object:
            Xh[c] = Xh[c].fillna("")
    Xt = pre.transform(Xh)
    try:
        names = list(pre.get_feature_names_out())
    except Exception:
        names = ["f_" + str(i) for i in range(Xt.shape[1])]
    Xt = pd.DataFrame(Xt, columns=names)
    feat_repro_warnings = []
    for feat in engineered_features:
        fill = feat.get("fill_value", 0.0)
        try:
            ctx = {col: X_raw[col] for col in X_raw.columns}
            ctx.update({"pd": pd, "np": np, "df": X_raw})
            colv = eval(feat["formula"], {"__builtins__": {}}, ctx)
            colv = pd.to_numeric(pd.Series(colv).reset_index(drop=True), errors="coerce")
            n_bad = int(colv.isna().sum()) + int(np.isinf(colv.fillna(0)).sum())
            colv = colv.replace([np.inf, -np.inf], np.nan).fillna(fill)
            # Skew sentinel: compare against the TRAINING distribution of this
            # feature — a big shift means the model sees values it never trained
            # on (train/serve skew), which silently destroys holdout scores.
            if feat["name"] in X_tr.columns:
                tr_col = pd.to_numeric(X_tr[feat["name"]], errors="coerce").dropna()
                if len(tr_col) > 10 and colv.notna().any():
                    tr_std = float(tr_col.std()) or 1.0
                    shift = abs(float(colv.mean()) - float(tr_col.mean())) / tr_std
                    if shift > 3.0:
                        feat_repro_warnings.append(
                            f"{feat['name']}: holdout distribution shifted "
                            f"{shift:.1f} sigma vs training (train/serve skew)")
            if n_bad > 0.5 * len(Xt):
                feat_repro_warnings.append(
                    f"{feat['name']}: {n_bad}/{len(Xt)} holdout values unparseable — filled with {fill}")
        except Exception as _fe_err:
            # NEVER silent: a feature that fails to reproduce at scoring time is a
            # train/serve skew bug — the score is only honest if we report it.
            feat_repro_warnings.append(
                f"{feat['name']}: formula failed on holdout ({type(_fe_err).__name__}: {str(_fe_err)[:120]}) "
                f"— entire column filled with constant {fill}")
            colv = pd.Series([fill] * len(Xt))
        Xt[feat["name"]] = colv.values
    for w in feat_repro_warnings:
        print("ENGINEERED_FEATURE_SKEW:", w)
    globals()["_FEAT_REPRO_WARNINGS"] = feat_repro_warnings
    return Xt.reindex(columns=list(X_tr.columns), fill_value=0), yte


evaluation_basis = "holdout"
try:
    if not holdout_path:
        raise ValueError("no holdout reserved")
    # Fit on ALL training rows; score the untouched holdout. No refit-on-subset.
    model.fit(X_tr, y_tr)
    X_test, y_test = _build_holdout()
    X_train, y_train = X_tr, y_tr
    if len(X_test) == 0:
        raise ValueError("empty holdout after target dropna")
except Exception as _he:
    # Degrade to the legacy in-sample split rather than crash the run.
    evaluation_basis = "in_sample_split"
    print("HOLDOUT_FALLBACK:", repr(_he))
    if task_type == "multilabel_classification":
        X_train, X_test, y_train, y_test = train_test_split(
            X_tr, y_tr, test_size=0.2, random_state=42)
    else:
        X_train, X_test, y_train, y_test = train_test_split(
            X_tr, y_tr, test_size=0.2, random_state=42,
            stratify=y_tr if task_type != "regression" else None)
    model.fit(X_train, y_train)

y_pred = model.predict(X_test)

metrics = {}
# Curve DATA (not just PNGs) so the UI can draw interactive, theme-aware charts
# that adapt to the task type. Downsampled to keep the payload small.
curves = {}
def _ds(arr, n=60):
    a = [float(v) for v in list(arr)]
    if len(a) <= n:
        return [round(v, 5) for v in a]
    step = len(a) / n
    return [round(a[int(i * step)], 5) for i in range(n)]
os.makedirs(artifacts_dir, exist_ok=True)

if task_type == "regression":
    mae = mean_absolute_error(y_test, y_pred)
    mse = mean_squared_error(y_test, y_pred)
    rmse = np.sqrt(mse)
    r2 = r2_score(y_test, y_pred)
    metrics = {"mae": round(mae,4), "mse": round(mse,4), "rmse": round(rmse,4), "r2": round(r2,4)}

    # Residual plot
    residuals = y_test - y_pred
    fig, ax = plt.subplots(figsize=(8,5))
    ax.scatter(y_pred, residuals, alpha=0.3, s=10)
    ax.axhline(0, color="red", linestyle="--")
    ax.set_xlabel("Predicted"); ax.set_ylabel("Residual")
    ax.set_title("Residual Plot")
    plt.tight_layout()
    fig.savefig(os.path.join(artifacts_dir, "residual_plot.png"), dpi=100)
    plt.close()

    final_score = rmse if primary_metric == "rmse" else (mae if primary_metric == "mae" else r2)
    recommended_threshold = None
    calibration_result = None

    # Predicted-vs-actual + residuals for the UI (sorted by actual for a clean line).
    try:
        order = np.argsort(y_test)
        curves["regression"] = {
            "y_true": _ds(np.asarray(y_test)[order]),
            "y_pred": _ds(np.asarray(y_pred)[order]),
            "residuals": _ds(np.asarray(residuals)[order]),
        }
    except Exception:
        pass

elif task_type == "multilabel_classification":
    # Multilabel metrics
    # Per-label metrics
    n_labels = y_test.shape[1]
    per_label_prec = precision_score(y_test, y_pred, average=None, zero_division=0)
    per_label_rec = recall_score(y_test, y_pred, average=None, zero_division=0)
    per_label_f1 = f1_score(y_test, y_pred, average=None, zero_division=0)
    
    # Average metrics
    metrics["f1_micro"] = round(f1_score(y_test, y_pred, average="micro", zero_division=0), 4)
    metrics["f1_macro"] = round(f1_score(y_test, y_pred, average="macro", zero_division=0), 4)
    metrics["f1_samples"] = round(f1_score(y_test, y_pred, average="samples", zero_division=0), 4)
    metrics["precision_micro"] = round(precision_score(y_test, y_pred, average="micro", zero_division=0), 4)
    metrics["precision_macro"] = round(precision_score(y_test, y_pred, average="macro", zero_division=0), 4)
    metrics["recall_micro"] = round(recall_score(y_test, y_pred, average="micro", zero_division=0), 4)
    metrics["recall_macro"] = round(recall_score(y_test, y_pred, average="macro", zero_division=0), 4)
    metrics["hamming_loss"] = round(hamming_loss(y_test, y_pred), 4)
    # Subset accuracy (exact match)
    subset_acc = accuracy_score(y_test, y_pred)
    metrics["subset_accuracy"] = round(subset_acc, 4)
    
    # Per-label details for reporting
    metrics["per_label"] = {
        "precision": [round(float(p), 4) for p in per_label_prec],
        "recall": [round(float(r), 4) for r in per_label_rec],
        "f1": [round(float(f), 4) for f in per_label_f1],
    }
    
    recommended_threshold = None
    calibration_result = None
    
    # Per-label metrics grid plot (instead of confusion matrix)
    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    label_names = [str(i) for i in range(n_labels)]
    x = np.arange(n_labels)
    width = 0.25
    axes[0].bar(x - width, per_label_prec, width, label='Precision', color='#c8a96e')
    axes[0].bar(x, per_label_rec, width, label='Recall', color='#5fb3a1')
    axes[0].bar(x + width, per_label_f1, width, label='F1', color='#e9e4d8')
    axes[0].set_xlabel('Label Index')
    axes[0].set_ylabel('Score')
    axes[0].set_title('Per-Label Metrics')
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(label_names, rotation=45, ha='right')
    axes[0].legend()
    axes[0].grid(True, alpha=0.3)
    
    # Hamming loss and subset accuracy as text
    axes[1].text(0.5, 0.6, f'Subset Accuracy: {subset_acc:.4f}', ha='center', va='center', fontsize=14, transform=axes[1].transAxes)
    axes[1].text(0.5, 0.4, f'Hamming Loss: {metrics["hamming_loss"]:.4f}', ha='center', va='center', fontsize=14, transform=axes[1].transAxes)
    axes[1].set_title('Multilabel Summary')
    axes[1].axis('off')
    
    # Average metrics
    avg_metrics = ['f1_micro', 'f1_macro', 'f1_samples', 'precision_micro', 'precision_macro', 'recall_micro', 'recall_macro']
    avg_vals = [metrics[m] for m in avg_metrics]
    axes[2].barh(avg_metrics, avg_vals, color='#c8a96e')
    axes[2].set_xlabel('Score')
    axes[2].set_title('Average Metrics')
    axes[2].grid(True, alpha=0.3)
    
    plt.tight_layout()
    fig.savefig(os.path.join(artifacts_dir, "multilabel_metrics.png"), dpi=100)
    plt.close()

    # final_score for multilabel — pick the chosen metric (default f1_macro).
    # hamming_loss is "lower is better", so report its complement for a unified
    # "higher is better" final_score.
    if primary_metric == "hamming_loss":
        final_score = round(1.0 - metrics["hamming_loss"], 4)
    else:
        final_score = metrics.get(primary_metric, metrics.get("f1_macro", subset_acc))

else:
    acc = accuracy_score(y_test, y_pred)
    prec = precision_score(y_test, y_pred, average="weighted", zero_division=0)
    rec = recall_score(y_test, y_pred, average="weighted", zero_division=0)
    f1 = f1_score(y_test, y_pred, average="weighted", zero_division=0)

    metrics = {
        "accuracy": round(acc,4), "precision": round(prec,4),
        "recall": round(rec,4), "f1": round(f1,4),
    }

    try:
        y_proba = model.predict_proba(X_test)
        # Log loss (cross-entropy) — works for binary + multiclass from the full
        # probability matrix; "lower is better".
        try:
            metrics["log_loss"] = round(float(log_loss(y_test, y_proba)), 4)
        except Exception:
            pass
        if task_type == "binary_classification":
            y_proba_pos = y_proba[:, 1]
            auc = roc_auc_score(y_test, y_proba_pos)
            metrics["auc_roc"] = round(auc,4)

            # PR-AUC (average_precision) — primary metric for severe imbalance
            pr_auc = average_precision_score(y_test, y_proba_pos)
            metrics["pr_auc"] = round(pr_auc,4)

            # Threshold selection based on FP/FN preference
            thresholds = np.arange(0.1, 0.9, 0.05)
            best_thresh = 0.5
            if "recall" in fp_fn_preference.lower() or "fn" in fp_fn_preference.lower():
                # Maximize recall at acceptable precision
                best_thresh = float(thresholds[np.argmax([
                    recall_score(y_test, (y_proba_pos >= t).astype(int), zero_division=0)
                    for t in thresholds
                ])])
            elif "precision" in fp_fn_preference.lower() or "fp" in fp_fn_preference.lower():
                best_thresh = float(thresholds[np.argmax([
                    precision_score(y_test, (y_proba_pos >= t).astype(int), zero_division=0)
                    for t in thresholds
                ])])
            else:
                # F1-maximising threshold
                best_thresh = float(thresholds[np.argmax([
                    f1_score(y_test, (y_proba_pos >= t).astype(int), zero_division=0)
                    for t in thresholds
                ])])

            recommended_threshold = best_thresh
            y_pred_final = (y_proba_pos >= best_thresh).astype(int)
            metrics["recall_at_threshold"] = round(recall_score(y_test, y_pred_final, zero_division=0), 4)
            metrics["precision_at_threshold"] = round(precision_score(y_test, y_pred_final, zero_division=0), 4)

            # Calibration
            fraction_pos, mean_pred = calibration_curve(y_test, y_proba_pos, n_bins=10)
            calibration_error = float(np.mean(np.abs(fraction_pos - mean_pred)))
            calibration_result = {"mean_calibration_error": round(calibration_error, 4),
                                    "well_calibrated": bool(calibration_error < 0.05)}

            # ROC + PR curve DATA for the UI (downsampled).
            try:
                fpr, tpr, _ = roc_curve(y_test, y_proba_pos)
                curves["roc"] = {"fpr": _ds(fpr), "tpr": _ds(tpr), "auc": round(float(auc), 4)}
            except Exception:
                pass

            # Precision-Recall curve plot
            precision_vals, recall_vals, _ = precision_recall_curve(y_test, y_proba_pos)
            try:
                curves["pr"] = {"precision": _ds(precision_vals), "recall": _ds(recall_vals), "ap": round(float(pr_auc), 4)}
            except Exception:
                pass
            fig, ax = plt.subplots(figsize=(6,5))
            ax.plot(recall_vals, precision_vals, linewidth=2, color="#c8a96e")
            ax.set_xlabel("Recall"); ax.set_ylabel("Precision")
            ax.set_title("Precision-Recall Curve (PR-AUC = {:.3f})".format(pr_auc))
            ax.grid(True, alpha=0.3)
            plt.tight_layout()
            fig.savefig(os.path.join(artifacts_dir, "precision_recall_curve.png"), dpi=100)
            plt.close()
        else:
            auc = roc_auc_score(y_test, y_proba, multi_class="ovr", average="weighted")
            metrics["auc_roc_weighted"] = round(auc,4)
            recommended_threshold = None
            calibration_result = None
    except Exception:
        recommended_threshold = 0.5
        calibration_result = None

    # Confusion matrix plot (for single-label classification only)
    cm = confusion_matrix(y_test, y_pred)
    # Emit the matrix as data; for binary, also expose tn/fp/fn/tp metrics.
    try:
        curves["confusion"] = cm.tolist()
        if cm.shape == (2, 2):
            _tn, _fp, _fn, _tp = cm.ravel()
            metrics["tn"] = int(_tn); metrics["fp"] = int(_fp)
            metrics["fn"] = int(_fn); metrics["tp"] = int(_tp)
    except Exception:
        pass
    fig, ax = plt.subplots(figsize=(6,5))
    import seaborn as sns
    sns.heatmap(cm, annot=True, fmt="d", ax=ax, cmap="Blues")
    ax.set_title("Confusion Matrix"); ax.set_ylabel("True"); ax.set_xlabel("Predicted")
    plt.tight_layout()
    fig.savefig(os.path.join(artifacts_dir, "confusion_matrix.png"), dpi=100)
    plt.close()

    metric_map = dict(
        auc_roc=metrics.get("auc_roc", metrics.get("auc_roc_weighted", acc)),
        pr_auc=metrics.get("pr_auc", acc),
        recall=rec, precision=prec, f1=f1, accuracy=acc,
        f1_micro=metrics.get("f1_micro", acc),
        f1_macro=metrics.get("f1_macro", acc),
        f1_samples=metrics.get("f1_samples", acc),
        hamming_loss=metrics.get("hamming_loss", acc),
        subset_accuracy=metrics.get("subset_accuracy", acc),
    )
    final_score = metric_map.get(primary_metric, acc)

# SHAP analysis (skip for multilabel - MultiOutputClassifier not directly supported)
shap_top_features = []
if task_type != "multilabel_classification":
    try:
        import shap
        if hasattr(model, "predict_proba") or hasattr(model, "predict"):
            explainer = shap.TreeExplainer(model) if hasattr(model, "feature_importances_") \
                else shap.LinearExplainer(model, X_train)
            sample = X_test.iloc[:min(200, len(X_test))]
            shap_values = explainer.shap_values(sample)
            if isinstance(shap_values, list):
                shap_values = shap_values[1]
            feature_names = X.columns.tolist()
            mean_abs_shap = np.abs(shap_values).mean(axis=0)
            top_idx = np.argsort(mean_abs_shap)[::-1][:15]
            shap_top_features = [feature_names[i] for i in top_idx]

            # SHAP summary plot
            fig, ax = plt.subplots(figsize=(10, 6))
            shap.summary_plot(shap_values, sample, feature_names=feature_names,
                              max_display=15, show=False, plot_type="bar")
            plt.tight_layout()
            fig.savefig(os.path.join(artifacts_dir, "shap_summary.png"), dpi=100, bbox_inches="tight")
            plt.close()
    except Exception as e:
        shap_top_features = list(X.columns[:10])

# Slice analysis on top categorical column
slice_performance = {}
try:
    df_test = pd.DataFrame(X_test, columns=X.columns)
    df_test["__y_true__"] = y_test.tolist() if task_type == "multilabel_classification" else y_test
    df_test["__y_pred__"] = y_pred.tolist() if task_type == "multilabel_classification" else y_pred
    cat_cols = X.select_dtypes(exclude=["number"]).columns.tolist()
    if cat_cols:
        col = cat_cols[0]
        for val in df_test[col].value_counts().head(5).index:
            mask = df_test[col] == val
            if mask.sum() > 20:
                slice_y = df_test[mask]["__y_true__"]
                slice_pred = df_test[mask]["__y_pred__"]
                if task_type == "regression":
                    slice_performance[str(val)] = {"rmse": round(float(np.sqrt(mean_squared_error(slice_y, slice_pred))), 4)}
                elif task_type == "multilabel_classification":
                    # For multilabel slice, compute subset accuracy
                    slice_acc = accuracy_score(slice_y.tolist(), slice_pred.tolist())
                    slice_performance[str(val)] = {"subset_accuracy": round(float(slice_acc), 4), "n": int(mask.sum())}
                else:
                    slice_performance[str(val)] = {
                        "recall": round(float(recall_score(slice_y, slice_pred, average="weighted", zero_division=0)), 4),
                        "n": int(mask.sum()),
                    }
except Exception:
    pass

plots = ["shap_summary.png"] if task_type != "multilabel_classification" else ["multilabel_metrics.png"]
if task_type == "binary_classification":
    plots.append("precision_recall_curve.png")
elif task_type != "regression" and task_type != "multilabel_classification":
    plots.append("confusion_matrix.png")
elif task_type == "regression":
    plots = ["residual_plot.png", "shap_summary.png"]

# Iteration noise floor: CV std of the model on the training data. A later
# iteration's gain must clear this to count as real improvement (Phase 0.2).
score_std = 0.0
try:
    from sklearn.model_selection import cross_val_score
    if task_type in ("binary_classification", "multiclass_classification"):
        _noise_cv = StratifiedKFold(n_splits=3, shuffle=True, random_state=0)
    else:
        _noise_cv = KFold(n_splits=3, shuffle=True, random_state=0)
    _cvs = cross_val_score(model, X_tr, y_tr, cv=_noise_cv)
    score_std = float(np.std(_cvs))
except Exception:
    score_std = 0.0

RESULT = {
    "metrics": metrics,
    "curves": curves,
    "final_score": round(final_score, 4),
    "evaluation_basis": evaluation_basis,   # "holdout" | "in_sample_split"
    "score_std": round(score_std, 4),
    "recommended_threshold": float(recommended_threshold) if recommended_threshold else 0.5,
    "calibration": calibration_result,
    "shap_top_features": shap_top_features,
    "slice_performance": slice_performance,
    "plots": plots,
    # Train/serve skew sentinel (2026-07-03): engineered features that failed or
    # shifted when reproduced on the holdout — the honest-score health check.
    "feature_reproduction_warnings": globals().get("_FEAT_REPRO_WARNINGS", []),
}
'''


class EvaluatorAgent(BaseAgent):
    name = "evaluator"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(run_id, "Running full evaluation: metrics, slices, calibration, SHAP...")

        # Engineered-feature recipe (name/formula/fill_value) — reproduced on the
        # holdout exactly as inference does. Mirror api/routes/inference.py.
        engineered = [
            {"name": f["name"], "formula": f.get("formula"), "fill_value": f.get("fill_value", 0.0)}
            for f in state.get("features_created", []) if f.get("formula")
        ]

        # Token replacement (NOT .format) — dict/f-string heavy template, natural braces.
        code = (
            EVALUATION_CODE_TEMPLATE
            .replace("__ENRICHED_PATH__", repr(state.get("enriched_data_path", "")))
            .replace("__TUNED_PATH__", repr(state.get("tuned_model_path", state.get("winner_model_path", ""))))
            .replace("__TASK_TYPE__", repr(state["task_type"]))
            .replace("__PRIMARY_METRIC__", repr(state["primary_metric"]))
            .replace("__FP_FN_PREFERENCE__", repr(state.get("fp_fn_preference") or ""))
            .replace("__HOLDOUT_PATH__", repr(state.get("holdout_path", "")))
            .replace("__PREPROCESSOR_PATH__", repr(state.get("preprocessor_path", "")))
            .replace("__ENGINEERED_FEATURES__", repr(engineered))
            .replace("__TARGET_CLASSES__", repr(list(state.get("target_classes") or [])))
            .replace("__TARGET_COLUMN__", repr(state.get("target_column", "")))
            .replace("__LABEL_COLUMNS__", repr(list(state.get("label_columns") or [])))
            .replace("__LABEL_DELIMITER__", repr(state.get("label_delimiter", "")))
            .replace("__MLB_PATH__", repr(state.get("multilabel_binarizer_path", "")))
        )

        result = await self.execute_code(code, run_id, timeout=400)
        result = await self.try_agentic_repair(
            run_id, code, result,
            task_type=state.get("task_type", "unknown"),
            result_keys=["final_score", "metrics"],
            goal=("Evaluate the model at the tuned model path on enriched.csv (has a '__target__' "
                  "column): fit on the data, compute task-appropriate metrics, and set RESULT with "
                  "final_score (float, for the primary metric) and metrics (dict). Plots are optional."),
            timeout=400,
        )
        if not result["success"]:
            await self._mark_step(run_id, "failed", result["error"])
            return {"error": f"Evaluation failed: {result['error']}", "status": "failed"}

        data = result["result"]
        final_score = data["final_score"]
        metrics = data["metrics"]
        evaluation_basis = data.get("evaluation_basis", "holdout")
        score_std = data.get("score_std", 0.0)

        basis_text = (
            "Scored on a held-out test set carved from raw data BEFORE any "
            "fitting/selection/tuning — an unbiased generalization estimate."
            if evaluation_basis == "holdout" else
            "Dataset too small for a separate holdout — scored via an in-sample "
            "split (mildly optimistic; selection saw these rows)."
        )
        entry = await self._log_decision(
            run_id=run_id,
            decision=f"Final evaluation ({evaluation_basis}): {state['primary_metric']} = {final_score:.4f}",
            reasoning=(
                f"{basis_text} All metrics: {metrics}. "
                f"Score noise floor (CV std): {score_std}. "
                f"Threshold: {data.get('recommended_threshold')}. "
                f"Calibration: {data.get('calibration')}."
            ),
            result_summary=f"final_score={final_score:.4f} ({evaluation_basis})",
        )

        # Train/serve skew sentinel: engineered features that failed/shifted when
        # reproduced on the holdout make the holdout score dishonest — surface
        # loudly (decision log + live stream), never silently.
        repro_warnings = data.get("feature_reproduction_warnings") or []
        if isinstance(repro_warnings, list) and repro_warnings:
            await self._log_decision(
                run_id=run_id,
                decision=f"WARNING — {len(repro_warnings)} engineered feature(s) did not reproduce cleanly on the holdout",
                reasoning="Train/serve skew: the model trained on values these formulas produced on "
                          "train data, but holdout reproduction differed. The holdout score may be "
                          "understated. Details: " + " | ".join(str(w)[:160] for w in repro_warnings[:5]),
                result_summary="feature_reproduction_warnings",
            )
            await self.emit(run_id, f"⚠ Feature reproduction skew on holdout: {str(repro_warnings[0])[:140]}",
                            {"warnings": repro_warnings[:5]})

        from app.core import mlflow_tracker as mlflow
        mlflow.log_metrics({f"final_{k}": v for k, v in metrics.items() if isinstance(v, (int, float))})
        mlflow.log_metric("final_score", final_score)
        mlflow.log_dict(data.get("slice_performance", {}), "slice_analysis.json")

        import os
        artifact_dir = os.path.join("/data", run_id, "artifacts")
        for plot in data.get("plots", []):
            mlflow.log_artifact(os.path.join(artifact_dir, plot))

        # Persist the score NOW — if a later iteration crashes (e.g. LLM outage),
        # the run record still shows the best completed result.
        await self._update_run_field(
            run_id,
            final_score=final_score,
            iteration_count=state.get("iteration", 0) + 1,
        )

        scores = state.get("iteration_scores", []) + [final_score]
        await self.emit(
            run_id,
            f"Evaluation complete: {final_score:.4f} {state['primary_metric']} | SHAP top: {data['shap_top_features'][:3]}",
            {"final_score": final_score, "metrics": metrics},
        )
        await self._mark_step(run_id, "completed")

        existing_log = state.get("decision_log", [])
        existing_cells = state.get("notebook_cells", [])
        iteration = state.get("iteration", 0)
        new_cell = {
            "agent": self.name,
            "title": f"Model Evaluation — Iteration {iteration + 1}",
            "iteration": iteration + 1,
            "code": code,
            "stdout": result.get("stdout", ""),
            "result_summary": {
                "iteration": iteration + 1,
                "final_score": round(final_score, 4),
                "metric": state["primary_metric"],
                "all_metrics": metrics,
                "recommended_threshold": data.get("recommended_threshold", 0.5),
                "calibration": data.get("calibration"),
                "shap_top_features": data.get("shap_top_features", [])[:10],
                "slice_performance": data.get("slice_performance", {}),
                "baseline_score": round(state.get("baseline_score", 0.0), 4),
                # Directional: a POSITIVE number always means "better than baseline",
                # including for lower-is-better metrics (rmse/mae/log_loss).
                "improvement_vs_baseline": round(
                    metric_registry.directional_gain(
                        final_score, state.get("baseline_score", 0.0),
                        state.get("primary_metric", "")), 4
                ),
            },
        }
        return {
            "evaluation_report": {"metrics": metrics, "calibration": data.get("calibration"), "curves": data.get("curves")},
            "slice_performance": data.get("slice_performance", {}),
            "recommended_threshold": data.get("recommended_threshold", 0.5),
            "shap_top_features": data.get("shap_top_features", []),
            "current_score": final_score,
            "score_std": score_std,
            "evaluation_basis": evaluation_basis,
            "iteration_scores": scores,
            "decision_log": existing_log + [entry],
            "notebook_cells": existing_cells + [new_cell],
        }