'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { autopilot } from '@/lib/autopilot';
import { Card, StateView } from '@/components/ui';

const ARM_LABEL: Record<string, string> = {
  retrain_first: 'Retrain first (classic AutoML response)',
  fixed_order: 'Fixed-order checklist',
  random: 'Random eligible test',
  autopilot: 'Autopilot (information gain ÷ cost)',
};

const th = { textAlign: 'left' as const, fontSize: 10.5, color: 'var(--t3)', padding: '8px 10px', borderBottom: '1px solid var(--bd)', textTransform: 'uppercase' as const };
const td = { fontSize: 12.5, padding: '8px 10px', borderBottom: '1px solid var(--bd2)', fontFamily: 'var(--font-mono), monospace' };

function LiveAblation({ a }: { a: any }) {
  if (!a) return null;
  if (!a.available) {
    return <Card><p style={{ margin: 0, fontSize: 13 }}>Live ablation not run yet. Run <code>{a.how_to_run}</code>.</p></Card>;
  }
  const arms: [string, string][] = [['rules', 'Rules planner (no model)'], ['nemotron', `Nemotron planner · ${a.planner_model}`]];
  const t = a.tavily_ablation_T01;
  return (
    <Card padding={0}>
      <div style={{ padding: '14px 18px 10px' }}>
        <h2 style={{ fontSize: 14, margin: 0 }}>Live ablation: what Nemotron and Tavily add</h2>
        <p style={{ fontSize: 12, color: 'var(--t3)', margin: '3px 0 0' }}>
          Real provider calls ({a.provider}), same seeds {a.seeds?.join(', ')}, budget, templates and verifier. Reported as measured, including where the model loses.
        </p>
      </div>
      <div style={{ overflowX: 'auto' }}>
        <table aria-label="Planner ablation" style={{ width: '100%', borderCollapse: 'collapse', minWidth: 760 }}>
          <thead><tr>{['Planner', 'Correct', 'False promotions', 'Mean tests', 'Cost units', 'Median model s', 'Est. model cost', 'Rule anchor needed', 'Fell back to rules'].map((h) => <th key={h} scope="col" style={th}>{h}</th>)}</tr></thead>
          <tbody>
            {arms.map(([k, label]) => {
              const s = a.planner_ablation[k];
              return (
                <tr key={k}>
                  <td style={{ ...td, fontFamily: 'inherit', fontWeight: 600 }}>{label}</td>
                  <td style={td}>{s.correct}/{s.runs}</td>
                  <td style={{ ...td, color: s.false_promotions ? '#ef4444' : undefined }}>{s.false_promotions}</td>
                  <td style={td}>{s.mean_experiments}</td>
                  <td style={td}>{s.total_cost_units}</td>
                  <td style={td}>{k === 'rules' ? '—' : s.median_model_seconds}</td>
                  <td style={td}>{k === 'rules' ? '—' : `$${s.est_cost_usd}`}</td>
                  <td style={td}>{k === 'rules' ? '—' : `${s.runs_where_anchor_added_a_cause} runs`}</td>
                  <td style={td}>{k === 'rules' ? '—' : `${s.runs_that_fell_back_to_rules} runs`}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {t && (
        <p style={{ fontSize: 12.5, margin: 0, padding: '12px 18px 16px' }}>
          <strong>Tavily on vs off (T01, library upgrade):</strong> with Tavily {t.tavily_on.correct}/{t.tavily_on.runs} correct,
          decisions {t.tavily_on.decisions.join(', ')}; without Tavily {t.tavily_off.correct}/{t.tavily_off.runs},
          decisions {t.tavily_off.decisions.join(', ')}. Without a cited official source the policy refuses to guess a parameter rename.
        </p>
      )}
    </Card>
  );
}

export default function BenchmarkPage() {
  const [data, setData] = useState<any>(null);
  const [abl, setAbl] = useState<any>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    autopilot.benchmark().then(setData).catch((e: Error) => setError(e.message));
    autopilot.ablation().then(setAbl).catch(() => setAbl(null));
  }, []);

  return (
    <div style={{ padding: '24px 20px 48px', maxWidth: 1100, margin: '0 auto' }}>
      <Link href="/autopilot" style={{ fontSize: 12, color: 'var(--acc)' }}>← Autopilot</Link>
      <h1 className="ds-display" style={{ fontSize: 24, fontWeight: 700, margin: '8px 0 4px' }}>Equal-budget benchmark</h1>
      <p style={{ color: 'var(--t2)', fontSize: 13, margin: '0 0 16px', maxWidth: 760 }}>
        Same incidents, seeds, splits, templates, budget and verifier for every arm. &ldquo;Correct&rdquo; means the terminal
        decision is in the hidden oracle&rsquo;s accepted set. Held-out seeds were not used during development.
      </p>
      <StateView loading={!data && !error} error={error}>
        {data && !data.available && (
          <Card><p style={{ margin: 0, fontSize: 13 }}>No benchmark report yet. Run <code>{data.how_to_run}</code>.</p></Card>
        )}
        {data?.available && (
          <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr)', gap: 16 }}>
            <Card padding={0}>
              <div style={{ overflowX: 'auto' }}>
                <table aria-label="Benchmark results by arm" style={{ width: '100%', borderCollapse: 'collapse', minWidth: 720 }}>
                  <thead><tr>{['Arm', 'Correct', 'False promotions', 'Median tests', 'Mean tests', 'Total cost units', 'Retrains', 'Median CPU s'].map((h) => <th key={h} scope="col" style={th}>{h}</th>)}</tr></thead>
                  <tbody>
                    {Object.entries(data.arms).map(([arm, s]: [string, any]) => (
                      <tr key={arm} style={{ background: arm === 'autopilot' ? 'var(--accw)' : undefined }}>
                        <td style={{ ...td, fontFamily: 'inherit', fontWeight: arm === 'autopilot' ? 700 : 500 }}>{ARM_LABEL[arm] ?? arm}</td>
                        <td style={td}>{s.correct}/{s.runs}</td>
                        <td style={{ ...td, color: s.false_promotions ? '#ef4444' : undefined }}>{s.false_promotions}</td>
                        <td style={td}>{s.median_experiments}</td>
                        <td style={td}>{s.mean_experiments}</td>
                        <td style={td}>{s.total_cost_units}</td>
                        <td style={td}>{s.total_retrains}</td>
                        <td style={td}>{s.median_cpu_seconds}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
            <LiveAblation a={abl} />
            <Card>
              <h2 style={{ fontSize: 14, margin: '0 0 8px' }}>Verifier must-fail controls</h2>
              <p style={{ fontSize: 12.5, margin: 0 }}>
                Leakage decoy, protocol mismatch and segment regression ({data.verifier_must_fail.scenarios.join(', ')}):
                {' '}{data.verifier_must_fail.correct}/{data.verifier_must_fail.runs} correctly rejected,
                {' '}{data.verifier_must_fail.false_promotions} false promotions.
              </p>
            </Card>
            <Card>
              <h2 style={{ fontSize: 14, margin: '0 0 8px' }}>Not run, and why</h2>
              <ul style={{ margin: 0, paddingLeft: 18, fontSize: 12.5 }}>
                {Object.entries(data.not_run).map(([k, v]) => <li key={k}><strong>{k}</strong>: {String(v)}</li>)}
              </ul>
              <ul style={{ margin: '10px 0 0', paddingLeft: 18, fontSize: 12, color: 'var(--t2)' }}>
                {data.notes.map((n: string) => <li key={n}>{n}</li>)}
              </ul>
              <p style={{ fontSize: 11.5, color: 'var(--t3)', margin: '10px 0 0' }}>Seeds {data.seeds.join(', ')} · generated {data.generated_at}</p>
            </Card>
          </div>
        )}
      </StateView>
    </div>
  );
}
