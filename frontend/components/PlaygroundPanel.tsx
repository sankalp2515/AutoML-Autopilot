'use client';
import { useCallback, useEffect, useState } from 'react';
import {
  getFeatureSchema,
  predictRows,
  explainPrediction,
  listDeployments,
  deployModel,
} from '@/lib/api';
import type { FeatureSchema, Prediction } from '@/lib/types';
import { Badge, Button, Card } from '@/components/ui';

interface Contribution { feature: string; contribution: number; }

const PlayIcon = (
  <svg width="15" height="15" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><polygon points="5,3 19,12 5,21" /></svg>
);

export default function PlaygroundPanel({ runId }: { runId: string }) {
  const [schema, setSchema] = useState<FeatureSchema[]>([]);
  const [values, setValues] = useState<Record<string, string>>({});
  const [deployed, setDeployed] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);
  const [predicting, setPredicting] = useState(false);
  const [result, setResult] = useState<Prediction | null>(null);
  const [latency, setLatency] = useState<number | null>(null);
  const [shap, setShap] = useState<Contribution[]>([]);
  const [error, setError] = useState<string | null>(null);

  const checkDeploy = useCallback(() => {
    listDeployments()
      .then((d) => setDeployed(d.deployments.some((x) => x.run_id === runId && x.status === 'active')))
      .catch(() => setDeployed(false));
  }, [runId]);

  useEffect(() => {
    checkDeploy();
    getFeatureSchema(runId)
      .then((d) => {
        setSchema(d.features);
        setValues(Object.fromEntries(d.features.map((f) => [f.name, f.example])));
      })
      .catch(() => {});
  }, [runId, checkDeploy]);

  const deploy = async () => {
    setBusy(true);
    try { await deployModel(runId); checkDeploy(); } catch (e) { setError(e instanceof Error ? e.message : 'Deploy failed'); } finally { setBusy(false); }
  };

  const run = async () => {
    setPredicting(true); setError(null); setResult(null); setShap([]);
    try {
      const row: Record<string, unknown> = {};
      for (const f of schema) {
        const v = values[f.name];
        row[f.name] = f.type === 'number' && v !== '' ? Number(v) : v;
      }
      const res = await predictRows(runId, [row]);
      setResult(res.predictions[0]);
      setLatency(res.latency_ms);
      // SHAP is best-effort — the endpoint may be unavailable or return an
      // unexpected shape for some models, so guard hard against non-arrays.
      explainPrediction(runId, [row])
        .then((e) => {
          const ex = (e as { explanations?: unknown }).explanations;
          const first = Array.isArray(ex) ? ex[0] : undefined;
          setShap(Array.isArray(first) ? (first as Contribution[]) : []);
        })
        .catch(() => setShap([]));
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Prediction failed');
    } finally {
      setPredicting(false);
    }
  };

  if (deployed === false) {
    return (
      <Card padding={40} delay={0} style={{ textAlign: 'center' }}>
        <div aria-hidden="true" style={{ fontSize: 28, marginBottom: 10 }}>🎮</div>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)', marginBottom: 4 }}>Deploy the model to use the Playground</div>
        <p style={{ fontSize: 12.5, color: 'var(--t2)', marginBottom: 18, maxWidth: 360, marginInline: 'auto' }}>The Playground sends rows to the live inference endpoint and returns predictions, probabilities, and SHAP explanations in real time.</p>
        <Button loading={busy} onClick={deploy}>🚀 Deploy model</Button>
        {error && <p role="alert" style={{ marginTop: 12, fontSize: 12, color: '#ef4444' }}>⚠ {error}</p>}
      </Card>
    );
  }

  const conf = result?.confidence ?? null;
  const shapArr = Array.isArray(shap) ? shap : [];
  const maxAbs = Math.max(0.0001, ...shapArr.map((c) => Math.abs(c.contribution)));

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(320px, 1fr))', gap: 22, alignItems: 'start' }}>
      {/* Input form */}
      <Card delay={0}>
        <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--t1)' }}>Input Features</div>
        <p style={{ fontSize: 12, color: 'var(--t3)', marginBottom: 16 }}>Enter values to get a real-time prediction</p>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, maxHeight: 460, overflowY: 'auto', paddingRight: 4 }}>
          {schema.map((f) => (
            <div key={f.name}>
              <label htmlFor={`pg-${f.name}`} className="ds-mono" style={{ display: 'block', fontSize: 11, color: 'var(--t2)', marginBottom: 5, textTransform: 'uppercase', letterSpacing: '0.04em' }}>{f.name}</label>
              <input id={`pg-${f.name}`} className="ds-input" type={f.type === 'number' ? 'number' : 'text'} step="any" value={values[f.name] ?? ''} onChange={(e) => setValues((p) => ({ ...p, [f.name]: e.target.value }))} style={{ width: '100%', height: 38, padding: '0 12px' }} />
            </div>
          ))}
          {schema.length === 0 && <div style={{ fontSize: 12, color: 'var(--t4)' }}>Loading feature schema…</div>}
        </div>
        <Button fullWidth size="lg" icon={PlayIcon} loading={predicting} disabled={schema.length === 0} onClick={run} style={{ marginTop: 16 }}>
          Run Prediction
        </Button>
        {error && <p role="alert" style={{ marginTop: 12, fontSize: 12, color: '#ef4444' }}>⚠ {error}</p>}
      </Card>

      {/* Output */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        {!result ? (
          <Card padding={40} delay={false} style={{ textAlign: 'center', color: 'var(--t4)', fontSize: 13 }}>Run a prediction to see results.</Card>
        ) : (
          <>
            <Card delay={0} style={{ padding: 24, textAlign: 'center', background: 'var(--accw)', borderColor: 'rgba(11,110,116,0.22)' }}>
              <div className="ds-mono" style={{ fontSize: 10, color: 'var(--acc)', textTransform: 'uppercase', letterSpacing: '0.6px', marginBottom: 6 }}>Prediction</div>
              {Array.isArray(result.prediction) ? (
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>
                  {result.prediction.length === 0 ? <span style={{ color: 'var(--t3)', fontStyle: 'italic' }}>no labels</span> : result.prediction.map((l) => <Badge key={l} tone="accent" style={{ fontSize: 13 }}>{l}</Badge>)}
                </div>
              ) : (
                <div className="ds-display" style={{ fontSize: 30, fontWeight: 800, color: 'var(--t1)', letterSpacing: '-0.5px' }}>{result.prediction}</div>
              )}
              {conf != null && <div style={{ fontSize: 12, color: 'var(--acc)', marginTop: 6 }}>{(conf * 100).toFixed(1)}% confidence</div>}
            </Card>

            {conf != null && !Array.isArray(result.prediction) && (
              <Card delay={1} padding={18}>
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)', marginBottom: 12 }}>Class Probabilities</div>
                {[{ l: String(result.prediction), p: conf, c: '#16a34a' }, { l: 'other', p: 1 - conf, c: '#ef4444' }].map((b) => (
                  <div key={b.l} style={{ marginBottom: 10 }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}><span style={{ color: 'var(--t2)' }}>{b.l}</span><span style={{ fontWeight: 700, color: 'var(--t1)' }}>{(b.p * 100).toFixed(1)}%</span></div>
                    <div style={{ height: 8, background: 'var(--inp)', borderRadius: 100, overflow: 'hidden' }}><div className="ds-bar-fill" style={{ height: 8, width: `${b.p * 100}%`, background: b.c, borderRadius: 100 }} /></div>
                  </div>
                ))}
              </Card>
            )}

            {shapArr.length > 0 && (
              <Card delay={2} padding={18}>
                <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)' }}>Feature Contributions</div>
                <p className="ds-mono" style={{ fontSize: 11, color: 'var(--t3)', marginBottom: 12 }}>SHAP values</p>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
                  {shapArr.slice(0, 8).map((c) => {
                    const pos = c.contribution >= 0;
                    const w = (Math.abs(c.contribution) / maxAbs) * 100;
                    return (
                      <div key={c.feature} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span className="ds-mono" style={{ fontSize: 11, color: 'var(--t2)', width: 120, textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.feature}</span>
                        <div style={{ flex: 1, height: 16, position: 'relative' }}>
                          <div className="ds-bar-fill" style={{ position: 'absolute', left: 0, height: 16, width: `${w}%`, background: pos ? 'rgba(239,68,68,0.5)' : 'rgba(34,197,94,0.5)', borderRadius: 4 }} />
                        </div>
                        <span className="ds-mono" style={{ fontSize: 11, fontWeight: 600, color: pos ? '#ef4444' : '#16a34a', width: 48, textAlign: 'right' }}>{pos ? '+' : ''}{c.contribution.toFixed(2)}</span>
                      </div>
                    );
                  })}
                </div>
              </Card>
            )}

            {latency != null && (
              <Card delay={3} style={{ padding: '12px 18px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <span style={{ fontSize: 12, color: 'var(--t2)' }}>Inference latency</span>
                <span style={{ fontSize: 13, fontWeight: 700, color: '#16a34a' }}>{latency.toFixed(0)} ms</span>
              </Card>
            )}
          </>
        )}
      </div>
    </div>
  );
}
