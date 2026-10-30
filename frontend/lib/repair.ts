"use client";

import { useCallback, useEffect, useState } from "react";
import { API_BASE } from "./api";
import { getAccessToken } from "./supabase";
import generatedReplays from "@/data/repair/generated-replays.json";
import rejected from "@/data/repair/v1.json";
import verified from "@/data/repair/v2.json";

export interface Check {
  name: string;
  status: string;
  detail: string;
  evidence: Record<string, unknown>;
}
export interface Receipt {
  decision: string;
  checks: Check[];
  reasons: string[];
  runner: string;
  receipt_digest?: string;
  metrics?: {
    holdout?: {
      metric: string;
      candidate: number;
      baseline?: number;
      rows: number;
    };
    delta?: {
      delta: number;
      ci_low: number;
      ci_high: number;
      relative?: boolean;
    };
    segments?: {
      column: string;
      metric: string;
      by_group: Record<
        string,
        { candidate: number; baseline: number; delta: number; rows: number }
      >;
      too_small: string[];
    };
  };
  holdout_opened?: boolean;
}
export interface Candidate {
  candidate: {
    candidate_id: string;
    hypothesis_id?: string;
    diff: string;
    diff_digest: string;
    files_changed: string[];
    status: string;
    summary: string;
    iterations: number;
  };
  steps: Step[];
  stop_reason: string;
  diff_lines: number;
  skipped_files?: string[];
  code_receipt?: Receipt | null;
  screen_receipt?: Receipt | null;
  eligible: boolean;
}
export interface Step {
  n: number;
  thought: string;
  action: string;
  target: string;
  ok: boolean;
  denied?: string | null;
  exit_code?: number | null;
  excerpt: string;
}
export interface Hypothesis {
  id: string;
  cause: string;
  evidence: string;
  check: string;
  files: string[];
  proposed_by?: string;
  status?: "untested" | "confirmed" | "refuted" | "unrunnable";
  probe?: { path: string; pattern: string; expect: string } | null;
}
export interface RepairEvent {
  seq: number;
  type: string;
  actor: string;
  at?: number | string;
  data?: Record<string, unknown>;
  [key: string]: unknown;
}
export interface TierUsage {
  calls?: number;
  model?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  est_cost_usd?: number;
  budget?: number;
}
export interface Report {
  task_id: string;
  mode: string;
  runner: string;
  provider?: string | null;
  contract_digest: string;
  failure_excerpt: string;
  hypotheses: Hypothesis[];
  candidates: Candidate[];
  finalist?: string | null;
  final_ml_receipt?: Receipt | null;
  decision: string;
  reasons: string[];
  usage: Record<string, TierUsage | number>;
  calls: {
    tier?: string;
    model?: string;
    ok: boolean;
    prompt_tokens?: number;
    completion_tokens?: number;
    est_cost_usd?: number;
  }[];
  events: RepairEvent[];
  seconds: number;
  recorded?: boolean;
  replay_id?: string;
  source_file?: string;
  source_sha256?: string;
  sample?: string;
  target?: string;
  image?: string | null;
  data_profile?: {
    columns: number | unknown[];
    flagged: { column: string; psi?: number; missing_shift?: number }[];
  };
  pull_request?: {
    url?: string;
    number?: number;
    branch?: string;
    error?: string;
  };
  maintainability_notes?: string[];
}
export interface Task {
  id: string;
  target: string;
  status: string;
  version: number;
  decision?: string | null;
  repo_name?: string | null;
  sample?: string | null;
  source?: string | null;
  cost_usd?: number | null;
  seconds?: number | null;
  created_at: number;
  error?: string | null;
  engine?: string;
  fail_to_pass?: string[];
  ml_invariants?: string[];
}
export interface Status {
  configured: boolean;
  provider?: string | null;
  models?: Record<string, string>;
  successful_calls_7d: number;
  failed_calls_7d?: number;
  last_model_used?: string | null;
  runner: string;
  isolated: boolean;
  live_runs: boolean;
  invite_required: boolean;
  worker_seen_s_ago?: number | null;
  verifier?: string;
  daily_cap_usd?: number;
  access_policy?: {judge_only: boolean; daily_runs_per_workspace: number; cooldown_seconds: number; max_active_tasks: number; total_estimated_usd_cap: number | null; reason: string};
  capabilities?: { task_limits?: boolean; teams?: boolean };
}
export interface Sample {
  name: string;
  title: string;
  task: string;
  failing_tests: string[];
  invariants: string[];
}
export interface StagedRepo {
  id: string;
  sample?: string | null;
  source: { kind: string; url?: string; sample?: string; name?: string };
  sha: string;
  upstream_sha: string;
  file_count: number;
  files: string[];
  contract_yaml: string;
  contract_errors: string[];
  contract_source: string;
  needs_input?: string[];
  unpinned_requirements?: string[];
  tests?: Discovery | null;
}
export interface Discovery {
  outcomes: Record<string, string>;
  failing: string[];
}
export const REPLAYS: Record<string, Report> = {
  ...(generatedReplays as unknown as Record<string, Report>),
  churn_live_prompt_v1: rejected as unknown as Report,
  churn_live_prompt_v2: verified as unknown as Report,
};
export const VERIFIED_REPLAY = "/repair/replay/churn_live_prompt_v2";
export const REJECTED_REPLAY = "/repair/replay/churn_live_prompt_v1";
export const terminal = (s: string) =>
  [
    "REVIEW_READY",
    "PR_DRAFTED",
    "REJECTED",
    "INCONCLUSIVE",
    "BLOCKED",
    "FAILED",
    "CANCELLED",
  ].includes(s);
export const label = (s?: string | null) =>
  (s || "Not reported")
    .toLowerCase()
    .replaceAll("_", " ")
    .replace(/^./, (c) => c.toUpperCase());
export const money = (n?: number | null) =>
  n == null ? "Not reported" : `$${n.toFixed(n < 1 ? 4 : 2)}`;
export const duration = (s?: number | null) =>
  s == null ? "Not reported" : `${Math.floor(s / 60)}m ${Math.floor(s % 60)}s`;
export const totalCost = (report: Report) =>
  typeof report.usage.total_est_cost_usd === "number"
    ? report.usage.total_est_cost_usd
    : null;
export const tiers = (report: Report): [string, TierUsage][] =>
  Object.entries(report.usage).filter(
    (x): x is [string, TierUsage] => typeof x[1] === "object",
  );
export const details = (event: RepairEvent) => ({
  ...event,
  ...(event.data || {}),
});

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}
function session(): string {
  const key = "autopilot-session";
  let value = localStorage.getItem(key);
  if (!value || !/^[A-Za-z0-9_-]{16,64}$/.test(value)) {
    value = crypto.randomUUID().replaceAll("-", "");
    localStorage.setItem(key, value);
  }
  return value;
}
export async function api<T>(
  path: string,
  init: RequestInit = {},
  text = false,
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Autopilot-Session", session());
  const token = await getAccessToken();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const repairToken = sessionStorage.getItem("repair-token");
  if (repairToken) {
    headers.set("X-Autopilot-Token", repairToken);
    const workspace = localStorage.getItem("repair-workspace");
    if (workspace) headers.set("X-Workspace", workspace);
  }
  if (init.body && !(init.body instanceof FormData))
    headers.set("Content-Type", "application/json");
  const controller = new AbortController();
  const timeout = setTimeout(
    () => controller.abort(),
    path === "/tasks" && init.method === "POST" ? 960000 : path.endsWith("/discover") ? 180000 : 45000,
  );
  try {
    const res = await fetch(`${API_BASE}/api/v1/v5${path}`, {
      ...init,
      headers,
      signal: init.signal || controller.signal,
      cache: "no-store",
    });
    if (!res.ok) {
      let reason = `The server returned ${res.status}.`;
      try {
        const body = await res.json();
        reason =
          typeof body.detail === "string"
            ? body.detail
            : Array.isArray(body.detail)
              ? body.detail
                  .map(
                    (e: { loc?: string[]; msg?: string }) =>
                      `${e.loc?.slice(1).join(".")}: ${e.msg}`,
                  )
                  .join("; ")
              : reason;
      } catch {}
      if (res.status === 401 && repairToken) {
        sessionStorage.removeItem("repair-token");
        localStorage.removeItem("repair-workspace");
        sessionStorage.setItem(
          "repair-auth-error",
          "Your sign-in expired. Sign in again from Workspace settings.",
        );
        window.dispatchEvent(new Event("repair-identity"));
        reason = "Your sign-in expired. Sign in again from Workspace settings.";
      }
      throw new ApiError(res.status, reason);
    }
    return (
      res.status === 204
        ? undefined
        : text
          ? await res.text()
          : await res.json()
    ) as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new Error(
      e instanceof DOMException && e.name === "AbortError"
        ? "The request timed out. Check the connection and retry."
        : "Cannot reach the repair service. Check your connection or watch a recorded repair.",
    );
  } finally {
    clearTimeout(timeout);
  }
}
export function useResource<T>(path: string | null, poll = 0) {
  const [data, setData] = useState<T | null>(null),
    [error, setError] = useState<string | null>(null),
    [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((v) => v + 1), []);
  useEffect(() => {
    if (!path) {
      setData(null);
      setLoading(false);
      return;
    }
    let alive = true,
      busy = false;
    const load = async () => {
      if (busy) return;
      busy = true;
      try {
        const result = await api<T>(path);
        if (alive) {
          setData(result);
          setError(null);
        }
      } catch (e) {
        if (alive) setError((e as Error).message);
      } finally {
        busy = false;
        if (alive) setLoading(false);
      }
    };
    setData(null);
    setLoading(true);
    void load();
    const timer = poll ? setInterval(load, poll) : undefined;
    return () => {
      alive = false;
      if (timer) clearInterval(timer);
    };
  }, [path, poll, revision]);
  return { data, error, loading, reload };
}
export function download(name: string, content: string, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export async function copy(text: string) {
  await navigator.clipboard.writeText(text);
}
export const safeGithub = (url: string) =>
  /^https:\/\/github\.com\/[\w.-]+\/[\w.-]+\/pull\/\d+\/?$/.test(url);
