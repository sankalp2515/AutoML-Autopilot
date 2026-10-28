'use client';
import { useCallback, useEffect, useState } from 'react';
import { getDriftReport, retrainChallenger } from '@/lib/api';
import type { DriftReport } from '@/lib/types';
import { toast } from '@/lib/toast';
import { Badge, Button, Card, CardHeader, StateView, Stat, StatGrid, Skeleton } from '@/components/ui';

/** PSI → severity band, shared by the bar color and the row badge. */
function band(psi: number | null): { c: string; tone: 'success' | 'warning' | 'danger'; label: string } {
  const v = psi ?? 0;
  if (v > 0.25) return { c: '#ef4444', tone: 'danger', label: 'Alert' };
  if (v > 0.1) return { c: '#d97706', tone: 'warning', label: 'Warning' };
  return { c: '#16a34a', tone: 'success', label: 'Stable' };
}

export default function DriftPanel({ runId }: { runId: string }) {
  const [report, setReport] = useState<DriftReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [retraining, setRetraining] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    setError(null);
    getDriftReport(runId)
      .then((r) => setReport(r))
      .catch((e) => setError(e))
      .finally(() => setLoading(false));
  }, [runId]);

  useEffect(() => { load(); }, [load]);

  const retrain = async () => {
    setRetraining(true);
    try {
      const r = await retrainChallenger(runId);
      toast(`Challenger retraining started (${r.challenger_run_id.slice(0, 8)}…)`, 'success');
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Retrain failed', 'error');
    } finally { setRetraining(false); }
  };

  const feats = report?.features ?? [];
  const insufficient = !report || report.status === 'insufficient_data';
  const avgPsi = feats.length ? feats.reduce((a, f) => a + (f.psi ?? 0), 0) / feats.length : 0;
  const drifted = report?.n_drifted ?? feats.filter((f) => (f.psi ?? 0) > 0.25).length;
  const maxW = Math.max(0.5, ...feats.map((f) => f.psi ?? 0));

  return (
    <StateView
      loading={loading}
      error={error}
      onRetry={load}
      loadingLabel="Loading drift report…"
      // A skeleton that mirrors the final layout (stat row + bars) so height is stable.
      loadingSkeleton={
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <StatGrid>
            {Array.from({ length: 4 }).map((_, i) => (
              <Card key={i} padding={18} delay={false}>
                <Skeleton width={90} height={11} />
                <Skeleton width={54} height={26} style={{ marginTop: 12 }} />
              </Card>
            ))}
          </StatGrid>
          <Card delay={false}>
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} height={22} style={{ marginBottom: 10 }} />
            ))}
          </Card>
        </div>
      }
      isEmpty={insufficient}
      empty={{
        icon: '📉',
        title: 'Not enough live traffic yet',
        description:
          report?.message ??
          'Drift compares live predictions against the training distribution. Make at least 10 predictions in the Playground to unlock it.',
      }}
    >
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        {drifted > 0 && (
          <Card
            padding={0}
            delay={0}
            role="alert"
            style={{ background: 'rgba(239,68,68,0.07)', borderColor: 'rgba(239,68,68,0.25)' }}
          >
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 14, padding: '14px 18px', flexWrap: 'wrap' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <span aria-hidden="true" style={{ color: '#ef4444' }}>⚠</span>
                <span style={{ fontSize: 13, color: 'var(--t2)' }}>
                  {drifted} feature{drifted !== 1 ? 's' : ''} show significant drift (PSI &gt; 0.25) — consider retraining
                </span>
              </div>
              <Button variant="danger" size="sm" loading={retraining} onClick={retrain}>
                Retrain Model
              </Button>
            </div>
          </Card>
        )}

        <StatGrid>
          <Stat label="Features Monitored" value={String(report?.n_features_checked ?? feats.length)} delay={0} />
          <Stat label="Features Drifted" value={String(drifted)} color={drifted > 0 ? '#ef4444' : 'var(--t1)'} delay={1} />
          <Stat label="Avg PSI Score" value={avgPsi.toFixed(2)} color={avgPsi > 0.1 ? '#d97706' : 'var(--t1)'} delay={2} />
          <Stat label="Predictions" value={String(report?.n_samples_current ?? report?.n_predictions ?? 0)} delay={3} />
        </StatGrid>

        <Card delay={2}>
          <CardHeader title="Feature Drift — PSI Scores" subtitle="Population Stability Index · warn > 0.1 · alert > 0.25" />
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {feats.map((f) => {
              const b = band(f.psi);
              const w = ((f.psi ?? 0) / maxW) * 100;
              return (
                <div key={f.feature} style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                  <span className="ds-mono" style={{ fontSize: 11, color: 'var(--t2)', width: 130, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={f.feature}>
                    {f.feature}
                  </span>
                  <div style={{ flex: 1, height: 22, background: 'var(--inp)', borderRadius: 6, overflow: 'hidden' }}>
                    <div className="ds-bar-fill" style={{ height: 22, width: `${Math.max(w, 6)}%`, background: b.c, borderRadius: 6, display: 'flex', alignItems: 'center', paddingLeft: 8 }}>
                      <span className="ds-mono" style={{ fontSize: 10, fontWeight: 700, color: '#fff' }}>PSI {(f.psi ?? 0).toFixed(2)}</span>
                    </div>
                  </div>
                  <Badge tone={b.tone} style={{ width: 64, justifyContent: 'center' }}>{b.label}</Badge>
                </div>
              );
            })}
          </div>
        </Card>
      </div>
    </StateView>
  );
}
