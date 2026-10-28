'use client';
import { useMemo, useState } from 'react';
import type { DecisionLog } from '@/lib/types';
import { Badge, Card, CardHeader } from '@/components/ui';

interface Candidate { name: string; score: number; }

// The model_selector / evaluator log their candidate→CV-score map as text like
// {'Ridge': 3.75, 'RandomForest': 5.50, 'GradientBoosting': 5.16}. We parse that
// out so we can show an interactive comparison instead of a flat table.
function parseCandidates(logs: DecisionLog[]): Candidate[] {
  const map = new Map<string, number>();
  for (const l of logs) {
    const text = `${l.decision} ${l.reasoning} ${l.result_summary}`;
    const blocks = text.match(/\{[^{}]*\}/g) ?? [];
    for (const b of blocks) {
      const re = /['"]([A-Za-z][\w .+-]*?)['"]\s*:\s*(-?\d+(?:\.\d+)?)/g;
      let m: RegExpExecArray | null;
      while ((m = re.exec(b))) {
        const name = m[1].trim();
        const val = parseFloat(m[2]);
        if (name.length <= 30 && isFinite(val)) map.set(name, val);
      }
    }
  }
  return Array.from(map.entries()).map(([name, score]) => ({ name, score }));
}

const LOWER_BETTER = /rmse|mae|mape|smape|log.?loss|error|drawdown|\bmse\b/i;

export default function ComparePanel({
  logs,
  winnerModel,
  primaryMetric,
}: {
  logs: DecisionLog[];
  winnerModel: string | null;
  primaryMetric: string | null;
}) {
  const candidates = useMemo(() => parseCandidates(logs), [logs]);
  const lowerBetter = LOWER_BETTER.test(primaryMetric ?? '');
  const modelDecisions = useMemo(
    () => logs.filter((l) => /select|model|evaluat|tune|baseline/i.test(l.agent_name)),
    [logs]
  );

  const ranked = useMemo(() => {
    const arr = [...candidates];
    arr.sort((a, b) => (lowerBetter ? a.score - b.score : b.score - a.score));
    return arr;
  }, [candidates, lowerBetter]);

  const [selected, setSelected] = useState<string | null>(null);

  if (candidates.length === 0) {
    // Fallback: interactive "model journey" from the relevant decisions.
    return (
      <Card delay={0}>
        <CardHeader title="Model Selection Journey" subtitle="How the winning model was chosen — click a step to expand." />
        {modelDecisions.length === 0 ? (
          <div style={{ color: 'var(--t4)', fontSize: 13 }}>No model-selection decisions were recorded for this run.</div>
        ) : (
          modelDecisions.map((d, i) => <JourneyStep key={i} d={d} />)
        )}
      </Card>
    );
  }

  const best = ranked[0];
  const worst = ranked[ranked.length - 1];
  const span = Math.abs((worst?.score ?? 0) - (best?.score ?? 0)) || 1;
  const isWinner = (name: string) =>
    winnerModel ? winnerModel.toLowerCase().includes(name.toLowerCase()) || name.toLowerCase().includes(winnerModel.toLowerCase()) : name === best.name;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <Card delay={0}>
        <CardHeader
          title="Model Comparison"
          subtitle={`${candidates.length} candidates evaluated under cross-validation. Click one to see why it placed where it did.`}
          actions={<span style={{ fontSize: 11, color: 'var(--t4)' }}>{primaryMetric ?? 'CV score'} · {lowerBetter ? 'lower is better' : 'higher is better'}</span>}
        />
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          {ranked.map((c) => {
            const win = isWinner(c.name);
            const goodness = 1 - Math.abs(c.score - best.score) / span; // 0..1 relative to best
            const w = 20 + goodness * 80;
            const active = selected === c.name;
            return (
              <div key={c.name}>
                <button
                  type="button"
                  onClick={() => setSelected(active ? null : c.name)}
                  aria-expanded={active}
                  style={{ display: 'flex', alignItems: 'center', gap: 12, cursor: 'pointer', width: '100%', background: 'none', border: 'none', padding: 0, textAlign: 'left' }}
                >
                  <span style={{ width: 150, fontSize: 13, fontWeight: win ? 700 : 500, color: win ? 'var(--acc)' : 'var(--t2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'flex', alignItems: 'center', gap: 6 }}>{win && '★'}{c.name}</span>
                  <span style={{ flex: 1, height: 26, background: 'var(--inp)', borderRadius: 6, overflow: 'hidden', display: 'block' }}>
                    <span className="ds-bar-fill" style={{ height: 26, width: `${w}%`, background: win ? 'var(--acc)' : 'var(--bone-faint, #8C8676)', opacity: win ? 1 : 0.5, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'flex-end', paddingRight: 8 }}>
                      <span className="ds-mono" style={{ fontSize: 11, fontWeight: 700, color: '#fff' }}>{c.score.toFixed(4)}</span>
                    </span>
                  </span>
                  {win && <Badge tone="accent">Winner</Badge>}
                </button>
                {active && (
                  <div className="ds-fade" style={{ marginTop: 8, marginLeft: 162, background: 'var(--inp)', border: '1px solid var(--bd2)', borderRadius: 8, padding: 12, fontSize: 12, color: 'var(--t2)', lineHeight: 1.6 }}>
                    <b style={{ color: 'var(--t1)' }}>{c.name}</b> scored <b style={{ color: 'var(--t1)' }}>{c.score.toFixed(4)}</b> on {primaryMetric ?? 'the CV metric'}.{' '}
                    {win ? 'It was selected as the winner and refit on all data.' : `That is ${lowerBetter ? 'worse (higher)' : 'worse (lower)'} than ${best.name}'s ${best.score.toFixed(4)}, so it was not chosen.`}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </Card>

      <Card delay={1}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)', marginBottom: 12 }}>Selection Reasoning</div>
        {modelDecisions.map((d, i) => <JourneyStep key={i} d={d} />)}
      </Card>
    </div>
  );
}

function JourneyStep({ d }: { d: DecisionLog }) {
  const [open, setOpen] = useState(false);
  return (
    <div style={{ borderBottom: '1px solid var(--bd2)', padding: '10px 0' }}>
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-expanded={open}
        style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', width: '100%', background: 'none', border: 'none', padding: 0, textAlign: 'left' }}
      >
        <Badge tone="accent" className="ds-mono" style={{ textTransform: 'uppercase', fontSize: 10, flexShrink: 0 }}>{d.agent_name.replace(/_/g, ' ')}</Badge>
        <span style={{ fontSize: 12.5, color: 'var(--t1)', fontWeight: 600, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: open ? 'normal' : 'nowrap' }}>{d.decision}</span>
        <span aria-hidden="true" style={{ color: 'var(--t4)', fontSize: 11, transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }}>▼</span>
      </button>
      {open && (
        <div className="ds-fade" style={{ marginTop: 8, paddingLeft: 4 }}>
          <p style={{ fontSize: 12, color: 'var(--t2)', lineHeight: 1.6 }}>{d.reasoning}</p>
          {d.result_summary && <p className="ds-mono" style={{ fontSize: 11, color: 'var(--t3)', marginTop: 6 }}>→ {d.result_summary}</p>}
        </div>
      )}
    </div>
  );
}
