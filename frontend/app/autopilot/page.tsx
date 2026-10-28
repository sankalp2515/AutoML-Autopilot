'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { PROVIDER_LABEL, autopilot, decisionTone, modelLabel, pretty, type Providers, type Scenario, type Summary } from '@/lib/autopilot';
import { Badge, Button, Card, StateView } from '@/components/ui';
import { toast } from '@/lib/toast';

const KIND_LABEL: Record<string, string> = {
  degradation: 'Model degradation',
  challenger_review: 'Challenger review',
  pipeline_failure: 'Pipeline failure',
};

const KIND_ORDER = ['degradation', 'pipeline_failure', 'challenger_review'];

const FLOW = [
  { n: '1', t: 'Measure', d: 'Freeze the splits, rebuild the baseline, compute findings from the data.' },
  { n: '2', t: 'Hypothesise', d: 'Nemotron proposes 2–4 competing causes, citing only real findings.' },
  { n: '3', t: 'Test cheaply', d: 'Run the approved test with the most information per unit of cost.' },
  { n: '4', t: 'Fix narrowly', d: 'Build the smallest fix for the supported cause. Tavily sources library changes.' },
  { n: '5', t: 'Verify', d: 'An independent checker scores the fix on a holdout it can open once.' },
];

function HowItWorks() {
  return (
    <ol aria-label="How an investigation works" style={{ listStyle: 'none', padding: 0, margin: '18px 0 0', display: 'grid',
        gridTemplateColumns: 'repeat(auto-fit, minmax(min(170px, 100%), 1fr))', gap: 10 }}>
      {FLOW.map((f) => (
        <li key={f.n} style={{ border: '1px solid var(--bd)', borderRadius: 10, padding: '10px 12px' }}>
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <span className="ds-mono" aria-hidden="true" style={{ width: 20, height: 20, borderRadius: 6, background: 'var(--accw)', color: 'var(--acc)',
                fontSize: 11, fontWeight: 700, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>{f.n}</span>
            <strong style={{ fontSize: 13 }}>{f.t}</strong>
          </div>
          <p style={{ fontSize: 11.5, color: 'var(--t2)', margin: '6px 0 0', lineHeight: 1.45 }}>{f.d}</p>
        </li>
      ))}
    </ol>
  );
}

function ProviderBadges({ p }: { p: Providers | null }) {
  if (!p) return null;
  const active = p.planner_provider === 'nebius_token_factory' ? p.nebius_token_factory
    : p.planner_provider === 'nvidia_api' ? p.nvidia_api : null;
  return (
    <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }} aria-label="Provider status">
      <Badge tone={active ? 'success' : 'neutral'} dot>
        {active ? `Planner: ${modelLabel(active.model)} via ${PROVIDER_LABEL[p.planner_provider!]}` : 'Planner: deterministic rules (no model key)'}
      </Badge>
      <Badge tone={p.tavily.configured ? 'success' : 'warning'} dot>
        Tavily research: {p.tavily.configured ? 'live' : 'not configured'}
      </Badge>
      {!p.nebius_token_factory.configured && <Badge tone="neutral">Nebius Token Factory: on hold</Badge>}
      <Badge tone="neutral">Runner: isolated {p.runner}</Badge>
    </div>
  );
}

export default function AutopilotHome() {
  const router = useRouter();
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null);
  const [recent, setRecent] = useState<Summary[]>([]);
  const [providers, setProviders] = useState<Providers | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [picked, setPicked] = useState<string>('S02');
  const [stepMode, setStepMode] = useState(false);
  const [research, setResearch] = useState<'auto' | 'off'>('auto');
  const [planner, setPlanner] = useState<'auto' | 'deterministic'>('auto');
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    Promise.all([autopilot.scenarios(), autopilot.providers(), autopilot.list(8)])
      .then(([s, p, l]) => { setScenarios(s.scenarios); setProviders(p); setRecent(l.investigations); })
      .catch((e: Error) => setError(e.message));
  }, []);

  async function start() {
    setBusy(true);
    try {
      const key = `${picked}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
      const r = await autopilot.create({ scenario_id: picked, auto_run: !stepMode, research, planner }, key);
      router.push(`/autopilot/${r.run_id}`);
    } catch (e) {
      toast((e as Error).message, 'error');
      setBusy(false);
    }
  }

  return (
    <div style={{ padding: '24px 20px 48px', maxWidth: 1180, margin: '0 auto' }}>
      <section style={{ marginBottom: 22 }}>
        <div className="ds-mono" style={{ fontSize: 10.5, letterSpacing: '1.6px', color: 'var(--acc)', fontWeight: 600, textTransform: 'uppercase' }}>
          AutoML Autopilot
        </div>
        <h1 className="ds-display" style={{ fontSize: 'clamp(22px, 3vw, 30px)', fontWeight: 700, margin: '6px 0 8px', letterSpacing: '-0.5px' }}>
          When a model breaks, run the experiment that teaches you the most — then prove the fix.
        </h1>
        <p style={{ color: 'var(--t2)', fontSize: 14, maxWidth: 760, lineHeight: 1.55, margin: 0 }}>
          Measured findings → competing hypotheses → the cheapest test that separates them → bounded execution →
          an independent verifier on a frozen, single-use holdout. Rejecting a bad fix or saying &ldquo;not enough
          evidence&rdquo; counts as a correct answer.
        </p>
        <div style={{ marginTop: 14 }}><ProviderBadges p={providers} /></div>
        <HowItWorks />
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', marginTop: 16, padding: '12px 14px',
                      border: '1px solid var(--acc)', borderRadius: 10, background: 'var(--accw)' }}>
          <div style={{ flex: 1, minWidth: 240 }}>
            <strong style={{ fontSize: 13.5 }}>Have a model that broke?</strong>
            <span style={{ display: 'block', fontSize: 12.5, color: 'var(--t2)', marginTop: 2 }}>
              Upload a healthy window and a recent window as CSV, and Autopilot investigates your incident the same way.
            </span>
          </div>
          <Link href="/autopilot/new" className="ds-btn-acc" style={{ height: 34, padding: '0 14px', fontSize: 12.5, display: 'inline-flex', alignItems: 'center', borderRadius: 8, textDecoration: 'none', color: '#fff' }}>
            Investigate your own model →
          </Link>
        </div>
      </section>

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(320px, 100%), 1fr))', gap: 18, alignItems: 'start' }}>
        <Card padding={0} aria-labelledby="pick-incident">
          <div style={{ padding: '16px 18px', borderBottom: '1px solid var(--bd)', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h2 id="pick-incident" style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>1 · Pick an incident</h2>
            <span style={{ fontSize: 11, color: 'var(--t3)' }}>Seeded data · fault hidden from the planner</span>
          </div>
          <StateView loading={!scenarios && !error} error={error} isEmpty={scenarios?.length === 0} empty={{ title: 'No scenarios available' }}>
            <div role="radiogroup" aria-label="Incident scenarios" style={{ display: 'flex', flexDirection: 'column' }}>
              {KIND_ORDER.flatMap((kind) => {
                const group = (scenarios ?? []).filter((s) => s.kind === kind);
                if (!group.length) return [];
                return [
                  <div key={`h-${kind}`} className="ds-mono" style={{ fontSize: 10, letterSpacing: '1.2px', textTransform: 'uppercase', color: 'var(--t3)',
                      padding: '10px 18px 6px', background: 'var(--sur)', borderBottom: '1px solid var(--bd2)' }}>
                    {KIND_LABEL[kind]} · {group.length}
                  </div>,
                  ...group.map((s) => {
                const on = s.scenario_id === picked;
                return (
                  <button key={s.scenario_id} role="radio" aria-checked={on} onClick={() => setPicked(s.scenario_id)}
                    style={{ textAlign: 'left', padding: '12px 18px', border: 'none', borderBottom: '1px solid var(--bd2)', cursor: 'pointer',
                             background: on ? 'var(--accw)' : 'transparent', color: 'var(--t1)', display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                    <span className="ds-mono" style={{ fontSize: 11, fontWeight: 700, color: on ? 'var(--acc)' : 'var(--t3)', minWidth: 30, paddingTop: 2 }}>{s.scenario_id}</span>
                    <span style={{ flex: 1 }}>
                      <span style={{ display: 'block', fontSize: 13, fontWeight: 600 }}>{s.title}</span>
                      <span style={{ display: 'block', fontSize: 12, color: 'var(--t2)', marginTop: 2, lineHeight: 1.45 }}>{s.symptom}</span>
                      {(!s.labels_available || s.rows_current < 200) && (
                        <span style={{ display: 'flex', gap: 6, marginTop: 6, flexWrap: 'wrap' }}>
                          {!s.labels_available && <Badge tone="warning">no labels yet</Badge>}
                          {s.rows_current < 200 && <Badge tone="warning">{s.rows_current} rows</Badge>}
                        </span>
                      )}
                    </span>
                  </button>
                );
                  }),
                ];
              })}
            </div>
          </StateView>
        </Card>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
          <Card aria-labelledby="run-opts">
            <h2 id="run-opts" style={{ fontSize: 14, fontWeight: 700, margin: '0 0 12px' }}>2 · Run the investigation</h2>
            <fieldset style={{ border: 'none', padding: 0, margin: '0 0 12px' }}>
              <legend style={{ fontSize: 12, color: 'var(--t2)', marginBottom: 6 }}>Execution</legend>
              <label style={{ display: 'flex', gap: 8, fontSize: 13, alignItems: 'center', marginBottom: 6 }}>
                <input type="radio" name="mode" checked={!stepMode} onChange={() => setStepMode(false)} /> Autopilot — run to a verdict
              </label>
              <label style={{ display: 'flex', gap: 8, fontSize: 13, alignItems: 'center' }}>
                <input type="radio" name="mode" checked={stepMode} onChange={() => setStepMode(true)} /> Step mode — confirm each stage
              </label>
            </fieldset>
            <fieldset style={{ border: 'none', padding: 0, margin: '0 0 12px' }}>
              <legend style={{ fontSize: 12, color: 'var(--t2)', marginBottom: 6 }}>Hypothesis planner</legend>
              <label style={{ display: 'flex', gap: 8, fontSize: 13, alignItems: 'center', marginBottom: 6 }}>
                <input type="radio" name="planner" checked={planner === 'auto'} onChange={() => setPlanner('auto')} /> Nemotron (falls back to rules if unavailable)
              </label>
              <label style={{ display: 'flex', gap: 8, fontSize: 13, alignItems: 'center' }}>
                <input type="radio" name="planner" checked={planner === 'deterministic'} onChange={() => setPlanner('deterministic')} /> Deterministic rules only (ablation)
              </label>
            </fieldset>
            <fieldset style={{ border: 'none', padding: 0, margin: '0 0 16px' }}>
              <legend style={{ fontSize: 12, color: 'var(--t2)', marginBottom: 6 }}>External research (Tavily)</legend>
              <label style={{ display: 'flex', gap: 8, fontSize: 13, alignItems: 'center', marginBottom: 6 }}>
                <input type="radio" name="research" checked={research === 'auto'} onChange={() => setResearch('auto')} /> When an evidence gap is detected
              </label>
              <label style={{ display: 'flex', gap: 8, fontSize: 13, alignItems: 'center' }}>
                <input type="radio" name="research" checked={research === 'off'} onChange={() => setResearch('off')} /> Off (ablation)
              </label>
            </fieldset>
            <Button onClick={start} loading={busy} disabled={!scenarios} fullWidth>
              Start investigation · {picked}
            </Button>
          </Card>

          <Card aria-labelledby="recent">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <h2 id="recent" style={{ fontSize: 14, fontWeight: 700, margin: 0 }}>Recent investigations</h2>
              <Link href="/autopilot/benchmark" style={{ fontSize: 12, color: 'var(--acc)' }}>Benchmark →</Link>
            </div>
            {recent.length === 0 ? (
              <p style={{ fontSize: 12, color: 'var(--t3)', margin: 0 }}>None yet. Start one above.</p>
            ) : (
              <ul style={{ listStyle: 'none', margin: 0, padding: 0 }}>
                {recent.map((r) => (
                  <li key={r.run_id}>
                    <Link href={`/autopilot/${r.run_id}`} style={{ display: 'flex', gap: 10, alignItems: 'center', padding: '8px 0', borderBottom: '1px solid var(--bd2)', color: 'var(--t1)', textDecoration: 'none' }}>
                      <span className="ds-mono" style={{ fontSize: 11, color: 'var(--t3)', minWidth: 30 }}>{r.scenario_id}</span>
                      <span style={{ flex: 1, fontSize: 12.5, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.title ?? r.run_id.slice(0, 8)}</span>
                      <Badge tone={decisionTone(r.decision)}>{r.decision ? pretty(r.decision) : pretty(r.status)}</Badge>
                    </Link>
                  </li>
                ))}
              </ul>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}
