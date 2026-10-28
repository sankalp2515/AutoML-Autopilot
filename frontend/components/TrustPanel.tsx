'use client';
import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getTrustReport, runTrustAudit, rerunRun, type TrustReport, type TrustProbe } from '@/lib/api';
import { toast } from '@/lib/toast';
import { Badge, Button, Card, StateView, type BadgeTone } from '@/components/ui';

interface SuspectFeature { feature: string; abs_corr_with_target: number; }

const STATUS: Record<string, { tone: BadgeTone; label: string }> = {
  pass: { tone: 'success', label: 'Pass' },
  warn: { tone: 'warning', label: 'Warn' },
  fail: { tone: 'danger', label: 'Fail' },
  skip: { tone: 'neutral', label: 'N/A' },
  error: { tone: 'neutral', label: 'Error' },
};

const STATUS_COLOR: Record<string, string> = { pass: '#16a34a', warn: '#d97706', fail: '#ef4444' };

function gradeColor(g: string): string {
  if (g === 'A' || g === 'B') return '#16a34a';
  if (g === 'C') return '#d97706';
  if (g === 'D' || g === 'F') return '#ef4444';
  return 'var(--t3)';
}

function ProbeCard({ p }: { p: TrustProbe }) {
  const [open, setOpen] = useState(p.status === 'fail');
  const s = STATUS[p.status] ?? STATUS.skip;
  const accent = STATUS_COLOR[p.status] ?? 'var(--t2)';
  const ev = Object.entries(p.evidence ?? {});
  return (
    <Card padding={0} delay={false} style={{ overflow: 'hidden', borderColor: p.status === 'fail' ? 'rgba(239,68,68,0.3)' : 'var(--bd)' }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        style={{ width: '100%', display: 'flex', alignItems: 'center', gap: 12, padding: '14px 16px', background: 'transparent', border: 'none', cursor: 'pointer', textAlign: 'left' }}
      >
        <Badge tone={s.tone} style={{ width: 52, justifyContent: 'center', flexShrink: 0 }}>{s.label}</Badge>
        <span style={{ flex: 1, minWidth: 0 }}>
          <span style={{ display: 'block', fontSize: 13, fontWeight: 600, color: 'var(--t1)' }}>{p.title}</span>
          <span style={{ display: 'block', fontSize: 12, color: 'var(--t2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: open ? 'normal' : 'nowrap' }}>{p.headline}</span>
        </span>
        <span aria-hidden="true" style={{ color: 'var(--t4)', fontSize: 11, transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }}>▼</span>
      </button>
      {open && (
        <div className="ds-fade" style={{ padding: '0 16px 14px 80px' }}>
          {p.recommendation && (
            <p style={{ fontSize: 12, color: 'var(--t2)', lineHeight: 1.6, marginBottom: ev.length ? 10 : 0 }}>
              <span style={{ color: accent, fontWeight: 600 }}>→ </span>{p.recommendation}
            </p>
          )}
          {ev.length > 0 && (
            <div className="ds-mono" style={{ fontSize: 11, color: 'var(--t3)', background: 'var(--inp)', borderRadius: 8, padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: 4 }}>
              {ev.map(([k, v]) => (
                <div key={k} style={{ display: 'flex', justifyContent: 'space-between', gap: 12 }}>
                  <span>{k}</span>
                  <span style={{ color: 'var(--t1)', textAlign: 'right', wordBreak: 'break-word' }}>{fmtVal(v)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  );
}

function fmtVal(v: unknown): string {
  if (Array.isArray(v)) {
    if (v.length === 0) return '[]';
    if (typeof v[0] === 'object') return v.slice(0, 3).map((o) => JSON.stringify(o)).join('  ');
    return v.join(', ');
  }
  if (typeof v === 'object' && v !== null) return JSON.stringify(v);
  return String(v);
}

export default function TrustPanel({ runId }: { runId: string }) {
  const router = useRouter();
  const [report, setReport] = useState<TrustReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getTrustReport(runId)
      .then(setReport)
      .catch(() => setReport(null))
      .finally(() => setLoading(false));
  }, [runId]);

  const run = useCallback(async () => {
    setRunning(true);
    setError(null);
    try {
      setReport(await runTrustAudit(runId));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Trust audit failed');
    } finally {
      setRunning(false);
    }
  }, [runId]);

  const g = report?.grade ?? '';
  const gc = gradeColor(g);

  // Actionable: the leaked columns the Skeptic found, and the recommendations.
  const leakage = report?.probes.find((p) => p.id === 'leakage');
  const leaked = ((leakage?.evidence?.suspicious_features as SuspectFeature[] | undefined) ?? [])
    .filter((s) => s.abs_corr_with_target >= 0.9)
    .map((s) => s.feature);
  const recommendations = (report?.probes ?? [])
    .filter((p) => (p.status === 'fail' || p.status === 'warn') && p.recommendation)
    .map((p) => ({ title: p.title, rec: p.recommendation }));

  const doRerun = async () => {
    setRerunning(true);
    try {
      const r = await rerunRun(runId, leaked);
      toast(`Re-running without ${leaked.length} feature(s)…`, 'success');
      router.push(`/runs/${r.id}`);
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Re-run failed', 'error');
      setRerunning(false);
    }
  };

  return (
    <StateView
      loading={loading}
      loadingLabel="Loading trust audit…"
      isEmpty={!report}
      empty={
        <Card padding={40} delay={0} style={{ textAlign: 'center' }}>
          <div aria-hidden="true" style={{ fontSize: 28, marginBottom: 10 }}>🔬</div>
          <div style={{ fontSize: 15, fontWeight: 700, color: 'var(--t1)', marginBottom: 4 }}>Run the Trust Audit</div>
          <p style={{ fontSize: 13, color: 'var(--t2)', maxWidth: 440, margin: '0 auto 18px', lineHeight: 1.6 }}>
            The Skeptic tries to <i>break</i> this result — probing for target leakage, optimistic evaluation,
            spurious features, weak slices, and fragility to data shift — then grades how much you can trust it.
          </p>
          <Button size="lg" loading={running} onClick={run}>Run Trust Audit</Button>
          {error && <p role="alert" style={{ marginTop: 14, fontSize: 12, color: '#ef4444' }}>⚠ {error}</p>}
        </Card>
      }
    >
      {report && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <Card delay={0} style={{ display: 'flex', alignItems: 'center', gap: 24, flexWrap: 'wrap' }}>
            <div aria-label={`Trust grade ${g}`} style={{ width: 96, height: 96, borderRadius: 18, background: `${gc}1f`, border: `2px solid ${gc}55`, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <span className="ds-display" style={{ fontSize: 44, fontWeight: 800, color: gc, lineHeight: 1 }}>{g}</span>
              {report.trust_score != null && <span style={{ fontSize: 11, color: gc, marginTop: 2 }}>{report.trust_score}/100</span>}
            </div>
            <div style={{ flex: 1, minWidth: 220 }}>
              <div className="ds-mono" style={{ fontSize: 10, color: 'var(--t2)', textTransform: 'uppercase', letterSpacing: '0.6px', marginBottom: 6 }}>Trust verdict</div>
              <p style={{ fontSize: 14, color: 'var(--t1)', lineHeight: 1.55, marginBottom: 12 }}>{report.summary ?? report.error}</p>
              <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
                <Badge tone="success">{report.n_pass} pass</Badge>
                <Badge tone="warning">{report.n_warn} warn</Badge>
                <Badge tone="danger">{report.n_fail} fail</Badge>
                <Badge tone="neutral">{report.n_skip} n/a</Badge>
              </div>
            </div>
            <Button variant="ghost" size="sm" loading={running} onClick={run} style={{ flexShrink: 0 }}>Re-run</Button>
          </Card>

          {(leaked.length > 0 || recommendations.length > 0) && (
            <Card delay={0}>
              <div className="ds-mono" style={{ fontSize: 10, color: 'var(--t2)', textTransform: 'uppercase', letterSpacing: '0.6px', marginBottom: 12 }}>What to do</div>
              {leaked.length > 0 && (
                <div style={{ background: 'rgba(239,68,68,0.06)', border: '1px solid rgba(239,68,68,0.25)', borderRadius: 10, padding: 14, marginBottom: 14 }}>
                  <div style={{ fontSize: 13, color: 'var(--t1)', marginBottom: 4 }}>
                    {leaked.length} feature{leaked.length !== 1 ? 's' : ''} look like leakage and won&apos;t exist at prediction time.
                  </div>
                  <div className="ds-mono" style={{ fontSize: 11, color: 'var(--t3)', marginBottom: 12 }}>{leaked.join(', ')}</div>
                  <Button loading={rerunning} onClick={doRerun}>
                    Re-run without {leaked.length} leaked feature{leaked.length !== 1 ? 's' : ''} →
                  </Button>
                </div>
              )}
              <ul style={{ margin: 0, paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 7 }}>
                {recommendations.map((r, i) => (
                  <li key={i} style={{ fontSize: 12.5, color: 'var(--t2)', lineHeight: 1.55 }}>
                    <span style={{ color: 'var(--t1)', fontWeight: 600 }}>{r.title}:</span> {r.rec}
                  </li>
                ))}
              </ul>
            </Card>
          )}

          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {report.probes.map((p) => <ProbeCard key={p.id} p={p} />)}
          </div>
        </div>
      )}
    </StateView>
  );
}
