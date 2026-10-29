'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { autopilot, decisionTone, incidentMarkdown, isTerminal, pretty, type Investigation } from '@/lib/autopilot';
import { Badge, Button, Card, StateView } from '@/components/ui';
import { toast } from '@/lib/toast';
import { ExperimentsPanel, FindingsPanel, HypothesesPanel, ModelCallsPanel, ResearchPanel, SelectionPanel, StageTimeline, StakeholderSummary, VerdictSummary, VerificationPanel } from './panels';

const STAGES: Record<string, string> = {
  CREATED: 'Waiting for an investigation slot', VALIDATING: 'Checking your data and freezing evaluation splits',
  BASELINING: 'Measuring model quality and data changes', INVESTIGATING: 'Checking the measurements for an incident',
  RESEARCHING: 'Reading relevant documentation with Tavily', PROPOSING: 'Planning competing causes from findings and team notes',
  POLICY_CHECK: 'Checking the test against the budget and safety policy', EXECUTING: 'Running the selected diagnostic test',
  OBSERVING: 'Comparing test results with each cause’s prediction', TESTING_CORRECTION: 'Building the smallest supported fix', VERIFYING: 'Independently checking the fix on unseen data',
};
const REASONS: Record<string, string> = {
  E_USER_CODE_CHANGE: 'A pipeline code change is recommended; it has not been applied or verified.',
  E_LEAKAGE: 'The proposed model uses information that may only exist after the outcome.',
  E_PROTOCOL_MISMATCH: 'The proposed model was evaluated on a different data split.',
  E_GUARDRAIL_FAIL: 'The proposed fix made a protected customer group worse.',
  E_EVIDENCE_MISSING: 'There is too little labelled data for a reliable decision.',
  E_UNCERTAIN_GAIN: 'The apparent improvement could be sampling noise.', E_PRIMARY_WORSE: 'The fix did not meet the required quality improvement.',
  E_INTERRUPTED: 'The worker stopped before completion. Start a new investigation.', E_IDLE_EXPIRED: 'The investigation closed after inactivity.',
  E_VERIFIER_UNAVAILABLE: 'The independent verifier did not finish; no verified result is available.',
};
const TOUR = [
  ['p-findings', 'Measured findings', 'Begin with observed quality and data changes. Check each method and sample count.'],
  ['p-hyp', 'Competing causes', 'Compare explanations. The weights are heuristics, not calibrated probabilities.'],
  ['p-sel', 'The cheapest useful test', 'Inspect why this test separates the causes before paying for a retrain.'],
  ['p-verify', 'Independent verification', 'Check unseen-data quality, uncertainty and the customer-group guardrail.'],
  ['verdict', 'The decision', 'Use the verdict and next action. Approval records a decision only.'],
];
function Progress({ inv, reconnecting }: { inv: Investigation; reconnecting: boolean }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const t = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(t); }, []);
  return <div className="ap-progress no-print">
    <strong role="status" aria-live="polite">{reconnecting ? 'Reconnecting…' : STAGES[inv.status] ?? pretty(inv.status)}</strong>
    <span>{Math.max(0, Math.floor((now - Date.parse(inv.created_at)) / 1000))} s elapsed · sample runs typically take 20–60 s</span>
    {inv.queue_position != null && <span>Queue position {inv.queue_position}</span>}
    {inv.status === 'PROPOSING' && <span>{inv.findings.length} findings and your notes are available to the planner.</span>}
  </div>;
}
function ResultChart({ inv }: { inv: Investigation }) {
  const v = inv.verification, f = v?.metrics.final_test, d = v?.metrics.delta;
  if (!v || !f) return null;
  const x = (n: number) => 100 + Math.max(-1, Math.min(1, n)) * 85;
  return <figure style={{ margin: '18px 0' }} aria-label={`Final test ${v.metric} before and after`}>
    {v.prediction_sources && <p style={{ fontSize: 12, color: 'var(--t2)' }}>Prediction sources — current model: {pretty(v.prediction_sources.incumbent)}; proposed fix: {pretty(v.prediction_sources.candidate)}.</p>}
    {(['incumbent', 'candidate'] as const).map((k) => <div key={k} style={{ margin: '8px 0', fontSize: 12 }}>
      {k === 'incumbent' ? 'Current model' : 'Proposed fix'}: <strong>{f[k][v.metric]}</strong>
      <div style={{ height: 9, background: 'var(--sur)', borderRadius: 4 }}><div style={{ height: '100%', width: `${f[k][v.metric] * 100}%`, background: 'var(--acc)', borderRadius: 4 }} /></div>
    </div>)}
    {d && <><svg viewBox="0 0 200 30" role="img" aria-label={`Improvement ${d.delta}; 95% confidence interval ${d.ci_low} to ${d.ci_high}`} style={{ width: '100%', maxWidth: 500 }}>
      <line x1="15" y1="15" x2="185" y2="15" stroke="var(--bd)" /><line x1="100" x2="100" y1="0" y2="30" stroke="var(--t3)" strokeDasharray="2 2" />
      <path d={`M${x(d.ci_low)} 7v16 M${x(d.ci_low)} 15H${x(d.ci_high)} M${x(d.ci_high)} 7v16`} fill="none" stroke="var(--acc)" strokeWidth="2" /><circle cx={x(d.delta)} cy="15" r="3" fill="var(--acc)" />
    </svg><figcaption style={{ fontSize: 12, color: 'var(--t2)' }}>Quality difference: {d.delta}; 95% CI [{d.ci_low}, {d.ci_high}]. Dashed line: no improvement.</figcaption></>}
  </figure>;
}
export default function InvestigationView({ id, shareToken, showcase = false }: { id?: string; shareToken?: string; showcase?: boolean }) {
  const readOnly = !!shareToken || showcase;
  const [inv, setInv] = useState<Investigation | null>(null), [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false), [section, setSection] = useState('Summary');
  const [tour, setTour] = useState<number | null>(null), [dialog, setDialog] = useState<'share' | 'approve' | 'reject' | null>(null);
  const [reviewer, setReviewer] = useState(''), [reason, setReason] = useState(''), [revision, setRevision] = useState(0);
  const modal = useRef<HTMLDialogElement>(null);
  const refresh = useCallback(() => setRevision((v) => v + 1), []);
  useEffect(() => {
    let stopped = false, failures = 0, timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      if (stopped || document.hidden) return;
      try {
        const s = showcase ? await autopilot.showcase() : shareToken ? await autopilot.shared(shareToken) : await autopilot.get(id!);
        if (stopped) return;
        failures = 0; setInv(s); setError(null);
        if (!readOnly && !isTerminal(s.status) && (s.config.auto_run !== false || s.step_pending)) timer = setTimeout(load, 900);
      } catch (e) {
        if (stopped) return;
        setError((e as Error).message); timer = setTimeout(load, Math.min(15000, 1000 * 2 ** Math.min(failures++, 4)));
      }
    };
    const visible = () => { clearTimeout(timer); if (!document.hidden) void load(); };
    document.addEventListener('visibilitychange', visible); void load();
    return () => { stopped = true; clearTimeout(timer); document.removeEventListener('visibilitychange', visible); };
  }, [id, shareToken, showcase, readOnly, revision]);
  useEffect(() => { if (showcase) { try { if (!localStorage.getItem('ap-tour-dismissed')) setTour(0); } catch { setTour(0); } } }, [showcase]);
  useEffect(() => {
    if (tour === null || !inv) return;
    setSection(tour === 4 ? 'Summary' : tour === 2 ? 'Audit trail' : 'Evidence');
    const t = setTimeout(() => document.getElementById(TOUR[tour][0])?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 100);
    return () => clearTimeout(t);
  }, [tour, inv]);
  useEffect(() => { if (dialog) modal.current?.showModal(); else modal.current?.close(); }, [dialog]);
  const closeTour = () => { setTour(null); try { localStorage.setItem('ap-tour-dismissed', '1'); } catch {} };
  async function act(fn: () => Promise<unknown>) { setBusy(true); try { await fn(); refresh(); } catch (e) { toast((e as Error).message, 'error'); } finally { setBusy(false); } }
  async function submitDialog(e: React.FormEvent) {
    e.preventDefault(); if (!inv || !dialog) return;
    await act(async () => {
      if (dialog === 'share') { const s = await autopilot.share(inv.run_id); await navigator.clipboard.writeText(`${window.location.origin}${s.path}`); toast('Read-only link copied. Expires in 24 hours.', 'success'); }
      else await autopilot.review(inv.run_id, dialog, reviewer.trim(), reason.trim());
      setDialog(null);
    });
  }
  const live = inv && !isTerminal(inv.status);
  return <div className="ap-investigation" style={{ padding: '20px 20px 48px', maxWidth: 1180, margin: '0 auto', minWidth: 0 }}>
    <StateView loading={!inv && !error} error={!inv ? error : null} onRetry={refresh}>{inv && <>
      <header><Link href="/autopilot" className="no-print" style={{ color: 'var(--acc)' }}>← Autopilot</Link>
        <h1 className="ds-display" style={{ fontSize: 24, margin: '12px 0' }}>{inv.scenario?.title ?? 'Investigation'}</h1>
        <div className="ap-actions"><Badge tone={decisionTone(inv.decision?.decision)}>{pretty(inv.decision?.decision ?? inv.status)}</Badge>
          {inv.scenario?.source === 'upload' && <Badge tone="neutral">Your data</Badge>}
          {readOnly && <Badge tone="neutral">Read-only {showcase ? 'recorded example · rules planner' : 'shared report'}</Badge>}
          <Badge tone="neutral">{inv.scenario?.prediction_source === 'user_scores' ? 'Your model scores' : 'Reconstructed model'}</Badge></div>
        <p style={{ color: 'var(--t2)' }}>{inv.scenario?.symptom}</p>
        <details className="no-print"><summary>Investigation details</summary><p>{inv.run_id} <Button size="sm" variant="ghost" onClick={() => act(() => navigator.clipboard.writeText(inv.run_id))}>Copy ID</Button></p><p>Planner: {inv.planner.used ?? 'pending'} · {inv.planner.note}</p><p>Team notes (context, not proof): {inv.scenario?.notes || 'None'}</p></details>
      </header>
      {live && <Progress inv={inv} reconnecting={!!error} />}{!live && error && <p role="status">Reconnecting… {error}</p>}
      {live && !readOnly && <div className="no-print ap-actions">{inv.config.auto_run === false && <Button loading={busy || !!inv.step_pending} onClick={() => act(() => autopilot.next(inv.run_id))}>Run next stage</Button>}<Button variant="ghost" onClick={() => act(() => autopilot.cancel(inv.run_id))}>Cancel</Button></div>}
      <div className="no-print ap-actions">
        {!live && !readOnly && <Button size="sm" onClick={() => setDialog('share')}>Share read-only link</Button>}
        <Button size="sm" variant="ghost" onClick={() => act(async () => { await navigator.clipboard.writeText(incidentMarkdown(inv)); toast('Summary copied', 'success'); })}>Copy summary</Button>
        <Button size="sm" variant="ghost" onClick={() => window.print()}>Print report</Button>
        {!readOnly && <Button size="sm" variant="ghost" onClick={() => act(() => autopilot.exportReceipt(inv.run_id))}>Export receipt</Button>}
        {showcase && <Button size="sm" variant="ghost" onClick={() => setTour(0)}>Take the guided tour</Button>}
      </div>
      <nav aria-label="Investigation sections" className="no-print ap-actions">{['Summary', 'Evidence', 'Audit trail'].map((s) => <Button key={s} size="sm" variant={section === s ? 'accent' : 'ghost'} aria-pressed={section === s} onClick={() => setSection(s)}>{s}</Button>)}</nav>
      <section className={`ap-section ${section === 'Summary' ? 'ap-selected' : ''}`} aria-label="Summary"><Card id="verdict">
        <h2 style={{ fontSize: 20 }}>{inv.decision ? pretty(inv.decision.decision) : 'Investigation in progress'}</h2><p>{inv.decision?.summary ?? 'A verdict will appear here. Follow the current stage above.'}</p>
        {inv.decision && <p><strong>Next action:</strong> {inv.decision.next_action}</p>}
        {inv.decision?.reason_codes.map((c) => <p key={c}><span title={c}>{REASONS[c] ?? pretty(c.replace(/^E_/, ''))}</span></p>)}
        <p style={{ fontSize: 12, color: 'var(--t2)' }}>Live model changed: {inv.decision?.incumbent_changed ? 'yes' : 'no'}. Approval records a decision only.</p>
        {inv.status === 'REVIEW_READY' && !readOnly && <div className="no-print ap-actions"><Button size="sm" onClick={() => setDialog('approve')}>Record approval</Button><Button size="sm" variant="ghost" onClick={() => setDialog('reject')}>Record rejection</Button></div>}
        {inv.review && <p>Recorded by {inv.review.reviewer}: {inv.review.action}. Reason: {inv.review.note}</p>}
        <VerdictSummary inv={inv} /><ResultChart inv={inv} /><StakeholderSummary inv={inv} />
      </Card></section>
      <section className={`ap-section ${section === 'Evidence' ? 'ap-selected' : ''}`} aria-label="Evidence"><FindingsPanel findings={inv.findings} /><HypothesesPanel inv={inv} /><ExperimentsPanel inv={inv} /><ResearchPanel inv={inv} /><VerificationPanel v={inv.verification} /></section>
      <section className={`ap-section ${section === 'Audit trail' ? 'ap-selected' : ''}`} aria-label="Audit trail"><StageTimeline inv={inv} /><SelectionPanel inv={inv} /><ModelCallsPanel inv={inv} /><Card><h2>Evaluation protocol and resource budget</h2><p>Experiments: {inv.budget.experiments_used}/{inv.budget.max_experiments} · Experiment cost units: {inv.budget.cost_units_used}/{inv.budget.max_cost_units} · Measured execution seconds: {inv.budget.cpu_seconds.toFixed(1)}</p><p>Unseen test set accesses: {inv.final_test_accesses.length} · Evaluation split fingerprint: <span style={{ overflowWrap: 'anywhere' }}>{inv.protocol?.protocol_hash}</span></p><p>Cost units are relative experiment weights, not currency. Hypothesis weights are uncalibrated.</p></Card></section>
      {inv.error && <p role="alert" style={{ color: 'var(--danger)' }}>{inv.error}</p>}
      {tour !== null && <aside className="ap-tour no-print" aria-label="Guided example"><strong>{tour + 1}/5 · {TOUR[tour][1]}</strong><p>{TOUR[tour][2]}</p><Button size="sm" onClick={() => tour === 4 ? closeTour() : setTour(tour + 1)}>{tour === 4 ? 'Finish tour' : 'Next'}</Button>{' '}<Button size="sm" variant="ghost" onClick={closeTour}>Dismiss</Button></aside>}
    </>}</StateView>
    <dialog ref={modal} onCancel={() => setDialog(null)} className="ap-dialog"><form onSubmit={submitDialog}>
      <h2>{dialog === 'share' ? 'Share this investigation' : 'Record a review decision'}</h2>
      {dialog === 'share' ? <p>Anyone with the link can read this report, including team notes, findings and research. It expires in 24 hours. Raw CSV files are not included.</p> : <><p>This records your decision. It does not change the production model.</p><label className="ap-field">Reviewer name<input autoFocus required maxLength={120} value={reviewer} onChange={(e) => setReviewer(e.target.value)} /></label><label className="ap-field">Reason<textarea required maxLength={1000} value={reason} onChange={(e) => setReason(e.target.value)} /></label></>}
      <div className="ap-actions"><Button type="submit" loading={busy} disabled={dialog !== 'share' && (!reviewer.trim() || !reason.trim())}>{dialog === 'share' ? 'Create and copy link' : 'Save decision'}</Button><Button type="button" variant="ghost" onClick={() => setDialog(null)}>Cancel</Button></div>
    </form></dialog>
  </div>;
}
