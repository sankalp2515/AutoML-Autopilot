from typing import Any, TypedDict


class DecisionLogEntry(TypedDict):
    agent: str
    timestamp: str
    decision: str
    reasoning: str
    code_executed: str
    result_summary: str


class AgentState(TypedDict, total=False):
    # Identity
    run_id: str
    mlflow_run_id: str

    # User inputs
    dataset_path: str
    user_goal: str
    exclude_columns: list[str]
    fp_fn_preference: str
    interpretability_required: bool
    data_dir: str
    pipeline: str                   # "tabular" (default) | "timeseries" — selects which graph runs

    # Time-Series studio (P3/T1) — forecasting framing + the Wrong-Door Guard
    timestamp_col: str
    forecast_horizon: int
    frequency: str                  # e.g. "D", "H", "W"
    wrong_door_warning: str         # set by tabular auditor when time-ordered data is detected

    # Problem Framer outputs
    task_type: str                  # binary_classification | multiclass_classification | multilabel_classification | regression
    target_column: str
    primary_metric: str
    good_enough_threshold: float
    inference_features: list[str]
    # Multilabel specific
    label_columns: list[str]        # for multiple binary target columns
    label_delimiter: str            # for delimited string targets (e.g. ";")

    # Rare-event / cost-sensitive framing (Phase 3.5). MUST be declared here or
    # LangGraph silently drops them between nodes and the at-k scorer would fall
    # back to the default budget with no error (the "undeclared state key" family).
    rare_event_k: float             # action budget as a FRACTION of the population
    business_objective: dict[str, Any]  # value_tp / cost_fp / cost_fn / value_tn / currency

    # Data Auditor outputs
    data_audit: dict[str, Any]
    audit_verdict: str
    imbalance_severity: str  # "none" | "moderate" | "severe" (minority < 5%)

    # Honest evaluation (Phase 0.2) — a true holdout is carved from the RAW
    # dataset BEFORE any agent fits/selects/tunes, so the reported final score
    # is a generalization estimate, not the selection CV.
    holdout_path: str               # raw never-seen rows; only the evaluator touches it
    holdout_frac: float             # fraction reserved (0 when dataset too small → CV fallback)
    evaluation_basis: str           # "holdout" | "in_sample_split" (fallback) — set by evaluator
    score_std: float                # CV std of the primary score on train — the iteration noise floor

    # Baseline Builder outputs
    baseline_score: float
    baseline_model: str
    baseline_errors: dict[str, Any]

    # EDA + Error Agent outputs
    eda_insights: dict[str, Any]
    prioritized_issues: list[str]

    # Preprocessing Agent outputs
    preprocessing_decisions: list[dict[str, Any]]
    preprocessor_path: str
    processed_data_path: str        # path to processed.csv for downstream agents
    target_classes: list[str]       # original class labels when target was label-encoded
    # Imbalance (P18) + multilabel (P19) — returned by preprocessor; MUST be declared
    # here or LangGraph silently drops them and they never reach exporter/inference.
    imbalance_strategy: str         # "smote" | "smote_tomek" | "class_weight" | "none"
    training_pipeline_path: str     # imblearn prep+resample pkl (documentation artifact; not used for CV)
    multilabel_binarizer_path: str  # path to MultiLabelBinarizer.pkl for multilabel decode
    resampler_used: str             # "smote" | "smote_tomek" | "none" — what actually ran
    feature_source_map_path: str    # exact transformed-name → raw-column JSON (trust gate leak mapping)
    hpo_trials: int                 # Optuna trials completed (P3.13 — Overview parity)
    framework_version: str          # sklearn version captured at export (P3.13)
    model_recommendations: str      # registry install-hints, e.g. CatBoost/LightGBM (P3.14)
    last_holdout_score: float       # previous iteration's HOLDOUT score — the gate's comparison basis
    scale_strategy: str             # in_memory | sampled | streaming (core/scale.py)
    train_full_path: str            # uncapped train.csv kept when streaming (out-of-core learners)
    streaming_winner: bool          # winner is the out-of-core SGD (tuner skips it)
    pipeline_plan: dict[str, Any]   # P1.7 planner: skips + iteration budget for this run

    # Feature Engineer outputs
    features_created: list[dict[str, Any]]
    features_dropped: list[dict[str, Any]]
    enriched_data_path: str         # path to enriched.csv (with new features)

    # Model Selector + Tuner outputs
    models_evaluated: dict[str, Any]
    winner_model: str
    winner_model_path: str          # path to winner_model.pkl
    winner_model_class: str         # e.g. "xgb.XGBClassifier"
    best_hyperparams: dict[str, Any]
    tuned_model_path: str           # path to tuned_model.pkl
    tuned_score: float

    # Quant trading diagnostic (time-series) — Sharpe/drawdown/turnover from the forecast
    backtest_metrics: dict[str, Any]

    # Evaluator outputs
    evaluation_report: dict[str, Any]
    slice_performance: dict[str, Any]
    recommended_threshold: float
    shap_top_features: list[str]

    # Iteration control
    iteration: int
    max_iterations: int
    iteration_scores: list[float]
    prev_score: float
    current_score: float

    # Tier-2 diagnostic router (P8) — bounded back-jump on failure
    backjumps_used: int
    repair_hint: str

    # Export outputs
    artifact_paths: dict[str, str]

    # Full audit trail
    decision_log: list[DecisionLogEntry]

    # Evidence notebook cells: each agent appends one entry with its actual
    # executed code + result summary so the exporter can build the notebook
    # via LLM-driven narrative (no hardcoded structure).
    # Schema per entry:
    #   agent: str          — agent name (matches AGENT_ORDER)
    #   title: str          — human-readable section title
    #   iteration: int      — 0 for one-time agents; N for per-iteration agents
    #   code: str           — full executed sandbox code (what actually ran)
    #   stdout: str         — captured stdout from the sandbox
    #   result_summary: dict — key metrics / findings passed to the LLM
    notebook_cells: list[dict[str, Any]]

    # Runtime
    error: str | None
    status: str
