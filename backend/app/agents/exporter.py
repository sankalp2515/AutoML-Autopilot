import json
import os
from typing import Any

from app.agents.base_agent import BaseAgent
from app.core import metric_registry
from app.core.state import AgentState

# ── Sandbox code: file I/O only, no string generation here ───────────────────
# All heavy content (model card, api code, notebook cells) is generated
# in Python agent scope and injected as repr() literals.
EXPORT_SANDBOX_CODE = '''
import os
import joblib
import nbformat
from nbformat.v4 import new_notebook, new_markdown_cell, new_code_cell

artifacts_dir_path = ARTIFACTS_DIR
os.makedirs(artifacts_dir_path, exist_ok=True)

tuned_path = TUNED_PATH
preprocessor_path = PREPROCESSOR_PATH
multilabel_binarizer_path = MULTILABEL_BINARIZER_PATH

# Save as a plain dict — custom classes defined in exec() can't be pickled
# (pickle needs to look up the class by module path, which doesn't exist for
# classes defined inside exec'd code). The generated API defines the wrapper.
preprocessor = joblib.load(preprocessor_path) if preprocessor_path and os.path.exists(preprocessor_path) else None
model = joblib.load(tuned_path) if tuned_path and os.path.exists(tuned_path) else None
# Multilabel: the binarizer decodes the binary prediction matrix back to label sets
multilabel_binarizer = (
    joblib.load(multilabel_binarizer_path)
    if multilabel_binarizer_path and os.path.exists(multilabel_binarizer_path) else None
)

pipeline_path = ""
if model:
    pipeline_data = {
        "preprocessor": preprocessor,
        "model": model,
        "threshold": THRESHOLD,
        "target_classes": TARGET_CLASSES,
        # task_type + binarizer let the inference layer decode predictions correctly
        # (single label vs. a SET of labels for multilabel).
        "task_type": TASK_TYPE,
        "multilabel_binarizer": multilabel_binarizer,
        # LLM-engineered features: the model was trained on preprocessed +
        # engineered columns, so inference MUST reproduce these formulas.
        "engineered_features": ENGINEERED_FEATURES,
        "version": "1.1",
    }
    pipeline_path = os.path.join(artifacts_dir_path, "inference_pipeline.pkl")
    joblib.dump(pipeline_data, pipeline_path)

# ── Write pre-generated text artifacts ──────────────────────────────────────
api_path = os.path.join(artifacts_dir_path, "api_main.py")
with open(api_path, "w") as f:
    f.write(API_CODE)

card_path = os.path.join(artifacts_dir_path, "model_card.md")
with open(card_path, "w") as f:
    f.write(MODEL_CARD)

# ── Notebook ─────────────────────────────────────────────────────────────────
# nb_cells is a list of {"type": "markdown"|"code", "source": "<text>"} dicts
nb_cells_data = NB_CELLS
nb = new_notebook()
for cell in nb_cells_data:
    if cell["type"] == "markdown":
        nb.cells.append(new_markdown_cell(cell["source"]))
    else:
        nb.cells.append(new_code_cell(cell["source"]))

nb_path = os.path.join(artifacts_dir_path, "pipeline.ipynb")
with open(nb_path, "w") as f:
    nbformat.write(nb, f)

try:
    import sklearn
    _fw_version = sklearn.__version__
except Exception:
    _fw_version = ""

RESULT = {
    "pipeline_path": pipeline_path,
    "api_path": api_path,
    "notebook_path": nb_path,
    "model_card_path": card_path,
    "sklearn_version": _fw_version,
}
'''

# ── LLM prompt for agentic notebook structure ─────────────────────────────────
NOTEBOOK_SYSTEM_PROMPT = """You are a senior ML engineer writing a Jupyter notebook that documents an automated ML pipeline run.

Your job: given the execution trace (what code ran, what results were produced at each step), decide the notebook structure and write the narrative.

The notebook is for DATA SCIENTISTS AND STAKEHOLDERS to understand:
1. What data we had and its quality
2. Why we made each preprocessing and modelling decision
3. What features were engineered and why they help
4. How the model compares to baseline
5. What the model learned (SHAP features)
6. How to use the model in production

IMPORTANT RULES:
- Adapt entirely to the problem type (classification vs regression, binary vs multiclass)
- Adapt to what ACTUALLY happened (if no features were kept, say that and explain why)
- Use specific numbers from the result summaries — never generic placeholders
- For classification: focus on confusion matrix, threshold tradeoffs, class balance
- For regression: focus on RMSE trends, residuals, R² interpretation
- If there were multiple iterations, explain how each improved the model
- The narrative should be readable by a non-technical stakeholder but precise enough for an engineer

RESPOND WITH VALID JSON:
{
  "notebook_title": "<descriptive title including model name and task>",
  "executive_summary": "<2-3 sentences: what problem, what approach, key result>",
  "sections": [
    {
      "agent_key": "<one of: data_auditor, baseline_builder, eda_agent, preprocessor, feature_engineer, model_selector, tuner, evaluator>",
      "iteration": <0 for one-time agents, N for per-iteration agents — must match exactly>,
      "heading": "## <number>. <section title>",
      "narrative_before": "<1-3 paragraphs BEFORE showing the code — explain what we are about to do and why>",
      "narrative_after": "<1-3 paragraphs AFTER the code — explain what we found and what decision we made>",
      "key_insights": ["<specific insight with numbers>", ...]
    }
  ],
  "conclusion": {
    "key_findings": ["<finding with specific number>", ...],
    "business_recommendations": ["<actionable recommendation>", ...],
    "next_steps": ["<concrete next step>", ...]
  }
}

Include ALL agents that have data. For iteration-based agents (feature_engineer, model_selector, tuner, evaluator), include one section per iteration with correct iteration number.
"""


def _build_api_code(run_id: str, winner_model: str, primary_metric: str, final_score: float) -> str:
    return f"""import joblib
import numpy as np
import pandas as pd
from fastapi import FastAPI
from pydantic import BaseModel
from typing import Any

app = FastAPI(title="AutoML Model API — run {run_id}")

# inference_pipeline.pkl stores a dict: preprocessor, model, threshold,
# target_classes (label decode), engineered_features (LLM feature formulas)
_data = joblib.load("inference_pipeline.pkl")
_preprocessor = _data["preprocessor"]
_model = _data["model"]
_threshold = _data.get("threshold", 0.5)
_target_classes = _data.get("target_classes") or []
_engineered = _data.get("engineered_features") or []


def _predict_raw(X_raw: pd.DataFrame):
    X = X_raw.copy()
    if _preprocessor is not None and hasattr(_preprocessor, "feature_names_in_"):
        for col in _preprocessor.feature_names_in_:
            if col not in X.columns:
                X[col] = np.nan
        X = X[list(_preprocessor.feature_names_in_)]
    if _preprocessor is not None:
        X_t = _preprocessor.transform(X)
        try:
            names = list(_preprocessor.get_feature_names_out())
        except Exception:
            names = [f"f_{{i}}" for i in range(X_t.shape[1])]
        X_t = pd.DataFrame(X_t, columns=names)
    else:
        X_t = X.reset_index(drop=True)
    # Reproduce engineered features the model was trained with
    for feat in _engineered:
        fill = feat.get("fill_value", 0.0)
        try:
            ctx = {{col: X_raw[col] for col in X_raw.columns}}
            ctx.update({{"pd": pd, "np": np, "df": X_raw}})
            col = eval(feat["formula"], {{"__builtins__": {{}}}}, ctx)
            col = pd.to_numeric(pd.Series(col).reset_index(drop=True), errors="coerce")
            col = col.replace([np.inf, -np.inf], np.nan).fillna(fill)
        except Exception:
            col = pd.Series([fill] * len(X_t))
        X_t[feat["name"]] = col.values
    return X_t


def _decode(label):
    try:
        i = int(label)
        if _target_classes and 0 <= i < len(_target_classes):
            return _target_classes[i]
    except (ValueError, TypeError):
        pass
    return label


class PredictRequest(BaseModel):
    features: dict[str, Any]


class PredictResponse(BaseModel):
    prediction: Any
    confidence: float | None = None
    threshold_used: float | None = None


@app.post("/predict", response_model=PredictResponse)
async def predict(request: PredictRequest) -> PredictResponse:
    X = pd.DataFrame([request.features])
    X_t = _predict_raw(X)
    confidence = None
    threshold_used = None
    if hasattr(_model, "predict_proba"):
        proba = _model.predict_proba(X_t)
        if proba.shape[1] == 2:
            confidence = float(proba[0, 1])
            threshold_used = _threshold
            prediction = _decode(int(confidence >= _threshold))
        else:
            classes = getattr(_model, "classes_", list(range(proba.shape[1])))
            i = int(np.argmax(proba[0]))
            confidence = float(proba[0, i])
            prediction = _decode(classes[i])
    else:
        prediction = float(_model.predict(X_t)[0])
    return PredictResponse(prediction=prediction, confidence=confidence, threshold_used=threshold_used)


@app.get("/health")
async def health():
    return {{"status": "ok", "model": "{winner_model}", "metric": "{primary_metric}", "score": {final_score}}}
"""


def _build_model_card(
    run_id: str,
    task_type: str,
    target_col: str,
    winner_model: str,
    primary_metric: str,
    final_score: float,
    baseline_score: float,
    recommended_threshold: float,
    top_features: list[str],
    best_params: dict,
) -> str:
    # Directional: positive ALWAYS means better, incl. lower-is-better metrics.
    # (A bare subtraction printed an rmse 3.5→0.9 win as "-2.6" in the customer's
    # model card and notebook — 2026-07-04 fix.)
    improvement = metric_registry.directional_gain(final_score, baseline_score, primary_metric)
    features_list = "\n".join(f"- {f}" for f in top_features[:10])
    params_json = json.dumps(best_params, indent=2)
    return f"""# Model Card — {winner_model}

**Run ID:** {run_id}
**Task:** {task_type} | **Target:** {target_col}

## Performance
- {primary_metric}: {final_score:.4f}
- Baseline: {baseline_score:.4f}
- Improvement: +{improvement:.4f}
- Recommended decision threshold: {recommended_threshold}

## Top Predictive Features (SHAP)
{features_list}

## Hyperparameters
```json
{params_json}
```

## Limitations
- Trained on a single flat CSV. Multi-table data not supported.
- Validate on fresh data before production deployment.
"""


def _build_setup_cell(run_id: str, target_col: str, task_type: str) -> str:
    """Fixed setup code cell — same for every notebook."""
    return f"""# AutoML Orchestrator — Evidence Notebook
# Run ID: {run_id}
# Task: {task_type} | Target: {target_col}

import pandas as pd
import numpy as np
import joblib
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import warnings
warnings.filterwarnings("ignore")

print("Setup complete. Run ID:", "{run_id}")
"""


def _assemble_notebook_cells(
    llm_structure: dict,
    agent_cells_by_key: dict[tuple[str, int], dict],
    setup_code: str,
    run_id: str,
    final_score: float,
    primary_metric: str,
    winner_model: str,
    baseline_score: float,
    recommended_threshold: float,
    top_features: list[str],
    best_params: dict,
) -> list[dict[str, str]]:
    """
    Assemble the final notebook cell list from LLM structure + actual agent code.
    Returns a list of {"type": "markdown"|"code", "source": "..."} dicts.
    """
    cells: list[dict[str, str]] = []

    # ── Title + executive summary ─────────────────────────────────────────────
    title = llm_structure.get("notebook_title", f"AutoML Pipeline — {run_id}")
    exec_summary = llm_structure.get("executive_summary", "")
    cells.append({"type": "markdown", "source": f"# {title}\n\n{exec_summary}"})

    # ── Setup cell ────────────────────────────────────────────────────────────
    cells.append({"type": "code", "source": setup_code})

    # ── Agent sections (in LLM-determined order) ──────────────────────────────
    for i, section in enumerate(llm_structure.get("sections", []), 1):
        agent_key_name = section.get("agent_key", "")
        iteration = section.get("iteration", 0)
        lookup_key = (agent_key_name, iteration)

        heading = section.get("heading", f"## {i}. {agent_key_name.replace('_', ' ').title()}")
        narrative_before = section.get("narrative_before", "")
        narrative_after = section.get("narrative_after", "")
        key_insights = section.get("key_insights", [])

        # Build section header + pre-narrative markdown
        pre_md = f"{heading}\n\n{narrative_before}"
        cells.append({"type": "markdown", "source": pre_md})

        # Actual executed code cell (what the agent really ran).
        # Try exact (agent, iteration) match first; fall back to any cell from
        # this agent so the notebook still shows code even if the LLM returned
        # a slightly wrong iteration number.
        agent_cell = agent_cells_by_key.get(lookup_key)
        if not agent_cell and agent_key_name:
            agent_cell = next(
                (cell for (name, _), cell in agent_cells_by_key.items()
                 if name == agent_key_name),
                None,
            )
        if agent_cell:
            code = agent_cell.get("code", "# code not captured")
            # Trim very long code (sandbox templates can be 300+ lines)
            # Keep first 200 lines to avoid bloating the notebook
            code_lines = code.splitlines()
            if len(code_lines) > 200:
                code = "\n".join(code_lines[:200]) + f"\n# ... ({len(code_lines) - 200} more lines)"
            cells.append({"type": "code", "source": code})

            # stdout output (if any meaningful content)
            stdout = agent_cell.get("stdout", "").strip()
            if stdout:
                stdout_preview = "\n".join(stdout.splitlines()[:30])
                cells.append({
                    "type": "markdown",
                    "source": f"**Output:**\n```\n{stdout_preview}\n```"
                })
        else:
            cells.append({
                "type": "markdown",
                "source": f"*Code for `{agent_key_name}` (iteration {iteration}) not captured.*"
            })

        # Post-code narrative + insights
        post_parts = []
        if narrative_after:
            post_parts.append(narrative_after)
        if key_insights:
            insights_md = "\n".join(f"- {ins}" for ins in key_insights)
            post_parts.append(f"**Key Insights:**\n{insights_md}")
        if post_parts:
            cells.append({"type": "markdown", "source": "\n\n".join(post_parts)})

    # ── Inference quickstart code ─────────────────────────────────────────────
    cells.append({
        "type": "markdown",
        "source": "---\n## Inference — How to Use This Model"
    })
    cells.append({
        "type": "code",
        "source": f"""# Load the packaged inference pipeline
import joblib, pandas as pd

pipeline_data = joblib.load("inference_pipeline.pkl")
preprocessor = pipeline_data["preprocessor"]
model = pipeline_data["model"]
threshold = pipeline_data.get("threshold", {recommended_threshold})

# Example: predict on new data
# new_df = pd.DataFrame([{{"feature1": value1, "feature2": value2, ...}}])
# X_new = preprocessor.transform(new_df) if preprocessor else new_df
# if hasattr(model, "predict_proba"):
#     proba = model.predict_proba(X_new)[:, 1]
#     prediction = (proba >= threshold).astype(int)
# else:
#     prediction = model.predict(X_new)

print(f"Pipeline loaded. Model: {winner_model!r}, threshold: {{threshold}}")
print(f"Best {primary_metric}: {final_score:.4f} (baseline was {baseline_score:.4f})")
print(f"Top SHAP features: {top_features[:5]!r}")
"""
    })

    # ── Conclusion markdown ───────────────────────────────────────────────────
    conclusion = llm_structure.get("conclusion", {})
    findings = conclusion.get("key_findings", [])
    recommendations = conclusion.get("business_recommendations", [])
    next_steps = conclusion.get("next_steps", [])

    conclusion_parts = ["---\n## Conclusion & Recommendations"]
    if findings:
        conclusion_parts.append(
            "### Key Findings\n" + "\n".join(f"- {f}" for f in findings)
        )
    if recommendations:
        conclusion_parts.append(
            "### Business Recommendations\n" + "\n".join(f"- {r}" for r in recommendations)
        )
    if next_steps:
        conclusion_parts.append(
            "### Next Steps\n" + "\n".join(f"- {n}" for n in next_steps)
        )

    best_params_json = json.dumps(best_params, indent=2)
    conclusion_parts.append(
        f"---\n**Final hyperparameters ({winner_model}):**\n```json\n{best_params_json}\n```"
    )
    cells.append({"type": "markdown", "source": "\n\n".join(conclusion_parts)})

    return cells


class ExporterAgent(BaseAgent):
    name = "exporter"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(
            run_id,
            "Packaging artifacts: inference pipeline, agentic notebook, API scaffold, model card..."
        )

        # ── Collect state values ──────────────────────────────────────────────
        winner_model = state.get("winner_model") or "UnknownModel"
        task_type = state.get("task_type") or "unknown"
        target_col = state.get("target_column") or "target"
        primary_metric = state.get("primary_metric") or "score"
        user_goal = state.get("user_goal") or ""
        final_score = state.get("current_score") or 0.0
        baseline_score = state.get("baseline_score") or 0.0
        best_params = state.get("best_hyperparams") or {}
        top_features = state.get("shap_top_features") or []
        recommended_threshold = state.get("recommended_threshold") or 0.5
        artifacts_dir = f"/data/{run_id}/artifacts"
        notebook_cells_raw = state.get("notebook_cells") or []

        # ── Build lookup dict: (agent_name, iteration) → cell ─────────────────
        # For agents with multiple iterations, we keep all of them.
        agent_cells_by_key: dict[tuple[str, int], dict] = {}
        for cell in notebook_cells_raw:
            key = (cell.get("agent", ""), cell.get("iteration", 0))
            agent_cells_by_key[key] = cell

        # ── Build compact steps summary for LLM (no raw code, only results) ──
        steps_summary = []
        for cell in notebook_cells_raw:
            steps_summary.append({
                "agent": cell.get("agent"),
                "title": cell.get("title"),
                "iteration": cell.get("iteration", 0),
                "result_summary": cell.get("result_summary", {}),
            })

        # ── LLM call: generate adaptive notebook structure ────────────────────
        await self.emit(run_id, "LLM generating adaptive notebook narrative...")
        user_message = f"""
Task Type: {task_type}
Target Column: {target_col}
Primary Metric: {primary_metric}
User Goal: {user_goal}
Winner Model: {winner_model}
Baseline Score: {baseline_score:.4f}
Final Score: {final_score:.4f}
Improvement: {final_score - baseline_score:+.4f}
Recommended Threshold: {recommended_threshold}
Top SHAP Features: {json.dumps(top_features[:10])}
Best Hyperparameters: {json.dumps(best_params)}

Pipeline Execution Trace (what actually ran, step by step):
{json.dumps(steps_summary, indent=2)}

Decision Summary:
{json.dumps([
    f"{d.get('agent')}: {d.get('decision')}"
    for d in state.get("decision_log", [])[:15]
], indent=2)}

Generate the notebook structure. Adapt entirely to this specific {task_type} problem.
Use the actual numbers from the execution trace — never write generic placeholders.
"""
        try:
            llm_structure = await self.llm.complete_json(NOTEBOOK_SYSTEM_PROMPT, user_message)
        except Exception as exc:
            # Graceful fallback: minimal structure if LLM fails
            self._log.warning("notebook_llm_failed", error=str(exc))
            llm_structure = _fallback_notebook_structure(
                run_id, task_type, winner_model, primary_metric,
                final_score, baseline_score, steps_summary
            )

        # Boundary validation: the notebook structure is untrusted LLM JSON —
        # normalize the shapes _assemble_notebook_cells iterates so a malformed
        # section/conclusion degrades to the fallback instead of crashing export.
        if not isinstance(llm_structure, dict):
            llm_structure = {}
        llm_structure["sections"] = self.norm_dicts(llm_structure.get("sections"))
        if not llm_structure["sections"]:
            llm_structure = _fallback_notebook_structure(
                run_id, task_type, winner_model, primary_metric,
                final_score, baseline_score, steps_summary
            )
        if not isinstance(llm_structure.get("conclusion"), dict):
            llm_structure["conclusion"] = {}
        for _k in ("key_findings", "business_recommendations", "next_steps"):
            if not isinstance(llm_structure["conclusion"].get(_k), list):
                llm_structure["conclusion"][_k] = []
        for _s in llm_structure["sections"]:
            if not isinstance(_s.get("key_insights"), list):
                _s["key_insights"] = []

        # ── Assemble notebook cells ───────────────────────────────────────────
        setup_code = _build_setup_cell(run_id, target_col, task_type)
        nb_cells = _assemble_notebook_cells(
            llm_structure=llm_structure,
            agent_cells_by_key=agent_cells_by_key,
            setup_code=setup_code,
            run_id=run_id,
            final_score=final_score,
            primary_metric=primary_metric,
            winner_model=winner_model,
            baseline_score=baseline_score,
            recommended_threshold=float(recommended_threshold),
            top_features=top_features,
            best_params=best_params,
        )

        # ── Generate static text artifacts ────────────────────────────────────
        api_code = _build_api_code(run_id, winner_model, primary_metric, final_score)
        model_card = _build_model_card(
            run_id, task_type, target_col, winner_model, primary_metric,
            final_score, baseline_score, recommended_threshold, top_features, best_params,
        )

        # ── Inject all values into sandbox code (repr() — no format conflicts) ─
        code = (
            EXPORT_SANDBOX_CODE
            .replace("ARTIFACTS_DIR", repr(artifacts_dir))
            .replace("TUNED_PATH", repr(state.get("tuned_model_path", state.get("winner_model_path", ""))))
            .replace("PREPROCESSOR_PATH", repr(state.get("preprocessor_path", "")))
            .replace("THRESHOLD", repr(float(recommended_threshold)))
            .replace("TARGET_CLASSES", repr(state.get("target_classes") or []))
            .replace("MULTILABEL_BINARIZER_PATH", repr(state.get("multilabel_binarizer_path") or ""))
            .replace("TASK_TYPE", repr(state.get("task_type") or "unknown"))
            .replace("ENGINEERED_FEATURES", repr([
                {"name": f.get("name"), "formula": f.get("formula"),
                 "fill_value": f.get("fill_value", 0.0)}
                for f in self.norm_dicts(state.get("features_created"))
                if f.get("formula")
            ]))
            .replace("API_CODE", repr(api_code))
            .replace("MODEL_CARD", repr(model_card))
            .replace("NB_CELLS", repr(nb_cells))
        )

        result = await self.execute_code(code, run_id, timeout=120)
        result = await self.try_agentic_repair(
            run_id, code, result,
            task_type=state.get("task_type", "unknown"),
            result_keys=["pipeline_path"],
            goal=("Assemble the inference pipeline as a plain dict "
                  "{preprocessor, model, threshold, target_classes, engineered_features, task_type, "
                  "multilabel_binarizer} (load the preprocessor/model from the paths in the failed "
                  "code) and joblib.dump it to artifacts_dir/inference_pipeline.pkl. Set RESULT with "
                  "pipeline_path (str). Other artifacts (notebook/api/model_card) are optional."),
        )
        if not result["success"]:
            await self._mark_step(run_id, "failed", result["error"])
            return {"error": f"Export failed: {result['error']}", "status": "failed"}

        data = self.result_dict(result)
        artifact_paths = {
            "inference_pipeline": data.get("pipeline_path", ""),
            "api_main": data.get("api_path", ""),
            "notebook": data.get("notebook_path", ""),
            "model_card": data.get("model_card_path", ""),
        }

        from app.core import mlflow_tracker as mlflow
        for name, path in artifact_paths.items():
            if path:
                mlflow.log_artifact(path)
        mlflow.log_dict(state.get("decision_log", []), "decision_log.json")
        mlflow.log_dict(
            {"sections": [s.get("heading") for s in llm_structure.get("sections", [])]},
            "notebook_structure.json",
        )

        await self.emit(
            run_id,
            "All artifacts packaged. Pipeline complete!",
            {"artifacts": list(artifact_paths.keys())},
        )
        await self._mark_step(run_id, "completed")

        return {
            "artifact_paths": artifact_paths,
            "framework_version": str(data.get("sklearn_version") or ""),
            "status": "completed",
        }


# ── Fallback notebook structure (if LLM call fails) ──────────────────────────
def _fallback_notebook_structure(
    run_id: str,
    task_type: str,
    winner_model: str,
    primary_metric: str,
    final_score: float,
    baseline_score: float,
    steps_summary: list[dict],
) -> dict:
    """Minimal but correct fallback structure when LLM call fails."""
    agent_order = [
        "data_auditor", "baseline_builder", "eda_agent", "preprocessor",
        "feature_engineer", "model_selector", "tuner", "evaluator",
    ]
    titles = {
        "data_auditor": "Data Quality Audit",
        "baseline_builder": "Baseline Performance",
        "eda_agent": "Exploratory Data Analysis",
        "preprocessor": "Data Preprocessing",
        "feature_engineer": "Feature Engineering",
        "model_selector": "Model Selection",
        "tuner": "Hyperparameter Tuning",
        "evaluator": "Final Evaluation",
    }
    sections = []
    seen = set()
    for step in steps_summary:
        agent = step.get("agent", "")
        iteration = step.get("iteration", 0)
        key = (agent, iteration)
        if key in seen:
            continue
        seen.add(key)
        title = titles.get(agent, agent.replace("_", " ").title())
        if iteration > 0:
            title = f"{title} — Iteration {iteration}"
        sections.append({
            "agent_key": agent,
            "iteration": iteration,
            "heading": f"## {title}",
            "narrative_before": f"Running the {agent.replace('_', ' ')} step.",
            "narrative_after": f"Step completed. See result summary above.",
            "key_insights": [],
        })

    # Directional: positive ALWAYS means better, incl. lower-is-better metrics.
    # (A bare subtraction printed an rmse 3.5→0.9 win as "-2.6" in the customer's
    # model card and notebook — 2026-07-04 fix.)
    improvement = metric_registry.directional_gain(final_score, baseline_score, primary_metric)
    return {
        "notebook_title": f"AutoML Pipeline — {run_id}",
        "executive_summary": (
            f"Automated ML pipeline for {task_type} task. "
            f"Winner: {winner_model}. "
            f"{primary_metric}: {final_score:.4f} (baseline: {baseline_score:.4f}, "
            f"improvement: {improvement:+.4f})."
        ),
        "sections": sections,
        "conclusion": {
            "key_findings": [
                f"{winner_model} achieved {primary_metric} = {final_score:.4f}",
                f"Baseline was {baseline_score:.4f} — improvement of {improvement:+.4f}",
            ],
            "business_recommendations": [
                "Review SHAP features to validate business logic",
                "Monitor model performance on fresh data",
            ],
            "next_steps": [
                "Deploy inference pipeline to staging environment",
                "Set up drift monitoring with Evidently",
            ],
        },
    }
