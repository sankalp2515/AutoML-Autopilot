'use client';
import { useCallback, useEffect, useState } from 'react';
import { getDecisionAnalysis, type DecisionAnalysis } from '@/lib/api';
import { Badge, Button, Card, CardHeader, Stat, StatGrid, SkeletonText } from '@/components/ui';

/** Reliability diagram — predicted vs actual probability, with the ideal diagonal. */
function Reliability({ points }: { points: { predicted: number; actual: number }[] }) {
  const W = 320, H = 300, pad = 40;
  const sx = (v: number) => pad + v * (W - 2 * pad);
  const sy = (v: number) => H - pad - v * (H - 2 * pad);
  if (points.length < 2) return <div style={{ fontSize: 12, color: 'var(--t3)' }}>Not enough points for a reliability curve.</div>;
  const line = points.map((p) => `${sx(p.predicted).toFixed(1)},${sy(p.actual).toFixed(1)}`).join(' ');
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ maxWidth: 340, display: 'block' }}>
      <line x1={sx(0)} y1={sy(0)} x2={sx(1)} y2={sy(1)} stroke="var(--bd)" strokeWidth="1.5" strokeDasharray="5,4" />
      <polyline points={line} fill="none" stroke="#0B6E74" strokeWidth="2.5" strokeLinejoin="round" />
      {points.map((p, i) => <circle key={i} cx={sx(p.predicted)} cy={sy(p.actual)} r="3.5" fill="#0B6E74" />)}
      <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} stroke="var(--bd)" strokeWidth="1" />
      <line x1={pad} y1={pad} x2={pad} y2={H - pad} stroke="var(--bd)" strokeWidth="1" />
      <text x={W / 2} y={H - 8} fontSize="10" fill="var(--t3)" textAnchor="middle">Predicted probability</text>
      <text x={12} y={H / 2} fontSize="10" fill="var(--t3)" textAnchor="middle" transform={`rotate(-90,12,${H / 2})`}>Actual frequency</text>
      <text x={W - pad} y={sy(1) - 6} fontSize="9" fill="var(--t4)" textAnchor="end">perfect calibration</text>
    </svg>
  );
}

export default function DecisionPanel({ runId }: { runId: string }) {
  const [data, setData] = useState<DecisionAnalysis | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(() => {
    setLoading(true); setError(null);
    getDecisionAnalysis(runId).then(setData).catch(setError).finally(() => setLoading(false));
  }, [runId]);
  useEffect(() => { load(); }, [load]);

  if (loading) return <SkeletonText lines={8} />;
  if (error || !data) {
    const msg = error instanceof Error ? error.message
      : 'Decision analysis is available for completed binary-classification runs (it needs probability outputs and the honest holdout).';
    return (
      <Card>
        <div style={{ padding: 28, textAlign: 'center', display: 'flex', flexDirection: 'column', gap: 12, alignItems: 'center' }}>
          <div style={{ fontSize: 22 }}>🎯</div>
          <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>Decision analysis unavailable</div>
          <div style={{ fontSize: 12.5, color: 'var(--t3)', maxWidth: 460 }}>{msg}</div>
          <Button onClick={load}>Retry</Button>
        </div>
      </Card>
    );
  }

  const cal = data.calibration;
  const cell: React.CSSProperties = { padding: '8px 10px', fontSize: 12.5, fontVariantNumeric: 'tabular-nums' };
  const head: React.CSSProperties = { ...cell, fontWeight: 700, color: 'var(--t3)', fontSize: 10.5, textTransform: 'uppercase', letterSpacing: '0.04em' };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <StatGrid>
        <Stat label="Holdout rows" value={data.n_holdout.toLocaleString()} />
        <Stat label="Base positive rate" value={`${(data.positive_rate * 100).toFixed(1)}%`} />
        <Stat label="Brier score" value={cal.brier_score.toFixed(4)} hint="lower is better" />
        <Stat label="Calibration" value={cal.well_calibrated ? 'Calibrated' : 'Check'} />
      </StatGrid>

      {/* Campaign simulator — the predictions → actions → outcome story. */}
      <Card>
        <CardHeader title="Campaign simulator" subtitle="Rank customers by propensity, contact the top K% — computed on the honest holdout, not invented." />
        <div style={{ padding: '0 16px 12px', fontSize: 12.5, color: 'var(--t2)' }}>
          Contact everyone ({data.campaign.contact_all.customers_contacted.toLocaleString()}) →{' '}
          {data.campaign.contact_all.conversions.toLocaleString()} conversions at {data.campaign.contact_all.positive_rate_pct}% base rate.
        </div>
        <div style={{ overflowX: 'auto', padding: '0 8px 12px' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 560 }}>
            <thead><tr style={{ borderBottom: '1px solid var(--bd)' }}>
              {['Contact', 'Customers', 'Conversions captured', 'Conversions retained', 'Contacts avoided', 'Lift vs random'].map((h) => <th key={h} style={{ ...head, textAlign: 'left' }}>{h}</th>)}
            </tr></thead>
            <tbody>
              {data.campaign.top_k.map((r) => (
                <tr key={r.contact_pct} style={{ borderBottom: '1px solid var(--bd2)' }}>
                  <td style={{ ...cell, fontWeight: 700 }}>Top {r.contact_pct}%</td>
                  <td style={cell}>{r.customers_contacted.toLocaleString()}</td>
                  <td style={cell}>{r.conversions_captured.toLocaleString()}</td>
                  <td style={cell}><Badge tone={r.conversions_retained_pct >= 70 ? 'success' : 'warning'}>{r.conversions_retained_pct}%</Badge></td>
                  <td style={cell}>{r.contacts_avoided.toLocaleString()}</td>
                  <td style={cell}>{r.lift_vs_random != null ? `${r.lift_vs_random}×` : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1.2fr) minmax(0,1fr)', gap: 18 }} className="ds-grid-2col">
        {/* Threshold analysis — probabilities → decisions. */}
        <Card>
          <CardHeader title="Threshold analysis" subtitle="Precision/recall/cost at each operating point — the decision is a business choice, not 0.5 by default." />
          <div style={{ overflowX: 'auto', padding: '0 8px 12px' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', minWidth: 380 }}>
              <thead><tr style={{ borderBottom: '1px solid var(--bd)' }}>
                {['Thresh', 'Precision', 'Recall', 'F1', 'Contacts'].map((h) => <th key={h} style={{ ...head, textAlign: 'left' }}>{h}</th>)}
              </tr></thead>
              <tbody>
                {data.threshold_sweep.map((r) => (
                  <tr key={r.threshold} style={{ borderBottom: '1px solid var(--bd2)' }}>
                    <td style={{ ...cell, fontWeight: 700 }}>{r.threshold.toFixed(1)}</td>
                    <td style={cell}>{r.precision.toFixed(3)}</td>
                    <td style={cell}>{r.recall.toFixed(3)}</td>
                    <td style={cell}>{r.f1.toFixed(3)}</td>
                    <td style={cell}>{r.contacts_pct}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        {/* Calibration reliability diagram. */}
        <Card>
          <CardHeader title="Probability calibration" subtitle={`Brier ${cal.brier_score.toFixed(4)}${cal.expected_calibration_error != null ? ` · ECE ${cal.expected_calibration_error.toFixed(4)}` : ''}`} />
          <div style={{ padding: '4px 16px 16px' }}>
            <Reliability points={cal.reliability_curve} />
          </div>
        </Card>
      </div>
    </div>
  );
}
