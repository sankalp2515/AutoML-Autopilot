import type {
  Run,
  RunDetail,
  Artifact,
  LLMStat,
  AgentTimingEntry,
  Results,
  FeatureSchema,
  DeploymentInfo,
  Prediction,
  PredictionLogEntry,
  DriftReport,
  Notebook,
} from './types';

import { getAccessToken } from './supabase';

export const API_BASE =
  // '' (set in the container build) = same origin, proxied to the API by Next.
  process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

export const WS_BASE = API_BASE.replace('http://', 'ws://').replace(
  'https://',
  'wss://'
);

async function apiRequest<T>(path: string, init?: RequestInit): Promise<T> {
  // Attach the Supabase access token when signed in (no-op if auth disabled).
  const token = await getAccessToken();
  const headers = new Headers(init?.headers);
  headers.set('Accept', 'application/json');
  if (token) {
    headers.set('Authorization', `Bearer ${token}`);
  }

  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers,
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try {
      const body = await res.json();
      detail = body.detail || detail;
    } catch {}
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

// ── Runs ──────────────────────────────────────────────────────────────────────

export async function createRun(formData: FormData): Promise<Run> {
  return apiRequest<Run>('/api/v1/runs', { method: 'POST', body: formData });
}

export interface StudioInfo {
  id: string;
  label: string;
  tagline: string;
  available: boolean;
  dataset_contract: string;
}

export async function listStudios(): Promise<{ studios: StudioInfo[] }> {
  return apiRequest<{ studios: StudioInfo[] }>('/api/v1/studios');
}

export async function listRuns(skip = 0, limit = 30): Promise<{ runs: Run[]; total: number }> {
  return apiRequest<{ runs: Run[]; total: number }>(
    `/api/v1/runs?skip=${skip}&limit=${limit}`
  );
}

export async function getRun(runId: string): Promise<RunDetail> {
  return apiRequest<RunDetail>(`/api/v1/runs/${runId}`);
}

export async function getResults(runId: string): Promise<Results> {
  return apiRequest<Results>(`/api/v1/runs/${runId}/results`);
}

// ── Artifacts ─────────────────────────────────────────────────────────────────

export async function listArtifacts(runId: string): Promise<Artifact[]> {
  const data = await apiRequest<{ artifacts: Artifact[] }>(
    `/api/v1/runs/${runId}/artifacts`
  );
  return data.artifacts;
}

export function artifactUrl(runId: string, name: string): string {
  return `${API_BASE}/api/v1/runs/${runId}/artifacts/${name}`;
}

export async function fetchArtifactText(runId: string, name: string): Promise<string> {
  const res = await fetch(artifactUrl(runId, name));
  if (!res.ok) throw new Error(`Artifact fetch failed: ${res.status}`);
  return res.text();
}

// ── Observability ─────────────────────────────────────────────────────────────

// Backend response shape for /llm-stats
interface LLMStatsApiResponse {
  run_id: string;
  per_agent: {
    agent: string;
    calls: number;
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
    avg_latency_ms: number;
    max_latency_ms: number;
    estimated_cost_usd: number;
  }[];
  totals: {
    total_calls: number;
    total_tokens: number;
    total_cost_usd: number;
    avg_latency_ms: number;
  };
}

export async function getLLMStats(runId: string): Promise<{ stats: LLMStat[]; totals: LLMStat }> {
  const data = await apiRequest<LLMStatsApiResponse>(
    `/api/v1/runs/${runId}/llm-stats`
  );
  const stats: LLMStat[] = data.per_agent.map((a) => ({
    agent_name: a.agent,
    n_calls: a.calls,
    total_prompt_tokens: a.prompt_tokens,
    total_completion_tokens: a.completion_tokens,
    total_tokens: a.total_tokens,
    total_cost_usd: a.estimated_cost_usd,
    avg_latency_ms: a.avg_latency_ms,
  }));
  const totals: LLMStat = {
    agent_name: 'total',
    n_calls: data.totals.total_calls,
    total_prompt_tokens: stats.reduce((s, a) => s + a.total_prompt_tokens, 0),
    total_completion_tokens: stats.reduce((s, a) => s + a.total_completion_tokens, 0),
    total_tokens: data.totals.total_tokens,
    total_cost_usd: data.totals.total_cost_usd,
    avg_latency_ms: data.totals.avg_latency_ms,
  };
  return { stats, totals };
}

export async function getAgentTimeline(runId: string): Promise<{ timeline: AgentTimingEntry[] }> {
  return apiRequest<{ timeline: AgentTimingEntry[] }>(
    `/api/v1/runs/${runId}/agent-timeline`
  );
}

// ── Phase 3: inference & drift ────────────────────────────────────────────────

export async function deployModel(runId: string): Promise<{ deployment_id: string; status: string }> {
  return apiRequest(`/api/v1/runs/${runId}/deploy`, { method: 'POST' });
}

export async function stopDeployment(runId: string): Promise<{ status: string }> {
  return apiRequest(`/api/v1/runs/${runId}/deploy`, { method: 'DELETE' });
}

export async function listDeployments(): Promise<{ deployments: DeploymentInfo[] }> {
  return apiRequest('/api/v1/deployments');
}

export async function getFeatureSchema(
  runId: string
): Promise<{ features: FeatureSchema[]; target_column: string | null; task_type: string | null }> {
  return apiRequest(`/api/v1/runs/${runId}/schema`);
}

export async function predictRows(
  runId: string,
  rows: Record<string, unknown>[]
): Promise<{ predictions: Prediction[]; model: string; latency_ms: number }> {
  return apiRequest(`/api/v1/runs/${runId}/predict`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ rows }),
  });
}

export async function getPredictionLog(
  runId: string,
  limit = 50
): Promise<{ predictions: PredictionLogEntry[] }> {
  return apiRequest(`/api/v1/runs/${runId}/predictions?limit=${limit}`);
}

export async function getDriftReport(runId: string): Promise<DriftReport> {
  return apiRequest(`/api/v1/runs/${runId}/drift`);
}

export interface DecisionAnalysis {
  run_id: string;
  n_holdout: number;
  positive_rate: number;
  threshold_sweep: { threshold: number; precision: number; recall: number; f1: number; contacts: number; contacts_pct: number }[];
  campaign: {
    top_k: { contact_pct: number; customers_contacted: number; conversions_captured: number; conversions_retained_pct: number; contacts_avoided: number; lift_vs_random: number | null }[];
    contact_all: { customers_contacted: number; conversions: number; positive_rate_pct: number };
  };
  calibration: {
    brier_score: number;
    expected_calibration_error: number | null;
    well_calibrated: boolean;
    reliability_curve: { predicted: number; actual: number }[];
  };
}

export async function getDecisionAnalysis(runId: string): Promise<DecisionAnalysis> {
  return apiRequest(`/api/v1/runs/${runId}/decision-analysis`);
}

export interface RegistryVersion {
  run_id: string; version: number; algorithm: string | null; score: number | null;
  primary_metric: string | null; trust_grade: string | null;
  status: 'champion' | 'challenger'; created_at: string | null;
}
export interface RegistryFamily {
  signature: string; dataset: string; task_type: string | null; primary_metric: string | null;
  versions: RegistryVersion[]; champion_run_id: string | null;
  promotable_challenger: { run_id: string; score: number; beats_champion_by: number } | null;
}
export async function getRegistry(): Promise<{ families: RegistryFamily[] }> {
  return apiRequest('/api/v1/registry');
}
export async function deployRun(runId: string): Promise<unknown> {
  return apiRequest(`/api/v1/runs/${runId}/deploy`, { method: 'POST' });
}
export async function undeployRun(runId: string): Promise<unknown> {
  return apiRequest(`/api/v1/runs/${runId}/deploy`, { method: 'DELETE' });
}

// ── Notebook ──────────────────────────────────────────────────────────────────

export async function fetchNotebook(runId: string): Promise<Notebook> {
  const text = await fetchArtifactText(runId, 'notebook');
  return JSON.parse(text) as Notebook;
}

// ── WebSocket ─────────────────────────────────────────────────────────────────

export function connectWebSocket(
  runId: string,
  onMessage: (msg: Record<string, unknown>) => void,
  onClose?: () => void
): WebSocket {
  // Must match the backend route: /ws/runs/{run_id}/progress
  const url = `${WS_BASE}/ws/runs/${runId}/progress`;
  const ws = new WebSocket(url);

  ws.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data as string) as Record<string, unknown>;
      onMessage(msg);
    } catch {}
  };

  ws.onerror = () => {
    ws.close();
  };

  if (onClose) {
    ws.onclose = onClose;
  }

  return ws;
}

// ── Backlog P10–P15 client functions ──────────────────────────────────────────
export async function compareRuns(a: string, b: string) {
  return apiRequest<Record<string, unknown>>(`/api/v1/runs/compare?a=${a}&b=${b}`);
}

export async function askModel(runId: string, question: string) {
  return apiRequest<{ answer: string; cited_agents?: string[] }>(
    `/api/v1/runs/${runId}/ask`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }) }
  );
}

export async function explainPrediction(runId: string, rows: Record<string, unknown>[]) {
  return apiRequest<{ explanations: { feature: string; contribution: number }[][] }>(
    `/api/v1/runs/${runId}/explain`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ rows }) }
  );
}

export async function fairnessAudit(runId: string, columns: string) {
  return apiRequest<Record<string, unknown>>(
    `/api/v1/runs/${runId}/fairness?columns=${encodeURIComponent(columns)}`
  );
}

export async function retrainChallenger(runId: string) {
  return apiRequest<{ champion_run_id: string; challenger_run_id: string; note: string }>(
    `/api/v1/runs/${runId}/retrain`, { method: 'POST' }
  );
}

export function batchPredictUrl(runId: string): string {
  return `${API_BASE}/api/v1/runs/${runId}/batch-predict`;
}

// ── Trust audit (the Skeptic) ───────────────────────────────────────────────
export interface TrustProbe {
  id: string;
  category: string;
  title: string;
  severity: string;
  status: 'pass' | 'warn' | 'fail' | 'skip' | 'error';
  headline: string;
  evidence: Record<string, unknown>;
  recommendation: string;
  score: number | null;
}

export interface TrustReport {
  schema_version: number;
  task_type?: string | null;
  primary_metric?: string | null;
  trust_score: number | null;
  grade: string;
  n_pass: number;
  n_warn: number;
  n_fail: number;
  n_skip: number;
  probes: TrustProbe[];
  summary?: string;
  error?: string;
  generated_at?: string;
}

export async function getTrustReport(runId: string): Promise<TrustReport> {
  return apiRequest<TrustReport>(`/api/v1/runs/${runId}/trust`);
}

export async function runTrustAudit(runId: string): Promise<TrustReport> {
  return apiRequest<TrustReport>(`/api/v1/runs/${runId}/trust`, { method: 'POST' });
}

export async function retryRun(runId: string): Promise<Run> {
  return apiRequest<Run>(`/api/v1/runs/${runId}/retry`, { method: 'POST' });
}

export async function rerunRun(runId: string, excludeColumns: string[]): Promise<Run> {
  return apiRequest<Run>(`/api/v1/runs/${runId}/rerun`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ exclude_columns: excludeColumns }),
  });
}

// ── Investigation (the Hypothesis Loop) ─────────────────────────────────────
export interface InvestigationHypothesis {
  id: string;
  statement: string;
  rationale: string;
  severity: string;
  primitive: string;
  params: Record<string, unknown>;
  expected: { direction: string; threshold: number };
  verdict: 'confirmed' | 'refuted' | 'inconclusive';
  observed?: number;
  reason?: string;
  evidence: Record<string, unknown>;
}

export interface InvestigationReport {
  schema_version: number;
  run_id: string;
  n_hypotheses: number;
  n_confirmed: number;
  n_refuted: number;
  n_inconclusive: number;
  truncated: boolean;
  summary: string;
  error?: string;
  generated_at?: string;
  hypotheses: InvestigationHypothesis[];
}

export async function getInvestigation(runId: string): Promise<InvestigationReport> {
  return apiRequest<InvestigationReport>(`/api/v1/runs/${runId}/investigate`);
}

export async function runInvestigation(runId: string): Promise<InvestigationReport> {
  return apiRequest<InvestigationReport>(`/api/v1/runs/${runId}/investigate`, { method: 'POST' });
}

// ── Settings control panel ──────────────────────────────────────────────────
export interface ConfigField {
  key: string;
  label: string;
  group: string;
  type: 'bool' | 'int' | 'float' | 'str';
  restart: boolean;
  secret: boolean;
  help: string;
  value: boolean | number | string;
  is_set?: boolean;
}

export async function getConfig(): Promise<{
  fields: ConfigField[];
  groups: string[];
  api_key_protected: boolean;
}> {
  return apiRequest('/api/v1/config');
}

export async function patchConfig(
  changes: Record<string, unknown>,
  adminKey?: string
): Promise<{ applied: string[]; restart_required: string[]; warning: string | null }> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (adminKey) headers['X-API-Key'] = adminKey;
  return apiRequest('/api/v1/config', {
    method: 'PATCH',
    headers,
    body: JSON.stringify({ changes }),
  });
}
