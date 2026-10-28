'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams } from 'next/navigation';
import Link from 'next/link';
import { PROVIDER_LABEL, autopilot, decisionTone, isTerminal, modelLabel, pretty, type Investigation } from '@/lib/autopilot';
import { Badge, Button, Card, Stat, StatGrid, StateView } from '@/components/ui';
import { toast } from '@/lib/toast';
import {
  ExperimentsPanel, FindingsPanel, HypothesesPanel, ModelCallsPanel, ResearchPanel, SelectionPanel, StageTimeline, StakeholderSummary,
  VerdictSummary, VerificationPanel,
} from '@/components/autopilot/panels';

export default function InvestigationPage() {
  const { id } = useParams<{ id: string }>();
  const [inv, setInv] = useState<Investigation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const load = useCallback(async () => {
    try {
      const s = await autopilot.get(id);
      setInv(s);
      setError(null);
      // Auto runs poll to the end; step mode polls while a requested stage is queued or running.
      if (!isTerminal(s.status) && (s.config.auto_run !== false || s.step_pending)) {
        timer.current = setTimeout(load, 900);
      }
    } catch (e) {
      setError((e as Error).message);
    }
  }, [id]);

  useEffect(() => {
    load();
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [load]);

  async function act(name: string, fn: () => Promise<unknown>) {
    setBusy(name);
    try {
      await fn();
      if (timer.current) clearTimeout(timer.current);
      await load();
    } catch (e) {
      toast((e as Error).message, 'error');
    } finally {
      setBusy(null);
    }
  }

  const live = inv && !isTerminal(inv.status);
  const d = inv?.decision;
  const pcall = inv?.planner.calls[inv.planner.calls.length - 1];
  const plannerLabel = !inv ? '' : inv.planner.used === 'nemotron' && pcall?.ok
    ? `${modelLabel(pcall.model)} · ${PROVIDER_LABEL[pcall.provider] ?? pcall.provider}`
    : inv.planner.used ?? (inv.scenario?.kind === 'challenger_review' || inv.decision ? 'not needed'
      : inv.planner.requested === 'auto' ? 'pending' : inv.planner.requested);
  const toneColor = (t: string) => (t === 'success' ? '#16a34a' : t === 'danger' ? '#ef4444' : t === 'warning' ? '#d97706' : 'var(--acc)');

  return (
    <div style={{ padding: '20px 20px 48px', maxWidth: 1180, margin: '0 auto' }}>
      <StateView loading={!inv && !error} error={error} onRetry={load}>
        {inv && (
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 16 }}>
            <header>
              <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
                <Link href="/autopilot" style={{ fontSize: 12, color: 'var(--acc)' }}>← Autopilot</Link>
                <span className="ds-mono" style={{ fontSize: 11, color: 'var(--t3)' }}>{inv.scenario?.scenario_id} · {inv.run_id.slice(0, 8)}</span>
                <Badge tone={live ? 'accent' : decisionTone(d?.decision)} dot>{live ? pretty(inv.status) : pretty(d?.decision ?? inv.status)}</Badge>
                <Badge tone="neutral">planner: {plannerLabel}</Badge>
                {inv.research.state === 'AVAILABLE' && <Badge tone="success">Tavily evidence</Badge>}
              </div>
              <h1 className="ds-display" style={{ fontSize: 'clamp(19px, 2.4vw, 24px)', fontWeight: 700, margin: '8px 0 4px' }}>
                {inv.scenario?.title ?? 'Investigation'}
              </h1>
              <p style={{ color: 'var(--t2)', fontSize: 13, margin: 0 }}>Reported symptom: {inv.scenario?.symptom}</p>
              {inv.scenario?.notes && (
                <p style={{ color: 'var(--t3)', fontSize: 12, margin: '4px 0 0', lineHeight: 1.45 }}>
                  Team notes (read by Nemotron, not treated as proof): {inv.scenario.notes}
                </p>
              )}
            </header>

            <div aria-live="polite"><StageTimeline inv={inv} /></div>

            {d && (
              <Card style={{ borderColor: toneColor(decisionTone(d.decision)), borderLeftWidth: 4 }}>
                <div role="status" style={{ display: 'flex', gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                  <div style={{ flex: 1, minWidth: 260 }}>
                    <div className="ds-mono" style={{ fontSize: 11, color: 'var(--t3)', letterSpacing: '1px' }}>DECISION</div>
                    <div style={{ fontSize: 22, fontWeight: 750, margin: '2px 0 6px', color: toneColor(decisionTone(d.decision)) }}>{pretty(d.decision)}</div>
                    <p style={{ fontSize: 13, margin: 0, lineHeight: 1.5 }}>{d.summary}</p>
                    <p style={{ fontSize: 12.5, color: 'var(--t2)', margin: '6px 0 0' }}><strong>Next:</strong> {d.next_action}</p>
                    <p style={{ fontSize: 11.5, color: 'var(--t3)', margin: '6px 0 0' }}>
                      Incumbent changed: <strong>{d.incumbent_changed ? 'yes' : 'no'}</strong>
                      {d.reason_codes.length > 0 && <> · reasons: <span className="ds-mono">{d.reason_codes.join(', ')}</span></>}
                    </p>
                    {inv.review && <p style={{ fontSize: 12, margin: '6px 0 0' }}>Reviewer <strong>{inv.review.reviewer}</strong> {inv.review.action}d — {inv.review.effect}.</p>}
                  </div>
                  <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                    {inv.status === 'REVIEW_READY' && (
                      <>
                        <Button size="sm" loading={busy === 'approve'} onClick={() => act('approve', () => autopilot.review(inv.run_id, 'approve', 'ml-lead'))}>Approve (record)</Button>
                        <Button size="sm" variant="ghost" loading={busy === 'reject'} onClick={() => act('reject', () => autopilot.review(inv.run_id, 'reject', 'ml-lead'))}>Reject</Button>
                      </>
                    )}
                    <Button size="sm" variant="ghost" loading={busy === 'export'} onClick={() => act('export', () => autopilot.exportReceipt(inv.run_id))}>
                      Export receipt (JSON)
                    </Button>
                  </div>
                </div>
                <VerdictSummary inv={inv} />
                <StakeholderSummary inv={inv} />
              </Card>
            )}

            {live && inv.queue_position != null && (
              <p role="status" style={{ fontSize: 12.5, color: 'var(--t2)', margin: 0 }}>
                Queued: position {inv.queue_position}. The shared demo runs two investigations at a time; yours starts automatically.
              </p>
            )}
            {live && (
              <div style={{ display: 'flex', gap: 8 }}>
                {inv.config.auto_run === false && (
                  <Button size="sm" loading={busy === 'next' || !!inv.step_pending} onClick={() => act('next', () => autopilot.next(inv.run_id))}>
                    {inv.step_pending ? 'Running stage…' : 'Run next stage'}
                  </Button>
                )}
                <Button size="sm" variant="ghost" loading={busy === 'cancel'} onClick={() => act('cancel', () => autopilot.cancel(inv.run_id))}>Cancel</Button>
              </div>
            )}

            <StatGrid min={150}>
              <Stat label="Experiments used" value={`${inv.budget.experiments_used} / ${inv.budget.max_experiments}`} />
              <Stat label="Cost units" value={`${inv.budget.cost_units_used} / ${inv.budget.max_cost_units}`} />
              <Stat label="Measured CPU seconds" value={inv.budget.cpu_seconds.toFixed(1)} />
              <Stat label="Final-test opens" value={inv.final_test_accesses.length} hint="single-use holdout" />
              <Stat label="Protocol" value={<span className="ds-mono" style={{ fontSize: 12 }}>{inv.protocol?.protocol_hash.slice(0, 10) ?? '—'}</span>} />
            </StatGrid>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(420px, 100%), 1fr))', gap: 16, alignItems: 'start' }}>
              <FindingsPanel findings={inv.findings} />
              <HypothesesPanel inv={inv} />
            </div>
            <SelectionPanel inv={inv} />
            <ExperimentsPanel inv={inv} />
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(420px, 100%), 1fr))', gap: 16, alignItems: 'start' }}>
              <ResearchPanel inv={inv} />
              <VerificationPanel v={inv.verification} />
            </div>
            <ModelCallsPanel inv={inv} />
            {inv.planner.note && !inv.hypotheses.length && <p role="note" style={{ fontSize: 12, color: '#d97706', margin: 0 }}>{inv.planner.note}</p>}
            {inv.error && <p role="alert" style={{ fontSize: 12, color: '#ef4444', margin: 0 }}>{inv.error}</p>}
          </div>
        )}
      </StateView>
    </div>
  );
}
