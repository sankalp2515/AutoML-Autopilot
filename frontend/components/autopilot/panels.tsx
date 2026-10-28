'use client';
/**
 * Investigation panels. Each panel answers one reviewer question (PRD §9) and
 * renders only persisted backend data — no client-side metric computation.
 */
import type { CSSProperties, ReactNode } from 'react';
import { Badge, Card } from '@/components/ui';
import {
  PROVIDER_LABEL, TEMPLATE_INFO, modelLabel, pretty, templateLabel, type CandidateScore, type Experiment, type Finding,
  type Hypothesis, type Investigation, type Status, type Verification,
} from '@/lib/autopilot';

const th: CSSProperties = { textAlign: 'left', fontSize: 10.5, fontWeight: 600, color: 'var(--t3)', padding: '8px 10px', borderBottom: '1px solid var(--bd)', textTransform: 'uppercase', letterSpacing: '.5px' };
const td: CSSProperties = { fontSize: 12.5, padding: '8px 10px', borderBottom: '1px solid var(--bd2)', verticalAlign: 'top' };
const mono: CSSProperties = { fontFamily: 'var(--font-mono), monospace', fontSize: 11.5 };

export function Panel({ title, question, children, id, right }: { title: ReactNode; question: string; children: ReactNode; id: string; right?: ReactNode }) {
  return (
    <Card padding={0} aria-labelledby={id} style={{ overflow: 'hidden', minWidth: 0 }}>
      <div style={{ padding: '14px 18px 12px', borderBottom: '1px solid var(--bd)', display: 'flex', gap: 12, alignItems: 'flex-start' }}>
        <div style={{ flex: 1 }}>
          <h2 id={id} style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>{title}</h2>
          <p style={{ fontSize: 12, color: 'var(--t3)', margin: '3px 0 0' }}>{question}</p>
        </div>
        {right}
      </div>
      <div style={{ padding: '14px 18px' }}>{children}</div>
    </Card>
  );
}

function Table({ head, children, label }: { head: string[]; children: ReactNode; label: string }) {
  return (
    <div style={{ overflowX: 'auto', margin: '0 -18px' }}>
      <table aria-label={label} style={{ width: '100%', borderCollapse: 'collapse', minWidth: 560 }}>
        <thead><tr>{head.map((h) => <th key={h} scope="col" style={th}>{h}</th>)}</tr></thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

// ── Stage timeline ───────────────────────────────────────────────────────────
const STAGES: { key: string; label: string; states: Status[] }[] = [
  { key: 'intake', label: 'Protocol frozen', states: ['CREATED', 'VALIDATING'] },
  { key: 'evidence', label: 'Baseline & findings', states: ['BASELINING', 'INVESTIGATING'] },
  { key: 'research', label: 'External evidence', states: ['RESEARCHING'] },
  { key: 'hypotheses', label: 'Hypotheses & selection', states: ['PROPOSING', 'POLICY_CHECK'] },
  { key: 'execute', label: 'Bounded test', states: ['EXECUTING', 'OBSERVING'] },
  { key: 'correct', label: 'Correction', states: ['TESTING_CORRECTION'] },
  { key: 'verify', label: 'Independent verifier', states: ['VERIFYING'] },
  { key: 'decide', label: 'Decision', states: ['REVIEW_READY', 'APPROVED', 'REJECTED', 'INCONCLUSIVE', 'BLOCKED', 'FAILED', 'CANCELLED'] },
];

export function StageTimeline({ inv }: { inv: Investigation }) {
  const idx = STAGES.findIndex((s) => s.states.includes(inv.status));
  const visited = new Set<string>();
  if (inv.findings.length) { visited.add('intake'); visited.add('evidence'); }
  if (!['PENDING', 'NOT_NEEDED'].includes(inv.research.state)) visited.add('research');
  if (inv.hypotheses.length || inv.rankings.length) visited.add('hypotheses');
  if (inv.experiments.some((e) => e.kind === 'diagnostic')) visited.add('execute');
  if (inv.correction) visited.add('correct');
  if (inv.verification) visited.add('verify');
  if (inv.decision) visited.add('decide');
  return (
    <ol aria-label="Investigation stages" style={{ listStyle: 'none', padding: 0, margin: 0, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
      {STAGES.map((s, i) => {
        const current = i === idx && !inv.decision;
        const done = visited.has(s.key) && !current;
        const skipped = !visited.has(s.key) && !current && (idx > i || !!inv.decision);
        return (
          <li key={s.key} aria-current={current ? 'step' : undefined}
            style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '6px 10px', borderRadius: 8, fontSize: 11.5, fontWeight: 600,
                     border: `1px solid ${current ? 'var(--acc)' : 'var(--bd)'}`,
                     background: current ? 'var(--accw)' : done ? 'var(--sur)' : 'transparent',
                     color: skipped ? 'var(--t4)' : current ? 'var(--acc)' : 'var(--t2)' }}>
            <span aria-hidden="true" style={{ width: 7, height: 7, borderRadius: '50%', background: current ? 'var(--acc)' : done ? '#16a34a' : 'var(--t4)',
                                              animation: current ? 'ds-pulse 1.2s ease-in-out infinite' : undefined }} />
            {s.label}{skipped ? ' · not needed' : ''}
          </li>
        );
      })}
    </ol>
  );
}

// ── Provenance: every claim is measured, retrieved, or inferred ──────────────
const PROV: Record<'OBSERVED' | 'RETRIEVED' | 'INFERRED', { tone: 'success' | 'accent' | 'warning'; title: string }> = {
  OBSERVED: { tone: 'success', title: 'Measured by deterministic code on this incident' },
  RETRIEVED: { tone: 'accent', title: 'Fetched from an external source by Tavily; context, not proof' },
  INFERRED: { tone: 'warning', title: 'Proposed by a model or rule; must be tested before it counts' },
};
export function Provenance({ kind }: { kind: keyof typeof PROV }) {
  return <Badge tone={PROV[kind].tone} title={PROV[kind].title}>{kind.toLowerCase()}</Badge>;
}

// ── Verdict summary: the whole investigation in four lines ───────────────────
const RETRAIN_COST = 10;   // MODEL_FAMILY_CHALLENGER, the classic "just retrain" response

function Fact({ label, children, tone }: { label: string; children: ReactNode; tone?: string }) {
  return (
    <div style={{ minWidth: 0 }}>
      <div className="ds-mono" style={{ fontSize: 10, letterSpacing: '1px', textTransform: 'uppercase', color: 'var(--t3)' }}>{label}</div>
      <div style={{ fontSize: 14, fontWeight: 650, marginTop: 4, lineHeight: 1.35, color: tone ?? 'var(--t1)' }}>{children}</div>
    </div>
  );
}

export function VerdictSummary({ inv }: { inv: Investigation }) {
  const supported = inv.hypotheses.find((h) => h.status === 'SUPPORTED');
  const v = inv.verification;
  const delta = v?.metrics?.delta;
  const f = v?.metrics?.final_test;
  const diag = inv.experiments.filter((e) => e.kind === 'diagnostic').length;
  const challenger = inv.scenario?.kind === 'challenger_review';
  const cause = supported ? pretty(supported.cause) : inv.decision ? 'Not established' : '…';
  const fix = inv.correction ? templateLabel(inv.correction.template_id) : 'None proposed';
  const failed = v?.checks.filter((c) => c.status !== 'PASS').map((c) => pretty(c.check)) ?? [];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(190px, 100%), 1fr))', gap: 16,
                  padding: '14px 0 2px', borderTop: '1px solid var(--bd2)', marginTop: 12 }}>
      {challenger ? (
        <>
          <Fact label="Candidate under review">{v?.candidate ?? '…'}</Fact>
          <Fact label="Verifier checks" tone={failed.length ? '#ef4444' : v ? '#16a34a' : undefined}>
            {v ? (failed.length ? `Failed: ${failed.join(', ')}` : 'All passed') : '…'}
          </Fact>
        </>
      ) : (
        <>
          <Fact label="Root cause">{cause}</Fact>
          <Fact label="Fix tested">{fix}</Fact>
        </>
      )}
      <Fact label={`Result on held-out test${v ? ` (${v.metric})` : ''}`}
        tone={delta ? (delta.ci_low > 0 ? '#16a34a' : delta.ci_high < 0 ? '#ef4444' : undefined) : undefined}>
        {f && delta ? (
          <>{f.incumbent[v!.metric]} → {f.candidate[v!.metric]}
            <span style={{ display: 'block', fontSize: 11.5, fontWeight: 500, color: 'var(--t2)' }}>
              Δ {delta.delta >= 0 ? '+' : ''}{delta.delta} · 95% CI [{delta.ci_low}, {delta.ci_high}]
            </span></>
        ) : v ? 'Stopped before the holdout was opened' : 'No candidate verified'}
        {v?.mode === 'non_inferior' && f && (
          <span style={{ display: 'block', fontSize: 11.5, fontWeight: 500, color: 'var(--t2)' }}>
            goal: restore the pipeline with no meaningful loss (CI low above −0.02)
          </span>
        )}
      </Fact>
      <Fact label="Spend">
        {diag} test{diag === 1 ? '' : 's'} · {inv.budget.cost_units_used} cost units
        <span style={{ display: 'block', fontSize: 11.5, fontWeight: 500, color: 'var(--t2)' }}>
          a full retrain alone costs {RETRAIN_COST} · {inv.budget.cpu_seconds.toFixed(1)} CPU s
        </span>
      </Fact>
    </div>
  );
}

// ── Stakeholder summary (small model, number-checked) ────────────────────────
export function StakeholderSummary({ inv }: { inv: Investigation }) {
  const s = inv.stakeholder_summary;
  const call = inv.summary_calls?.[inv.summary_calls.length - 1];
  if (!s) return null;
  return (
    <div style={{ marginTop: 12, padding: '10px 12px', background: 'var(--sur)', borderRadius: 8 }}>
      <div className="ds-mono" style={{ fontSize: 10, letterSpacing: '1px', textTransform: 'uppercase', color: 'var(--t3)' }}>
        For your manager{call?.model ? ` · written by ${modelLabel(call.model)}` : ''}
      </div>
      {s.status === 'OK' ? (
        <p style={{ fontSize: 13, margin: '4px 0 0', lineHeight: 1.5 }}>{s.text}</p>
      ) : (
        <p style={{ fontSize: 12, margin: '4px 0 0', color: 'var(--t2)' }}>
          {s.status === 'REJECTED_UNGROUNDED'
            ? 'The model summary was discarded: it contained a number that is not in the verified facts. The decision above is authoritative.'
            : 'No model summary (provider unavailable). The decision above is authoritative.'}
        </p>
      )}
      <p style={{ fontSize: 10.5, color: 'var(--t3)', margin: '4px 0 0' }}>Every number in this summary was checked against the verified facts.</p>
    </div>
  );
}

// ── Model calls and cost ─────────────────────────────────────────────────────
export function ModelCallsPanel({ inv }: { inv: Investigation }) {
  const rows = [
    ...inv.planner.calls.map((c) => ({ c, role: c.operation.endsWith('retry') ? 'Plan (corrective retry)' : 'Plan hypotheses' })),
    ...(inv.summary_calls ?? []).map((c) => ({ c, role: 'Stakeholder summary' })),
  ];
  const tavily = inv.research.calls.length;
  if (!rows.length && !tavily) return null;
  const total = rows.reduce((a, r) => a + (r.c.est_cost_usd ?? 0), 0);
  return (
    <Panel id="p-models" title="Model calls & cost" question="Which Nemotron model did what, how long it took and what it cost."
      right={<Badge tone="neutral">≈ ${total.toFixed(4)} per run</Badge>}>
      <Table label="Model calls" head={['Role', 'Model · provider', 'Result', 'Latency', 'Tokens in / out', 'Est. cost']}>
        {rows.map(({ c, role }) => (
          <tr key={c.correlation_id}>
            <td style={td}>{role}</td>
            <td style={td}>{modelLabel(c.model)}<span style={{ display: 'block', fontSize: 11, color: 'var(--t3)' }}>{PROVIDER_LABEL[c.provider] ?? c.provider}</span></td>
            <td style={{ ...td, fontSize: 11.5, color: c.ok ? '#16a34a' : '#ef4444' }}>{c.ok ? 'accepted' : c.error}</td>
            <td style={{ ...td, ...mono }}>{(c.latency_ms / 1000).toFixed(1)} s</td>
            <td style={{ ...td, ...mono }}>{c.prompt_tokens ?? '—'} / {c.completion_tokens ?? '—'}</td>
            <td style={{ ...td, ...mono }}>{c.est_cost_usd != null ? `$${c.est_cost_usd.toFixed(5)}` : '—'}</td>
          </tr>
        ))}
      </Table>
      <p style={{ fontSize: 11, color: 'var(--t3)', margin: '8px 0 0' }}>
        Ultra does the reasoning; a small model writes the summary. Costs are estimates at Nebius Token Factory list prices (Oct 2026), not a bill.
        {tavily > 0 && ` Plus ${tavily} Tavily call${tavily === 1 ? '' : 's'}.`}
      </p>
    </Panel>
  );
}

// ── Planner receipt ──────────────────────────────────────────────────────────
export function PlannerReceipt({ inv }: { inv: Investigation }) {
  const p = inv.planner;
  const call = p.calls[p.calls.length - 1];
  const usedModel = p.used === 'nemotron' && call?.ok;
  return (
    <div style={{ fontSize: 11.5, color: 'var(--t2)', background: 'var(--sur)', borderRadius: 8, padding: '8px 10px', marginBottom: 12, lineHeight: 1.5 }}>
      {usedModel ? (
        <>Proposed by <strong>{modelLabel(call.model)}</strong> via {PROVIDER_LABEL[call.provider] ?? call.provider} ·
          {' '}{(call.latency_ms / 1000).toFixed(1)} s{call.prompt_tokens ? ` · ${call.prompt_tokens}+${call.completion_tokens ?? 0} tokens` : ''} ·
          {' '}<span style={mono}>{call.request_id}</span>
          <span style={{ display: 'block', color: 'var(--t3)' }}>Reply was schema-checked; every finding it cites exists. The model cannot run code or report metrics.</span></>
      ) : p.used?.startsWith('deterministic') ? (
        <>Proposed by the <strong>deterministic rule planner</strong>{call && !call.ok ? ` (model call failed: ${call.error})` : ''}.</>
      ) : <>Planner: {p.used ?? p.requested}</>}
      {p.note && <span style={{ display: 'block', color: '#b45309', marginTop: 2 }}>{p.note}</span>}
    </div>
  );
}

// ── Evidence map ─────────────────────────────────────────────────────────────
const sevTone = (s: Finding['severity']) => (s === 'critical' ? 'danger' : s === 'warn' ? 'warning' : 'neutral');

export function FindingsPanel({ findings }: { findings: Finding[] }) {
  return (
    <Panel id="p-findings" title="Evidence map" question="What do we measurably know? (machine-computed, aggregates only)"
      right={<Provenance kind="OBSERVED" />}>
      {findings.length === 0 ? <p style={{ fontSize: 12, color: 'var(--t3)', margin: 0 }}>Computing findings…</p> : (
        <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 6 }}>
          {findings.map((f) => (
            <li key={f.finding_id} style={{ display: 'flex', gap: 10, alignItems: 'baseline' }}>
              <span style={{ ...mono, color: 'var(--t3)', minWidth: 28 }}>{f.finding_id}</span>
              <span style={{ flex: 1, fontSize: 12.5 }}>
                {f.title}
                <span style={{ display: 'block', fontSize: 11, color: 'var(--t3)' }}>{f.method} · n={f.sample_count}</span>
              </span>
              <Badge tone={sevTone(f.severity)}>{f.severity}</Badge>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// ── Hypotheses ───────────────────────────────────────────────────────────────
const hypTone = (s: Hypothesis['status']) => (s === 'SUPPORTED' ? 'success' : s === 'REFUTED' ? 'danger' : 'neutral');

export function HypothesesPanel({ inv }: { inv: Investigation }) {
  const first = inv.belief_history[0]?.weights ?? {};
  return (
    <Panel id="p-hyp" title="Competing hypotheses" question="Which causes are still plausible, and what would each one predict?"
      right={<span style={{ display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' }}>
        <Provenance kind="INFERRED" />
        <Badge tone="neutral" title="Belief weights are a heuristic, not calibrated probabilities">uncalibrated weights</Badge>
      </span>}>
      {inv.hypotheses.length === 0 ? (
        <p style={{ fontSize: 12, color: 'var(--t3)', margin: 0 }}>
          {inv.scenario?.kind === 'challenger_review' ? 'Challenger review: the submitted model goes straight to the verifier.'
            : inv.decision ? 'No competing causes were warranted by the findings.'
            : inv.status === 'PROPOSING' ? `The planner is proposing competing causes${inv.config.planner === 'deterministic' ? '' : ' (a Nemotron call can take 10–30 s)'}…`
            : inv.status === 'RESEARCHING' ? 'Collecting external evidence before planning…' : 'Waiting for findings…'}
        </p>
      ) : (
        <div style={{ display: 'grid', gap: 12 }}>
          <PlannerReceipt inv={inv} />
          {inv.hypotheses.map((h) => (
            <div key={h.hypothesis_id} style={{ border: '1px solid var(--bd2)', borderRadius: 10, padding: 12, opacity: h.status === 'REFUTED' ? 0.6 : 1 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ ...mono, color: 'var(--t3)' }}>{h.hypothesis_id}</span>
                <strong style={{ fontSize: 13 }}>{pretty(h.cause)}</strong>
                <Badge tone={hypTone(h.status)}>{pretty(h.status)}</Badge>
                {h.proposed_by === 'rule_anchor'
                  ? <Badge tone="warning" title="The model omitted a cause the findings score highly; a rule added it so it is tested too">added by rule</Badge>
                  : <span style={{ fontSize: 11, color: 'var(--t3)' }}>by {h.proposed_by}</span>}
              </div>
              <p style={{ fontSize: 12.5, color: 'var(--t2)', margin: '6px 0 8px', lineHeight: 1.45 }}>{h.statement}</p>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <div role="meter" aria-label={`Belief weight ${h.hypothesis_id}`} aria-valuemin={0} aria-valuemax={1} aria-valuenow={h.belief_weight}
                  style={{ flex: 1, height: 8, background: 'var(--sur)', borderRadius: 4, overflow: 'hidden', position: 'relative' }}>
                  <div style={{ width: `${Math.round(h.belief_weight * 100)}%`, height: '100%', background: h.status === 'SUPPORTED' ? '#16a34a' : 'var(--acc)', transition: 'width .5s' }} />
                </div>
                <span style={{ ...mono, minWidth: 92, textAlign: 'right' }}>
                  {(first[h.hypothesis_id] ?? h.belief_weight).toFixed(2)} → {h.belief_weight.toFixed(2)}
                </span>
              </div>
              <div style={{ fontSize: 11, color: 'var(--t3)', marginTop: 6 }}>
                Evidence: {h.supporting_findings.join(', ') || '—'}
                {h.source_refs.length > 0 && <> · Sources: {h.source_refs.join(', ')}</>}
              </div>
            </div>
          ))}
        </div>
      )}
    </Panel>
  );
}

// ── Selection: why this test, not a retrain ──────────────────────────────────
export function SelectionPanel({ inv }: { inv: Investigation }) {
  const step = inv.rankings[0];
  if (!step) {
    return (
      <Panel id="p-sel" title="Experiment choice" question="Why run this next rather than a retrain?">
        <p style={{ fontSize: 12, color: 'var(--t3)', margin: 0 }}>No selection step yet.</p>
      </Panel>
    );
  }
  const chosen = inv.experiments.filter((e) => e.kind === 'diagnostic').map((e) => e.template_id);
  const renderStep = (r: Investigation['rankings'][number]) => {
    const diag = r.candidates.filter((c) => c.kind === 'diagnostic');
    const fixes = r.candidates.filter((c) => c.kind === 'corrective');
    return (
      <>
        <Table label={`Ranked tests, step ${r.step}`} head={['Test', 'Info gain (bits)', 'Cost', 'Score', 'Status']}>
          {diag.map((c: CandidateScore) => {
            const picked = c.eligible && chosen[r.step - 1] === c.template_id;
            return (
              <tr key={c.template_id} style={{ background: picked ? 'var(--accw)' : undefined }}>
                <td style={td}>
                  <span style={{ fontWeight: picked ? 700 : 550 }}>{templateLabel(c.template_id)}</span>
                  <span style={{ display: 'block', fontSize: 11, color: 'var(--t3)' }}>{TEMPLATE_INFO[c.template_id]?.what}</span>
                </td>
                <td style={{ ...td, ...mono }}>{c.expected_information_bits.toFixed(2)}</td>
                <td style={{ ...td, ...mono }}>{c.estimated_cost_units}</td>
                <td style={{ ...td, ...mono }}>{c.score.toFixed(2)}</td>
                <td style={{ ...td, fontSize: 11.5, color: picked ? 'var(--acc)' : 'var(--t3)', fontWeight: picked ? 700 : 400 }}>{picked ? '▶ selected' : c.reason}</td>
              </tr>
            );
          })}
        </Table>
        {fixes.length > 0 && (
          <p style={{ fontSize: 11.5, color: 'var(--t3)', margin: '8px 0 0' }}>
            Held back until a cause is supported: {fixes.map((c) => `${templateLabel(c.template_id)} (${c.estimated_cost_units})`).join(' · ')}
          </p>
        )}
      </>
    );
  };
  const latest = inv.rankings[inv.rankings.length - 1];
  return (
    <Panel id="p-sel" title="Experiment choice" question="Why run this test next rather than retrain? Score = expected information gain ÷ cost."
      right={<Badge tone="neutral">{inv.rankings.length} step{inv.rankings.length === 1 ? '' : 's'}</Badge>}>
      {inv.rankings.slice(0, -1).map((r) => (
        <details key={r.step} style={{ marginBottom: 10 }}>
          <summary style={{ fontSize: 12, color: 'var(--t2)', cursor: 'pointer' }}>
            Step {r.step}: chose {templateLabel(chosen[r.step - 1] ?? '—')}
          </summary>
          <div style={{ marginTop: 8 }}>{renderStep(r)}</div>
        </details>
      ))}
      <div style={{ fontSize: 11.5, color: 'var(--t3)', marginBottom: 4 }}>Step {latest.step}{inv.rankings.length > 1 ? ' (latest)' : ''}</div>
      {renderStep(latest)}
    </Panel>
  );
}

// ── Executed experiments: predicted vs observed ──────────────────────────────
function fmtValue(v: unknown): ReactNode {
  if (v === null || v === undefined) return '—';
  if (Array.isArray(v)) return v.map(String).join(', ');
  if (typeof v === 'object') {
    return (
      <span style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
        {Object.entries(v as Record<string, unknown>).map(([k, x]) => (
          <span key={k} style={{ background: 'var(--sur)', borderRadius: 5, padding: '1px 6px' }}>
            {k}: {typeof x === 'object' && x !== null ? Object.entries(x).map(([a, b]) => `${a} ${b}`).join(', ') : String(x)}
          </span>
        ))}
      </span>
    );
  }
  return String(v);
}

function Measurements({ m }: { m: Record<string, unknown> }) {
  const keep = Object.entries(m).filter(([k]) => !['installed_parameters', 'psi', 'numeric', 'categorical_psi', 'null_rate_increase', 'non_numeric_share', 'artifact_sha256'].includes(k));
  if (!keep.length) return null;
  return (
    <dl style={{ display: 'grid', gridTemplateColumns: 'max-content minmax(0, 1fr)', gap: '4px 12px', margin: '8px 0 0', fontSize: 11.5 }}>
      {keep.slice(0, 8).map(([k, v]) => (
        <div key={k} style={{ display: 'contents' }}>
          <dt style={{ color: 'var(--t3)' }}>{pretty(k)}</dt>
          <dd style={{ margin: 0, ...mono, wordBreak: 'break-word' }}>{fmtValue(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ExperimentsPanel({ inv }: { inv: Investigation }) {
  return (
    <Panel id="p-exp" title="Executed tests" question="What actually ran, what each hypothesis predicted, and what we observed.">
      {inv.experiments.length === 0 ? <p style={{ fontSize: 12, color: 'var(--t3)', margin: 0 }}>Nothing executed yet.</p> : (
        <div style={{ display: 'grid', gap: 12 }}>
          {inv.experiments.map((e: Experiment) => (
            <div key={e.experiment_id} style={{ border: '1px solid var(--bd2)', borderRadius: 10, padding: 12 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ ...mono, color: 'var(--t3)' }}>{e.experiment_id}</span>
                <strong style={{ fontSize: 13 }}>{templateLabel(e.template_id)}</strong>
                {e.kind === 'corrective' && <Badge tone="neutral">fix</Badge>}
                <Badge tone={e.status === 'DONE' ? 'success' : e.status === 'RUNNING' ? 'accent' : 'danger'}>{pretty(e.status)}</Badge>
                {e.observation && <Badge tone="accent">observed: {pretty(e.observation.bucket)}</Badge>}
                <span style={{ fontSize: 11, color: 'var(--t3)', marginLeft: 'auto' }}>
                  {e.actual_seconds != null ? `${e.actual_seconds}s` : ''} {e.runner_mode ? `· ${e.runner_mode}` : ''} · sig {e.signature.slice(0, 8)}
                </span>
              </div>
              {e.belief_update && e.belief_update.length > 0 && (
                <ul style={{ listStyle: 'none', margin: '8px 0 0', padding: 0, display: 'grid', gap: 3 }}>
                  {e.belief_update.map((b) => (
                    <li key={b.hypothesis_id} style={{ fontSize: 12, display: 'flex', gap: 8 }}>
                      <span style={{ ...mono, color: 'var(--t3)', minWidth: 24 }}>{b.hypothesis_id}</span>
                      <span style={{ flex: 1 }}>{b.predicted ? <>predicted <span style={mono}>{pretty(b.predicted)}</span></> : <span style={{ color: 'var(--t3)' }}>made no prediction</span>}</span>
                      <Badge tone={b.verdict === 'consistent' ? 'success' : b.verdict === 'inconsistent' ? 'danger' : 'neutral'}>{b.verdict}</Badge>
                      <span style={{ ...mono, minWidth: 80, textAlign: 'right' }}>{b.before.toFixed(2)} → {b.after.toFixed(2)}</span>
                    </li>
                  ))}
                </ul>
              )}
              {e.observation && <Measurements m={e.observation.measurements} />}
              {e.source_refs.length > 0 && <div style={{ fontSize: 11.5, marginTop: 6, color: 'var(--t2)' }}>Justified by source {e.source_refs.join(', ')} (a reason to test, not proof)</div>}
              {e.envelope && (
                <details style={{ marginTop: 6 }}>
                  <summary style={{ fontSize: 11.5, color: 'var(--t3)', cursor: 'pointer' }}>
                    Experiment envelope <span style={mono}>{String(e.envelope.envelope_digest).slice(0, 19)}…</span>
                  </summary>
                  <pre style={{ ...mono, fontSize: 10.5, background: 'var(--sur)', padding: 8, borderRadius: 6, overflowX: 'auto', margin: '6px 0 0' }}>
                    {JSON.stringify(e.envelope, null, 2)}
                  </pre>
                  <p style={{ fontSize: 10.5, color: 'var(--t3)', margin: '4px 0 0' }}>
                    Digested at admission over every field except the digest; the verifier refuses to run if it no longer matches.
                  </p>
                </details>
              )}
              {e.error && <p role="alert" style={{ fontSize: 12, color: '#ef4444', margin: '6px 0 0' }}>{e.error}</p>}
            </div>
          ))}
        </div>
      )}
      {inv.denials.length > 0 && (
        <div role="alert" style={{ marginTop: 12, fontSize: 12, color: '#d97706' }}>
          Policy denials: {inv.denials.map((d) => `${d.code} (${d.reason})`).join('; ')}
        </div>
      )}
    </Panel>
  );
}

// ── External research ────────────────────────────────────────────────────────
const EV_ORDER: Record<string, number> = { ACCEPTED: 1, CONTEXT_ONLY: 2 };
const evRank = (e: { source_ref: string; status: string }, mapping: { source_ref?: string } | null) =>
  mapping?.source_ref === e.source_ref ? 0 : EV_ORDER[e.status] ?? 3;
export function ResearchPanel({ inv }: { inv: Investigation }) {
  const r = inv.research;
  const tone = r.state === 'AVAILABLE' ? 'success' : ['UNAVAILABLE', 'BLOCKED_PRIVACY'].includes(r.state) ? 'warning' : 'neutral';
  return (
    <Panel id="p-research" title={<>External evidence (Tavily) <Provenance kind="RETRIEVED" /></>} question="Did the investigation need current library knowledge, and what did it use?"
      right={<Badge tone={tone}>{pretty(r.state)}</Badge>}>
      {r.state === 'NOT_NEEDED' && <p style={{ fontSize: 12, color: 'var(--t3)', margin: 0 }}>No external evidence gap: local measurements were sufficient, so no search was made.</p>}
      {r.state === 'UNAVAILABLE' && r.calls.length === 0 && (
        <p style={{ fontSize: 12, color: 'var(--t2)', margin: 0 }}>EXTERNAL_EVIDENCE_UNAVAILABLE — Tavily is not configured on this server. The run continued with local evidence only; nothing was fabricated.</p>
      )}
      {r.state === 'OFF_ABLATION' && <p style={{ fontSize: 12, color: 'var(--t2)', margin: 0 }}>Research disabled for this run (Tavily-off ablation).</p>}
      {r.query?.query && <p style={{ fontSize: 12, margin: '0 0 8px' }}>Sanitized query: <span style={mono}>{r.query.query}</span></p>}
      {r.calls.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: '0 0 10px', display: 'grid', gap: 3 }}>
          {r.calls.map((c) => (
            <li key={c.correlation_id} style={{ fontSize: 11.5, ...mono }}>
              Tavily {c.operation} · {c.ok ? 'ok' : `failed (${c.error})`} · {c.latency_ms} ms · {c.result_count ?? 0} results · {c.request_id ?? `${c.correlation_id} (local id)`}
            </li>
          ))}
        </ul>
      )}
      {r.evidence.length > 0 && (
        <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 8 }}>
          {[...r.evidence].sort((a, b) => evRank(a, r.mapping) - evRank(b, r.mapping)).map((e) => (
            <li key={e.source_ref} style={{ border: `1px solid ${r.mapping?.source_ref === e.source_ref ? '#16a34a' : 'var(--bd2)'}`, borderRadius: 8, padding: 10, opacity: e.status === 'ACCEPTED' ? 1 : 0.7 }}>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <span style={{ ...mono, color: 'var(--t3)' }}>{e.source_ref}</span>
                <a href={e.url} target="_blank" rel="noopener noreferrer nofollow" style={{ fontSize: 12.5, color: 'var(--acc)', fontWeight: 600, minWidth: 0, overflowWrap: 'anywhere' }}>{e.title}</a>
                <Badge tone={e.status === 'ACCEPTED' ? 'success' : e.status === 'CONTEXT_ONLY' ? 'neutral' : 'danger'}>{e.status === 'CONTEXT_ONLY' ? 'Context only' : pretty(e.status)}</Badge>
                {r.mapping?.source_ref === e.source_ref && <Badge tone="success">used for the fix</Badge>}
              </div>
              <div style={{ fontSize: 11, color: 'var(--t3)', marginTop: 3 }}>
                {e.publisher} · {pretty(e.source_type)} · version {e.version_match} · fetched {new Date(e.fetched_at).toLocaleTimeString()}{e.extracted ? ' · extracted' : ''}
              </div>
              {e.caveats.length > 0 && <div style={{ fontSize: 11.5, color: '#d97706', marginTop: 3 }}>{e.caveats.join(' · ')}</div>}
              {e.excerpt && <blockquote style={{ margin: '6px 0 0', paddingLeft: 10, borderLeft: '2px solid var(--bd)', fontSize: 11.5, color: 'var(--t2)' }}>{e.excerpt.slice(0, 320)}{e.excerpt.length > 320 ? '…' : ''}</blockquote>}
            </li>
          ))}
        </ul>
      )}
      {r.mapping && (
        <p style={{ fontSize: 12, margin: '10px 0 0' }}>
          Sourced parameter mapping: <span style={mono}>{r.mapping.object}({r.mapping.from} → {r.mapping.to})</span> from {r.mapping.source_ref}, confirmed against the installed library signature.
        </p>
      )}
    </Panel>
  );
}

// ── Verification ─────────────────────────────────────────────────────────────
const chkTone = (s: string) => (s === 'PASS' ? 'success' : s === 'FAIL' ? 'danger' : 'warning');

export function VerificationPanel({ v }: { v: Verification | null }) {
  if (!v) {
    return (
      <Panel id="p-verify" title="Independent verification" question="Did the fix work on the frozen protocol, safely?">
        <p style={{ fontSize: 12, color: 'var(--t3)', margin: 0 }}>No candidate reached the verifier in this investigation.</p>
      </Panel>
    );
  }
  const f = v.metrics.final_test;
  const seg = v.metrics.critical_segment;
  return (
    <Panel id="p-verify" title="Independent verification" question={`${v.candidate} vs ${v.incumbent} · ${v.metric} · mode ${v.mode}`}
      right={<Badge tone={v.decision === 'CORRECTION_VERIFIED' ? 'success' : v.decision === 'REJECT' ? 'danger' : 'warning'}>{pretty(v.decision)}</Badge>}>
      <ul style={{ listStyle: 'none', padding: 0, margin: 0, display: 'grid', gap: 6 }}>
        {v.checks.map((c) => (
          <li key={c.check} style={{ display: 'flex', gap: 10, alignItems: 'baseline' }}>
            <Badge tone={chkTone(c.status)} style={{ minWidth: 92, justifyContent: 'center' }}>{c.status}</Badge>
            <span style={{ flex: 1, fontSize: 12.5 }}><strong>{pretty(c.check)}</strong> — {c.detail}</span>
          </li>
        ))}
      </ul>
      {f && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 10, marginTop: 14 }}>
          <Metric label={`Incumbent ${v.metric} (final test)`} value={f.incumbent[v.metric]} />
          <Metric label={`Candidate ${v.metric} (final test)`} value={f.candidate[v.metric]} />
          <Metric label="Δ with 95% CI" value={`${v.metrics.delta.delta >= 0 ? '+' : ''}${v.metrics.delta.delta} [${v.metrics.delta.ci_low}, ${v.metrics.delta.ci_high}]`} />
          {seg && <Metric label={`${seg.segment} ${seg.metric}`} value={`${seg.incumbent} → ${seg.candidate}`} />}
          <Metric label="Final-test rows (opened once)" value={f.rows} />
        </div>
      )}
    </Panel>
  );
}

function Metric({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div style={{ background: 'var(--sur)', borderRadius: 8, padding: '10px 12px' }}>
      <div style={{ fontSize: 10.5, color: 'var(--t3)' }}>{label}</div>
      <div style={{ ...mono, fontSize: 14, fontWeight: 600, marginTop: 3 }}>{value}</div>
    </div>
  );
}
