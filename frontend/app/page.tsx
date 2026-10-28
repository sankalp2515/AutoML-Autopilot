'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { listRuns } from '@/lib/api';
import type { Run } from '@/lib/types';

const FEATURES = [
  { c: '#0B6E74', t: 'Intelligent Data Auditing', d: 'Detects missing values, outliers, leakage, and class imbalance before training begins.', p: 'M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z' },
  { c: '#0B6E74', t: '10-Stage Adaptive Pipeline', d: 'End-to-end agentic workflow from EDA to export, adapting to your data automatically.', p: 'M22 12 18 12 15 21 9 3 6 12 2 12' },
  { c: '#2BB8C0', t: 'Model & Algorithm Search', d: 'Evaluates XGBoost, RandomForest, GradientBoosting and more — tuned via Optuna.', p: 'M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20M9 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3' },
  { c: '#22c55e', t: 'One-Click Deploy + Monitor', d: 'Deploy as a REST endpoint with live prediction logging and drift detection.', p: 'M22 12h-4l-3 9L9 3l-3 9H2' },
];

function chip(status: string) {
  const m: Record<string, { bg: string; c: string; l: string }> = {
    completed: { bg: 'rgba(34,197,94,0.12)', c: '#16a34a', l: 'Complete' },
    running: { bg: 'rgba(11,110,116,0.12)', c: 'var(--acc)', l: 'Running' },
    queued: { bg: 'rgba(11,110,116,0.10)', c: 'var(--acc)', l: 'Queued' },
    failed: { bg: 'rgba(239,68,68,0.12)', c: '#ef4444', l: 'Failed' },
  };
  return m[status] ?? { bg: 'var(--inp)', c: 'var(--t3)', l: status };
}

export default function HomePage() {
  const router = useRouter();
  const [runs, setRuns] = useState<Run[]>([]);
  useEffect(() => {
    listRuns(0, 4).then((d) => setRuns(d.runs)).catch(() => {});
  }, []);

  const openNewRun = () => window.dispatchEvent(new Event('open-new-run'));

  return (
    <div style={{ maxWidth: 860, margin: '0 auto', padding: '48px 32px' }}>
      {/* Hero */}
      <div style={{ marginBottom: 42 }}>
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, border: '1px solid var(--bd)', borderRadius: 5, padding: '5px 11px', marginBottom: 24, background: 'var(--sb)' }}>
          <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--acc)', animation: 'ds-pulse 2s infinite' }} />
          <span className="ds-mono" style={{ fontSize: 10.5, color: 'var(--t2)', fontWeight: 600, letterSpacing: '0.5px' }}>ML CONTROL PLANE · v2.4</span>
        </div>
        <h1 className="ds-display" style={{ fontSize: 46, fontWeight: 700, letterSpacing: '-1.6px', lineHeight: 1.04, color: 'var(--t1)', marginBottom: 16, maxWidth: 640 }}>
          The control plane for your <span style={{ color: 'var(--acc)' }}>entire ML lifecycle</span>.
        </h1>
        <p style={{ fontSize: 15, color: 'var(--t2)', lineHeight: 1.65, maxWidth: 560 }}>
          Hand off a dataset and a goal in plain English. The Orchestrator audits, frames, engineers, trains, tunes, and ships — and hands back a production model with the receipts to trust it.
        </p>
      </div>

      {/* CTA card */}
      <div className="ds-card" style={{ padding: 28, marginBottom: 28, borderRadius: 16 }}>
        <div className="ds-display" style={{ fontSize: 18, fontWeight: 600, color: 'var(--t1)', marginBottom: 6 }}>Start a new run</div>
        <p style={{ fontSize: 13, color: 'var(--t2)', marginBottom: 18, lineHeight: 1.6 }}>Upload a CSV, describe your goal, and let the agents build a deployable model end to end.</p>
        <button className="ds-btn-acc" onClick={openNewRun} style={{ height: 46, padding: '0 22px', fontSize: 14, boxShadow: '0 4px 20px rgba(11,110,116,0.3)' }}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5"><polygon points="5,3 19,12 5,21" /></svg>
          Run AutoML Pipeline
        </button>
      </div>

      {/* Feature grid */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 14, marginBottom: 36 }}>
        {FEATURES.map((f) => (
          <div key={f.t} className="ds-card" style={{ padding: 18 }}>
            <div style={{ width: 34, height: 34, borderRadius: 8, background: 'var(--accw)', display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 11 }}>
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke={f.c} strokeWidth="1.5"><path d={f.p} /></svg>
            </div>
            <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t1)', marginBottom: 5 }}>{f.t}</div>
            <div style={{ fontSize: 12, color: 'var(--t2)', lineHeight: 1.55 }}>{f.d}</div>
          </div>
        ))}
      </div>

      {/* Recent runs */}
      <div>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 14 }}>
          <span className="ds-display" style={{ fontSize: 14, fontWeight: 700, color: 'var(--t1)' }}>Recent Runs</span>
          <span onClick={() => router.push('/runs')} style={{ fontSize: 12, color: 'var(--acc)', cursor: 'pointer', fontWeight: 500 }}>View all →</span>
        </div>
        <div className="ds-card" style={{ overflow: 'hidden' }}>
          {runs.length === 0 && <div style={{ padding: 28, textAlign: 'center', color: 'var(--t4)', fontSize: 13 }}>No runs yet — start one above.</div>}
          {runs.map((r) => {
            const s = chip(r.status);
            return (
              <div key={r.id} className="ds-row" onClick={() => router.push(`/runs/${r.id}`)} style={{ display: 'flex', alignItems: 'center', padding: '13px 18px', gap: 14, borderBottom: '1px solid var(--bd2)', cursor: 'pointer' }}>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.user_goal}</div>
                  <div style={{ fontSize: 11, color: 'var(--t4)' }}>{r.dataset_filename}{r.task_type ? ` · ${r.task_type.replace(/_/g, ' ')}` : ''}</div>
                </div>
                <span className="ds-chip" style={{ background: s.bg, color: s.c }}>{s.l}</span>
                <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--t1)', minWidth: 44, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{r.final_score != null ? r.final_score.toFixed(3) : '—'}</span>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
