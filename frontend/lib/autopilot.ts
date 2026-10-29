/**
 * AutoML Autopilot API client + types. Mirrors backend/app/autopilot/schemas.py.
 * All numbers shown in the UI come from these payloads — the UI never derives
 * metrics of its own.
 */
import { API_BASE } from './api';
import { getAccessToken } from './supabase';

const BASE = '/api/v1/autopilot';

export type Status =
  | 'CREATED' | 'VALIDATING' | 'BASELINING' | 'INVESTIGATING' | 'RESEARCHING' | 'PROPOSING'
  | 'POLICY_CHECK' | 'EXECUTING' | 'OBSERVING' | 'TESTING_CORRECTION' | 'VERIFYING'
  | 'REVIEW_READY' | 'APPROVED' | 'REJECTED' | 'INCONCLUSIVE' | 'BLOCKED' | 'FAILED' | 'CANCELLED';

export const TERMINAL: Status[] = ['REVIEW_READY', 'APPROVED', 'REJECTED', 'INCONCLUSIVE', 'BLOCKED', 'FAILED', 'CANCELLED'];

export interface Scenario {
  scenario_id: string; kind: string; title: string; symptom: string;
  rows_reference: number; rows_current: number; labels_available: boolean; has_challenger: boolean;
}

export interface Finding {
  finding_id: string; kind: string; title: string; value: unknown; method: string;
  sample_count: number; severity: 'info' | 'warn' | 'critical';
}

export interface Hypothesis {
  hypothesis_id: string; cause: string; statement: string; supporting_findings: string[];
  counter_findings: string[]; source_refs: string[]; prediction_by_template: Record<string, string>;
  belief_weight: number; calibration: string; status: 'PROPOSED' | 'SUPPORTED' | 'REFUTED' | 'UNRESOLVED';
  revision: number; proposed_by: string;
}

export interface CandidateScore {
  template_id: string; kind: 'diagnostic' | 'corrective'; eligible: boolean; reason: string;
  expected_information_bits: number; estimated_cost_units: number; duplicate_penalty: number; score: number;
  predicted_buckets: Record<string, string>;
}

export interface BeliefChange {
  hypothesis_id: string; predicted: string | null; observed: string; verdict: string;
  before: number; after: number; status: string;
}

export interface Experiment {
  experiment_id: string; template_id: string; kind: 'diagnostic' | 'corrective'; hypothesis_ids: string[];
  parameters: Record<string, unknown>; source_refs: string[]; expected_outcomes: Record<string, string>;
  signature: string; estimated_cost_units: number; status: string; actual_seconds: number | null;
  error: string | null; runner_mode?: string; output_sha256?: string;
  observation: { bucket: string; measurements: Record<string, unknown>; candidate?: Record<string, unknown> } | null;
  envelope?: Record<string, unknown> & { envelope_digest: string };
  belief_update?: BeliefChange[];
}

export interface Evidence {
  source_ref: string; url: string; title: string; publisher: string; source_type: string; fetched_at: string;
  version_match: string; relevance: number; status: string; caveats: string[]; excerpt: string;
  excerpt_sha256: string; extracted: boolean; purpose?: 'library_fix' | 'release_review' | 'user_docs';
}

export interface ProviderCall {
  provider: string; operation: string; correlation_id: string; request_id: string | null; model: string | null;
  ok: boolean; status_code: number | null; error: string | null; latency_ms: number; result_count: number | null;
  prompt_tokens?: number | null; completion_tokens?: number | null; est_cost_usd?: number | null; at: string;
}

export interface Check { check: string; status: 'PASS' | 'FAIL' | 'INCONCLUSIVE'; detail: string; evidence: Record<string, unknown> }

export interface Verification {
  verifier_version: string; decision: string; reason_codes: string[]; checks: Check[];
  metrics: Record<string, any>; candidate: string; incumbent: string; metric: string; mode: string;
  prediction_sources?: { incumbent: string; candidate: string };
}

export interface Decision {
  decision: string; reason_codes: string[]; summary: string; next_action: string; incumbent_changed: boolean;
}

export interface Investigation {
  run_id: string; status: Status; config: Record<string, any>; created_at: string;
  scenario: { scenario_id: string; kind: string; title: string; symptom: string; primary_metric: string;
              notes?: string; source?: 'catalog' | 'upload' | 'public_dataset'; docs_urls?: string[];
              prediction_source?: 'user_scores' | 'reconstructed_model';
              critical_segment: Record<string, any>; rows: { reference: number; current: number } } | null;
  protocol: { protocol_hash: string; counts: Record<string, number>; seed: number; metric: string; keying: string } | null;
  inputs: Record<string, string>;
  budget: { max_experiments: number; max_cost_units: number; experiments_used: number; cost_units_used: number; cpu_seconds: number };
  findings: Finding[]; hypotheses: Hypothesis[];
  belief_history: { after: string; template_id?: string; observed?: string; weights: Record<string, number> }[];
  rankings: { step: number; candidates: CandidateScore[] }[];
  experiments: Experiment[]; denials: { code: string; reason: string }[];
  research: { state: string; evidence: Evidence[]; calls: ProviderCall[]; query: any; mapping: any; reason_code: string | null };
  planner: { requested: string; used: string | null; calls: ProviderCall[]; note: string | null };
  correction: { template_id: string; cause: string | null; mode: string } | null;
  verification: Verification | null; decision: Decision | null;
  final_test_accesses: { candidate: string; at: string }[];
  review: { action: string; reviewer: string; note: string; at: string; effect: string } | null;
  error: string | null;
  queue_position?: number | null;
  step_pending?: boolean;
  stakeholder_summary?: { status: 'OK' | 'REJECTED_UNGROUNDED' | 'UNAVAILABLE'; text: string | null; reason?: string } | null;
  summary_calls?: ProviderCall[];
}

export interface Summary {
  run_id: string; scenario_id: string; status: Status; decision: string | null; title: string | null;
  created_at: string; planner: string | null; research: string | null;
}

export interface Providers {
  tavily: { configured: boolean; endpoint: string };
  nebius_token_factory: { configured: boolean; model: string; status: string };
  nvidia_api?: { configured: boolean; model: string; status: string };
  planner_provider?: string | null;
  runner: string;
}

/** Plain-language names for the fixed experiment templates. */
export const TEMPLATE_INFO: Record<string, { label: string; what: string }> = {
  CONTRACT_DIAGNOSTIC: { label: 'Data contract check', what: 'Nulls, unseen categories and schema against the training contract' },
  TRAIN_SERVE_PARITY: { label: 'Train vs serve parity', what: 'Compares feature scales and shapes between training and live inputs' },
  SHIFT_AND_LABEL_CHECK: { label: 'Drift & label-rate check', what: 'Separates input drift from a change in the outcome base rate' },
  THRESHOLD_STUDY: { label: 'Threshold study', what: 'Is the ranking still good while the decision cut-off is wrong?' },
  SEGMENT_FAILURE_AUDIT: { label: 'Segment audit', what: 'Is the failure concentrated in one customer segment?' },
  DEPENDENCY_API_PROBE: { label: 'Library API probe', what: 'Inspects the installed library signature for the failing call' },
  PREPROCESSING_ABLATION: { label: 'Input repair', what: 'Applies the measured unit fix to live inputs; no retrain' },
  THRESHOLD_CANDIDATE: { label: 'New threshold', what: 'Chooses a threshold on the selection split' },
  CLASS_WEIGHT_CHALLENGER: { label: 'Class-weighted retrain', what: 'Retrains with class weights' },
  MODEL_FAMILY_CHALLENGER: { label: 'Full retrain (new model family)', what: 'The classic AutoML response' },
  DEPENDENCY_COMPAT_FIX: { label: 'Sourced compatibility fix', what: 'Renames the parameter using a cited official source' },
  CHALLENGER_REVIEW: { label: 'Challenger intake', what: 'Loads the submitted model for verification' },
};
export const templateLabel = (id: string) => TEMPLATE_INFO[id]?.label ?? pretty(id);

export const PROVIDER_LABEL: Record<string, string> = {
  nvidia_api: 'NVIDIA API',
  nebius_token_factory: 'Nebius Token Factory',
  tavily: 'Tavily',
};

/** "nvidia/nemotron-3-ultra-550b-a55b" -> "Nemotron 3 Ultra 550B A55B" */
export const modelLabel = (m?: string | null) =>
  (m ?? '').split('/').pop()!.split('-').map((w) => (/[0-9]/.test(w) ? w.toUpperCase() : w.charAt(0).toUpperCase() + w.slice(1))).join(' ');

/** Anonymous per-browser id: on the public demo each visitor gets their own run limits. */
function sessionId(): string {
  const make = () => Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, '0')).join('');
  try {
    let id = localStorage.getItem('autopilot-session');
    if (!id) { id = make(); localStorage.setItem('autopilot-session', id); }
    return id;
  } catch {
    return (globalThis as any).__apSession ??= make();
  }
}

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await getAccessToken();
  const headers = new Headers(init?.headers);
  headers.set('X-Autopilot-Session', sessionId());
  headers.set('Accept', 'application/json');
  if (init?.body) headers.set('Content-Type', 'application/json');
  if (token) headers.set('Authorization', `Bearer ${token}`);
  const res = await fetch(`${API_BASE}${BASE}${path}`, { ...init, headers });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const b = await res.json();
      detail = typeof b.detail === 'string' ? b.detail : JSON.stringify(b.detail);
    } catch {}
    throw new Error(detail);
  }
  return res.status === 204 ? undefined as T : res.json() as Promise<T>;
}

export interface BenchmarkArm {
  correct: number; runs: number; false_promotions: number; median_experiments: number;
  mean_experiments: number; total_cost_units: number; total_retrains: number; median_cpu_seconds: number;
}
export interface Benchmark {
  available: boolean; how_to_run?: string; arms: Record<string, BenchmarkArm>;
  verifier_must_fail: { scenarios: string[]; correct: number; runs: number; false_promotions: number };
  not_run: Record<string, string>; notes: string[]; seeds: number[]; generated_at: string;
}
export interface AblationArm {
  correct: number; runs: number; false_promotions: number; mean_experiments: number; total_cost_units: number;
  median_model_seconds: number; est_cost_usd: number; runs_where_anchor_added_a_cause: number; runs_that_fell_back_to_rules: number;
}
export interface Ablation {
  available: boolean; how_to_run?: string; planner_model: string; provider: string; seeds: number[];
  planner_ablation: Record<string, AblationArm>;
  tavily_ablation_T01?: Record<'tavily_on' | 'tavily_off', { correct: number; runs: number; decisions: string[] }>;
}

export function incidentMarkdown(inv: Investigation): string {
  const v = inv.verification, f = v?.metrics.final_test, delta = v?.metrics.delta;
  return [`# ${inv.scenario?.title ?? 'Model investigation'}`, '', inv.decision?.summary ?? pretty(inv.status),
    `Next: ${inv.decision?.next_action ?? 'Investigation in progress.'}`,
    f ? `${v!.metric}: ${f.incumbent[v!.metric]} → ${f.candidate[v!.metric]} (final test, ${f.rows} rows)` : '',
    delta ? `Difference ${delta.delta}; paired bootstrap 95% CI [${delta.ci_low}, ${delta.ci_high}]` : '',
    `Current model predictions: ${v?.prediction_sources?.incumbent ?? inv.scenario?.prediction_source ?? 'reconstructed_model'}`,
    `Proposed fix predictions: ${v?.prediction_sources?.candidate ?? 'not evaluated'}`,
    `Tests: ${inv.budget.experiments_used}; experiment cost units: ${inv.budget.cost_units_used}`,
    'Approval records a decision; no live model is changed.', `Investigation: ${inv.run_id}`].filter(Boolean).join('\n');
}

export const autopilot = {
  scenarios: () => req<{ scenarios: Scenario[] }>('/scenarios'),
  providers: () => req<Providers>('/providers'),
  list: (limit = 25) => req<{ investigations: Summary[] }>(`/investigations?limit=${limit}`),
  get: (id: string) => req<Investigation>(`/investigations/${encodeURIComponent(id)}`),
  create: (body: { scenario_id: string; seed?: number; planner?: string; research?: string; auto_run?: boolean },
           idempotencyKey: string) =>
    req<{ run_id: string; status: Status; created: boolean }>('/investigations', {
      method: 'POST', body: JSON.stringify(body), headers: { 'Idempotency-Key': idempotencyKey },
    }),
  next: (id: string) => req<Investigation>(`/investigations/${encodeURIComponent(id)}/next`, { method: 'POST' }),
  cancel: (id: string) => req<{ status: Status }>(`/investigations/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),
  review: (id: string, action: 'approve' | 'reject', reviewer: string, note = '') =>
    req<{ status: Status }>(`/investigations/${encodeURIComponent(id)}/review`, {
      method: 'POST', body: JSON.stringify({ action, reviewer, note }),
    }),
  share: (id: string) => req<{ path: string; expires_at: number }>(`/investigations/${encodeURIComponent(id)}/share`, { method: 'POST' }),
  shared: (token: string) => req<Investigation>(`/shared/${encodeURIComponent(token)}`),
  showcase: () => req<Investigation>('/showcase'),
  deleteUpload: (id: string) => req<void>(`/uploads/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  /** Downloads the receipt with the caller's credentials (a plain link would drop the auth header). */
  exportReceipt: async (id: string) => {
    const token = await getAccessToken();
    const headers: Record<string, string> = { 'X-Autopilot-Session': sessionId() };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}${BASE}/investigations/${encodeURIComponent(id)}/export`, { headers });
    if (!res.ok) throw new Error(`Export failed: HTTP ${res.status}`);
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement('a');
    a.href = url;
    a.download = `autopilot-receipt-${id.slice(0, 8)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  },
  benchmark: () => req<Benchmark>('/benchmark'),
  publicBenchmark: () => req<{ available: boolean; dataset: string; source: string; fault: string; arms: Record<string, BenchmarkArm> }>('/benchmark/public-data'),
  samples: () => req<{ samples: Sample[] }>('/samples'),
  sampleUrl: (id: string, window: 'reference' | 'current') => `${API_BASE}${BASE}/samples/${id}/${window}.csv`,
  uploads: () => req<{ uploads: UploadInfo[] }>('/uploads'),
  upload: async (reference: Blob, current: Blob, names: [string, string] = ['reference.csv', 'current.csv']) => {
    const token = await getAccessToken();
    const fd = new FormData();
    fd.append('reference', reference, names[0]);
    fd.append('current', current, names[1]);
    const headers: Record<string, string> = { 'X-Autopilot-Session': sessionId() };
    if (token) headers.Authorization = `Bearer ${token}`;
    const res = await fetch(`${API_BASE}${BASE}/uploads`, { method: 'POST', body: fd, headers });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(typeof body.detail === 'string' ? body.detail : `Upload failed (HTTP ${res.status})`);
    return body as UploadInfo;
  },
  configure: (uploadId: string, spec: UploadSpec) =>
    req<UploadInfo & { features: { numeric: string[]; categorical: string[]; dropped: string[] }; warnings: string[]; labelled_current_rows: number }>(
      `/uploads/${encodeURIComponent(uploadId)}/configure`, { method: 'POST', body: JSON.stringify(spec) }),
  ablation: () => req<Ablation>('/ablation'),
};

export interface UploadColumn { name: string; kind: 'numeric' | 'categorical' | 'id_like' | 'high_cardinality'; unique: number; null_rate: number; current_null_rate?: number; samples?: string[]; in_current: boolean; values: string[] }
export interface UploadInfo {
  upload_id: string; rows: { reference: number; current: number }; columns: UploadColumn[];
  target_candidates: string[]; segment_candidates: string[]; configured: boolean; title?: string | null;
  spec?: UploadSpec | null;
}
export interface UploadSpec {
  title: string; symptom: string; notes?: string; target: string; positive_label: string;
  metric: 'f1' | 'recall' | 'precision' | 'balanced_accuracy';
  model_family: 'logistic_regression' | 'hist_gradient_boosting' | 'random_forest' | 'xgboost' | 'lightgbm';
  score_column?: string | null;
  serving_threshold: number; trained_threshold?: number | null;
  segment_column?: string | null; segment_value?: string | null; segment_max_drop?: number;
  exclude_columns?: string[]; packages_before?: Record<string, string>; packages_after?: Record<string, string>;
  error_log?: string; docs_urls?: string[];
}
export interface Sample {
  id: string; title: string; symptom: string; notes: string;
  suggested: Partial<UploadSpec>;
}

export const isTerminal = (s: Status) => TERMINAL.includes(s);

export function decisionTone(d?: string | null): 'success' | 'danger' | 'warning' | 'neutral' | 'accent' {
  if (d === 'CORRECTION_VERIFIED') return 'success';
  if (d === 'REJECT' || d === 'FAILED' || d === 'BLOCKED') return 'danger';
  if (d === 'INCONCLUSIVE' || d === 'DIAGNOSIS_SUPPORTED' || d === 'CANCELLED') return 'warning';
  return 'accent';
}

const ACRONYMS: Record<string, string> = { api: 'API', f1: 'F1', psi: 'PSI', ci: 'CI', auc: 'AUC', roc: 'ROC', sha256: 'SHA-256' };
export const pretty = (s: string) =>
  s.replace(/_/g, ' ').toLowerCase().split(' ').map((w) => ACRONYMS[w] ?? w).join(' ').replace(/^[a-z]/, (c) => c.toUpperCase());
