'use client';
import { useCallback, useEffect, useState } from 'react';
import { getInvestigation, runInvestigation, type InvestigationReport, type InvestigationHypothesis } from '@/lib/api';
import { Badge, Button, Card, StateView } from '@/components/ui';

function verdictTone(h: InvestigationHypothesis): { tone: 'success' | 'warning' | 'danger' | 'neutral'; label: string } {
  if (h.verdict === 'confirmed') {
    return { tone: h.severity === 'high' ? 'danger' : 'warning', label: 'Confirmed' };
  }
  if (h.verdict === 'refuted') return { tone: 'success', label: 'Refuted' };
  return { tone: 'neutral', label: 'Inconclusive' };
}

function HypothesisCard({ h }: { h: InvestigationHypothesis }) {
  const [open, setOpen] = useState(h.verdict === 'confirmed');
  const v = verdictTone(h);
  const ev = Object.entries(h.evidence ?? {});
  return (
    <Card
      padding={0}
      delay={false}
      style={{ overflow: 'hidden', borderColor: h.verdict === 'confirmed' && h.severity === 'high' ? 'rgba(239,68,68,0.3)' : 'var(--bd)' }}
    >
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        style={{ width: '100%', display: 'flex', alignItems: 'flex-start', gap: 12, padding: '14px 16px', background: 'transparent', border: 'none', cursor: 'pointer', textAlign: 'left' }}
      >
        <Badge tone={v.tone} style={{ width: 88, justifyContent: 'center', flexShrink: 0, marginTop: 1 }}>{v.label}</Badge>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: 13, fontWeight: 600, color: 'var(--t1)', whiteSpace: open ? 'normal' : 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{h.statement}</span>
          <span className="ds-mono" style={{ display: 'block', fontSize: 11, color: 'var(--t3)', marginTop: 2 }}>{h.primitive}({Object.values(h.params).join(', ')})</span>
        </span>
        <span aria-hidden="true" style={{ color: 'var(--t4)', fontSize: 11, transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s', marginTop: 4 }}>▼</span>
      </button>
      {open && (
        <div className="ds-fade" style={{ padding: '0 16px 14px 84px' }}>
          {h.rationale && <p style={{ fontSize: 12, color: 'var(--t2)', lineHeight: 1.6, marginBottom: 10 }}>{h.rationale}</p>}
          <div className="ds-mono" style={{ fontSize: 11, color: 'var(--t3)', background: 'var(--inp)', borderRadius: 8, padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 4 }}>
            {h.observed != null && <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>observed</span><span style={{ color: 'var(--t1)' }}>{h.observed} (expected {h.expected.direction} {h.expected.threshold})</span></div>}
            {h.reason && <div style={{ display: 'flex', justifyContent: 'space-between' }}><span>reason</span><span style={{ color: 'var(--t1)' }}>{h.reason}</span></div>}
            {ev.map(([k, val]) => (
              <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                <span>{k}</span><span style={{ color: 'var(--t1)', textAlign: 'right', wordBreak: 'break-word' }}>{String(val)}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

export default function InvestigatePanel({ runId }: { runId: string }) {
  const [report, setReport] = useState<InvestigationReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getInvestigation(runId).then(setReport).catch(() => setReport(null)).finally(() => setLoading(false));
  }, [runId]);

  const run = useCallback(async () => {
    setRunning(true);
    setError(null);
    try {
      setReport(await runInvestigation(runId));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Investigation failed');
    } finally {
      setRunning(false);
    }
  }, [runId]);

  return (
    <StateView
      loading={loading}
      loadingLabel="Loading investigation…"
      isEmpty={!report}
      empty={
        <Card padding={40} delay={0} style={{ textAlign: 'center' }}>
          <div aria-hidden="true" style={{ fontSize: 28, marginBottom: 10 }}>🧠</div>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--t1)', marginBottom: 4 }}>Run an Investigation</div>
          <p style={{ fontSize: 13, color: 'var(--t2)', maxWidth: 460, margin: '0 auto 18px', lineHeight: 1.6 }}>
            The strategist reasons about <i>this</i> dataset + the Skeptic&apos;s findings, proposes falsifiable
            hypotheses (about specific columns, slices and interactions), tests each one, and drills into what holds.
          </p>
          <Button size="lg" loading={running} onClick={run}>Run Investigation</Button>
          {error && <p role="alert" style={{ marginTop: 14, fontSize: 12, color: '#ef4444' }}>⚠ {error}</p>}
        </Card>
      }
    >
      {report && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <Card delay={0} style={{ display: 'flex', alignItems: 'flex-start', gap: 16 }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div className="ds-mono" style={{ fontSize: 10, color: 'var(--t2)', textTransform: 'uppercase', letterSpacing: '0.6px', marginBottom: 6 }}>Investigation verdict</div>
              <p style={{ fontSize: 14, color: 'var(--t1)', lineHeight: 1.55, marginBottom: 12 }}>{report.summary ?? report.error}</p>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <Badge tone="warning">{report.n_confirmed} confirmed</Badge>
                <Badge tone="success">{report.n_refuted} refuted</Badge>
                <Badge tone="neutral">{report.n_inconclusive} inconclusive</Badge>
              </div>
            </div>
            <Button variant="ghost" size="sm" loading={running} onClick={run} style={{ flexShrink: 0 }}>Re-run</Button>
          </Card>

          {report.hypotheses.length === 0 ? (
            <Card padding={28} style={{ textAlign: 'center', color: 'var(--t4)', fontSize: 13 }}>
              No testable hypotheses were produced (the strategist may be offline or returned nothing usable).
            </Card>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {report.hypotheses.map((h) => <HypothesisCard key={h.id} h={h} />)}
            </div>
          )}
        </div>
      )}
    </StateView>
  );
}
