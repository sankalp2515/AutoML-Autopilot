import json
from typing import Any

from app.agents.base_agent import BaseAgent
from app.core import metric_registry
from app.core import mlflow_tracker as mlflow
from app.core.state import AgentState

# The metric vocabulary + per-task menu are DERIVED from metric_registry (the
# single source of truth) — never hand-typed here. Adding a metric = one registry
# line, no prompt edit. The selection heuristics below are LLM *guidance*, not a
# hardcoded choice: the LLM still decides the metric from the user's goal.
_METRIC_ENUM = " | ".join(f'"{m}"' for m in metric_registry.all_metrics())

SYSTEM_PROMPT = f"""You are an expert ML problem framing specialist.
Given a user's business goal and dataset information, you determine the precise ML task definition.

You MUST respond with a valid JSON object containing exactly these fields:
{{
  "task_type": "binary_classification" | "multiclass_classification" | "multilabel_classification" | "regression",
  "target_column": "<best guess at target column name from the columns list>",
  "primary_metric": {_METRIC_ENUM},
  "good_enough_threshold": <float between 0 and 1>,
  "reasoning": "<your chain of thought>",
  "decisions": [
    {{"decision": "<what you decided>", "reasoning": "<why>"}}
  ],
  "label_columns": ["<list of binary target columns if multilabel>"],
  "label_delimiter": "<delimiter if target is a delimited string, e.g. ';' or ','>",
  "rare_event_k": <REQUIRED only if primary_metric is an at-k metric: the action budget
                   as a FRACTION of the population, e.g. 0.05 for "we can contact 5%">,
  "business_objective": <OPTIONAL object, ONLY when the goal states or clearly implies
                         unit economics; omit entirely otherwise:
    {{"value_tp": <gain from correctly acting on a positive>,
      "cost_fp": <cost of acting on a negative, e.g. a wasted contact>,
      "cost_fn": <cost of MISSING a positive; 0 if missing costs nothing directly>,
      "value_tn": <gain from correctly not acting; usually 0>,
      "currency": "<e.g. USD, EUR, unit>"}}>
}}

Allowed metrics by task — choose ONE that fits the task you assign:
{metric_registry.framer_menu_text()}

Selecting the metric from the goal (guidance, not rules — reason from the goal):
- fraud/churn/disease/default → recall (missing a positive is costly)
- spam filtering → precision
- price/sales/demand → rmse or mae
- SEVERE class imbalance (minority < 5%) → pr_auc — ROC-AUC misleads when extremely imbalanced
- multiclass: pick from its allowed list above; binary-only metrics' weighted variants are auto-applied

Rare-event / fixed-budget problems (needle in a haystack):
- When the goal names an ACTION BUDGET ("we can only call 5,000 people", "top 2% of the
  list", "we mail 10% of subscribers"), the question is not "who converts" but "who are
  the best N to act on". Choose precision_at_k (quality of the targeted slice),
  recall_at_k (share of opportunity captured) or lift_at_k (multiple over random), and
  set rare_event_k to that budget as a fraction.
- At a very low base rate (well under 1%) ROC-AUC integrates over a ranking nobody will
  ever act on — prefer pr_auc or an at-k metric.

Business objective (unit economics):
- Set it ONLY when the goal gives, or clearly implies, what an outcome is worth:
  "a conversion is worth about $300 and a wasted call costs us $40" →
  {{"value_tp": 300, "cost_fp": 40, "cost_fn": 0, "value_tn": 0, "currency": "USD"}}.
- Use positive magnitudes for both values and costs; the signs are applied downstream.
- If the goal says nothing about value or cost, OMIT the field. Do not invent numbers —
  a fabricated cost model produces a confident, wrong operating-point recommendation.

Multilabel detection:
- If target column contains delimited strings (e.g. "tag1;tag2;tag3") → multilabel_classification with label_delimiter
- If multiple binary columns represent labels (e.g. "label_a", "label_b", "label_c" all 0/1) → multilabel_classification with label_columns list
"""


class ProblemFramerAgent(BaseAgent):
    name = "problem_framer"

    async def run(self, state: AgentState) -> dict[str, Any]:
        run_id = state["run_id"]
        await self._mark_step(run_id, "running")
        await self.emit(run_id, "Analyzing your business goal and framing the ML problem...")

        audit = state.get("data_audit", {})
        columns = list(audit.get("dtypes", {}).keys()) if audit else []
        imbalance_severity = state.get("imbalance_severity", "none")

        user_message = f"""
Business goal: {state['user_goal']}

Dataset columns available: {json.dumps(columns)}
Exclude columns (user specified): {json.dumps(state.get('exclude_columns', []))}
FP/FN preference: {state.get('fp_fn_preference', 'not specified')}
Interpretability required: {state.get('interpretability_required', False)}
Class imbalance severity: {imbalance_severity}  # "none" | "moderate" | "severe" (minority < 5%)

Determine the ML task type, target column, and primary metric.
Also detect if this is a multilabel problem:
- Target column contains delimited strings (e.g. "tag1;tag2;tag3") → multilabel_classification with label_delimiter
- Multiple binary columns represent labels (e.g. "label_a", "label_b", "label_c" all 0/1) → multilabel_classification with label_columns list
"""
        response = await self.llm.complete_json(SYSTEM_PROMPT, user_message)

        # Guardrail: clamp the framing to valid values before it flows downstream
        # (a bad metric/task would crash or silently NaN a scorer later).
        from app.core.guardrails import validate_and_fix_framing
        response, gnotes = validate_and_fix_framing(response, columns)
        for note in gnotes:
            self._log.warning("framing_guardrail", run_id=run_id, fix=note)
            await self._log_decision(
                run_id=run_id, decision=f"Guardrail corrected framing: {note}",
                reasoning="Validated the LLM's problem framing against the metric/task registry.",
            )

        task_type = response.get("task_type", "binary_classification")
        target_column = response.get("target_column", "")
        primary_metric = response.get("primary_metric", "auc_roc")
        good_enough = response.get("good_enough_threshold", 0.80)
        label_columns = response.get("label_columns", [])
        label_delimiter = response.get("label_delimiter", "")
        # Phase 3.5 — both already clamped by validate_and_fix_framing above.
        rare_event_k = response.get("rare_event_k")
        business_objective = response.get("business_objective")

        decisions = response.get("decisions", [])
        decision_log_entries = []
        for d in decisions:
            entry = await self._log_decision(
                run_id=run_id,
                decision=d.get("decision", ""),
                reasoning=d.get("reasoning", ""),
            )
            decision_log_entries.append(entry)

        await self.emit(
            run_id,
            f"Task identified: {task_type} | Target: {target_column} | Metric: {primary_metric}",
            {"task_type": task_type, "target_column": target_column, "primary_metric": primary_metric},
        )
        if rare_event_k:
            await self.emit(
                run_id,
                f"Rare-event framing: optimizing {primary_metric} at an action budget of "
                f"{float(rare_event_k) * 100:.4g}% of the population.",
                {"rare_event_k": float(rare_event_k)},
            )
        if business_objective:
            cur = business_objective.get("currency", "unit")
            await self.emit(
                run_id,
                f"Business objective set from the goal: a true positive is worth "
                f"{business_objective.get('value_tp')} {cur}, a false positive costs "
                f"{business_objective.get('cost_fp')} {cur}. The operating point will be "
                f"chosen to maximize net benefit, not the metric.",
                {"business_objective": business_objective},
            )

        mlflow.log_params({
            "task_type": task_type,
            "target_column": target_column,
            "primary_metric": primary_metric,
            "user_goal": state["user_goal"][:250],
        })

        # Persist framing immediately — if the pipeline fails later, the run
        # record still shows what problem was being solved.
        await self._update_run_field(
            run_id,
            task_type=task_type,
            target_column=target_column,
            primary_metric=primary_metric,
            label_columns=label_columns,
            label_delimiter=label_delimiter,
        )

        await self._mark_step(run_id, "completed")

        existing_log = state.get("decision_log", [])
        return {
            "task_type": task_type,
            "target_column": target_column,
            "primary_metric": primary_metric,
            "good_enough_threshold": float(good_enough),
            "label_columns": label_columns,
            "label_delimiter": label_delimiter,
            "rare_event_k": float(rare_event_k) if rare_event_k else 0.0,
            "business_objective": business_objective or {},
            "decision_log": existing_log + decision_log_entries,
        }
