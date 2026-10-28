"""
Prometheus metrics. Falls back to no-op stubs if prometheus_client is not installed,
so the backend boots cleanly before the package is pip-installed into the container.
"""

try:
    from prometheus_client import Counter, Gauge, Histogram, Info
    _HAS_PROMETHEUS = True
except ImportError:
    _HAS_PROMETHEUS = False


# ── No-op stubs used when prometheus_client is absent ─────────────────────────
class _Noop:
    def labels(self, **_): return self
    def inc(self, _=1): pass
    def dec(self, _=1): pass
    def observe(self, _): pass
    def info(self, _): pass
    def set(self, _): pass


def _counter(name, doc, labels=()):
    if _HAS_PROMETHEUS:
        return Counter(name, doc, list(labels))
    return _Noop()


def _histogram(name, doc, labels=(), buckets=None):
    if _HAS_PROMETHEUS:
        kwargs = {"buckets": buckets} if buckets else {}
        return Histogram(name, doc, list(labels), **kwargs)
    return _Noop()


def _gauge(name, doc):
    if _HAS_PROMETHEUS:
        return Gauge(name, doc)
    return _Noop()


# ── Pipeline-level ────────────────────────────────────────────────────────────
pipeline_runs_total = _counter(
    "automl_pipeline_runs_total", "Total pipeline runs by status", ["status"]
)
pipeline_duration_seconds = _histogram(
    "automl_pipeline_duration_seconds", "End-to-end pipeline wall-clock time",
    buckets=[30, 60, 120, 300, 600, 900, 1800],
)
pipeline_baseline_score = _histogram(
    "automl_pipeline_baseline_score", "Baseline model score at run start",
    buckets=[0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0],
)
pipeline_final_score = _histogram(
    "automl_pipeline_final_score", "Final model score after all iterations",
    buckets=[0.5, 0.6, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0],
)
active_pipelines = _gauge("automl_active_pipelines", "Pipelines currently running")

# ── Agent-level ───────────────────────────────────────────────────────────────
agent_runs_total = _counter(
    "automl_agent_runs_total", "Agent invocations", ["agent_name", "status"]
)
agent_duration_seconds = _histogram(
    "automl_agent_duration_seconds", "Per-agent wall-clock time", ["agent_name"],
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
)
# Tier-1 self-repair micro-loop: outcome ∈ recovered | exhausted
agent_repairs_total = _counter(
    "automl_agent_repairs_total", "Self-repair attempts by outcome",
    ["agent_name", "outcome"],
)

# ── LLM-level ─────────────────────────────────────────────────────────────────
llm_calls_total = _counter(
    "automl_llm_calls_total", "LLM API calls", ["agent_name", "provider"]
)
llm_tokens_total = _counter(
    "automl_llm_tokens_total", "LLM tokens consumed", ["agent_name", "direction"]
)
llm_latency_seconds = _histogram(
    "automl_llm_latency_seconds", "LLM API call round-trip latency", ["agent_name"],
    buckets=[0.5, 1, 2, 5, 10, 20, 30],
)
llm_cost_usd_total = _counter(
    "automl_llm_cost_usd_total", "Estimated LLM cost in USD", ["agent_name"]
)
# Cache observability (Layer 4): ratio = hits / requests.
llm_cache_requests_total = _counter(
    "automl_llm_cache_requests_total", "LLM completions eligible for caching"
)
llm_cache_hits_total = _counter(
    "automl_llm_cache_hits_total", "LLM completions served from cache"
)

# ── HTTP request-level (Layer 1) ──────────────────────────────────────────────
http_requests_total = _counter(
    "automl_http_requests_total", "HTTP requests", ["method", "path", "status"]
)
http_request_duration_seconds = _histogram(
    "automl_http_request_duration_seconds", "HTTP request latency", ["method", "path"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1, 2, 5],
)

# ── Sandbox-level ─────────────────────────────────────────────────────────────
sandbox_executions_total = _counter(
    "automl_sandbox_executions_total", "Code sandbox executions", ["status"]
)
sandbox_duration_seconds = _histogram(
    "automl_sandbox_duration_seconds", "Sandbox code execution time",
    buckets=[1, 5, 10, 30, 60, 120, 300, 600],
)

# ── Model quality ─────────────────────────────────────────────────────────────
model_score_improvement = _histogram(
    "automl_model_score_improvement", "Score lift (final - baseline)",
    buckets=[-0.1, 0, 0.01, 0.05, 0.1, 0.15, 0.2, 0.3],
)
features_kept_total = _histogram(
    "automl_features_kept_total", "Engineered features kept per run",
    buckets=[0, 1, 2, 3, 4, 5, 6],
)

# ── Inference (Phase 3) ───────────────────────────────────────────────────────
predictions_total = _counter(
    "automl_predictions_total", "Predictions served", ["run_id", "status"]
)
prediction_latency_seconds = _histogram(
    "automl_prediction_latency_seconds", "Per-prediction round-trip latency",
    buckets=[0.1, 0.25, 0.5, 1, 2, 5, 10],
)
drift_psi_max = _gauge(
    "automl_drift_psi_max", "Max PSI across features (latest drift check)"
)
active_deployments = _gauge("automl_active_deployments", "Models currently deployed")
