'use client';
import { useId, useState } from 'react';
import { askModel, fairnessAudit, retrainChallenger, batchPredictUrl } from '@/lib/api';
import { Badge, Button, Card } from '@/components/ui';

// Bundles the backlog tools (P12 retrain · P13 batch · P14 ask · P15 fairness)
// into one run-page tab. Each section calls the matching backend endpoint.
export default function ModelToolsPanel({ runId }: { runId: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
      <AskSection runId={runId} />
      <FairnessSection runId={runId} />
      <BatchSection runId={runId} />
      <RetrainSection runId={runId} />
    </div>
  );
}

function SectionShell({ eyebrow, title, delay, children }: { eyebrow: string; title: string; delay?: 0 | 1 | 2 | 3; children: React.ReactNode }) {
  return (
    <Card padding={24} delay={delay}>
      <div style={{ marginBottom: 14 }}>
        <p className="eyebrow">{eyebrow}</p>
        <h3 className="font-display" style={{ fontSize: 18, color: 'var(--t1)', marginTop: 4 }}>{title}</h3>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>{children}</div>
    </Card>
  );
}

/** Consistent inline error line with an assertive live region so it's announced. */
function ErrorLine({ msg }: { msg: string | null }) {
  if (!msg) return null;
  return <p role="alert" style={{ fontSize: 12, color: '#ef4444' }}>◆ {msg}</p>;
}

// ── P14: Ask your model ───────────────────────────────────────────────────────
function AskSection({ runId }: { runId: string }) {
  const inputId = useId();
  const [q, setQ] = useState('');
  const [ans, setAns] = useState<string | null>(null);
  const [cited, setCited] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const ask = async () => {
    if (q.trim().length < 3) return;
    setBusy(true); setErr(null); setAns(null);
    try {
      const r = await askModel(runId, q.trim());
      setAns(r.answer); setCited(r.cited_agents ?? []);
    } catch (e) { setErr(e instanceof Error ? e.message : 'Ask failed'); }
    setBusy(false);
  };

  return (
    <SectionShell eyebrow="P14 · Grounded Q&A" title="Ask Your Model" delay={0}>
      <p style={{ fontSize: 13, color: 'var(--t3)' }}>Answers come only from this run’s decisions, metrics, and SHAP.</p>
      <div style={{ display: 'flex', gap: 8 }}>
        <label htmlFor={inputId} className="sr-only">Ask a question about this model</label>
        <input
          id={inputId}
          value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && ask()}
          placeholder="e.g. Why was the Cabin column dropped?"
          className="ds-input"
          style={{ flex: 1, height: 40, padding: '0 14px' }}
        />
        <Button onClick={ask} loading={busy} disabled={q.trim().length < 3}>Ask</Button>
      </div>
      <ErrorLine msg={err} />
      {ans && (
        <div className="ds-card-2" style={{ padding: 16, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <p style={{ fontSize: 13.5, color: 'var(--t2)', lineHeight: 1.6 }}>{ans}</p>
          {cited.length > 0 && (
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
              {cited.map((c) => <Badge key={c} tone="accent" className="ds-mono" style={{ fontSize: 9, textTransform: 'uppercase' }}>{c}</Badge>)}
            </div>
          )}
        </div>
      )}
    </SectionShell>
  );
}

// ── P15: Fairness audit ───────────────────────────────────────────────────────
function FairnessSection({ runId }: { runId: string }) {
  const inputId = useId();
  const [cols, setCols] = useState('');
  const [data, setData] = useState<Record<string, any> | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const run = async () => {
    if (!cols.trim()) return;
    setBusy(true); setErr(null); setData(null);
    try {
      const r = await fairnessAudit(runId, cols.trim());
      setData((r as any).fairness ?? {});
    } catch (e) { setErr(e instanceof Error ? e.message : 'Fairness audit failed'); }
    setBusy(false);
  };

  return (
    <SectionShell eyebrow="P15 · Disparate Impact" title="Fairness Audit" delay={1}>
      <div style={{ display: 'flex', gap: 8 }}>
        <label htmlFor={inputId} className="sr-only">Sensitive columns to audit, comma-separated</label>
        <input
          id={inputId}
          value={cols} onChange={(e) => setCols(e.target.value)}
          placeholder="sensitive columns, comma-separated (e.g. Sex, Age)"
          className="ds-input"
          style={{ flex: 1, height: 40, padding: '0 14px' }}
        />
        <Button variant="ghost" onClick={run} loading={busy} disabled={!cols.trim()}>Audit</Button>
      </div>
      <ErrorLine msg={err} />
      {data && Object.keys(data).length === 0 && (
        <p style={{ fontSize: 12.5, color: 'var(--t3)' }}>No group breakdown was produced for those columns.</p>
      )}
      {data && Object.entries(data).map(([col, info]: [string, any]) => (
        <div key={col} className="ds-card-2" style={{ padding: 12, display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 10 }}>
            <span className="ds-mono" style={{ fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.5px', color: 'var(--t2)' }}>{col}</span>
            <Badge tone={info.passes_80pct_rule ? 'success' : 'danger'} className="ds-mono" style={{ fontSize: 10 }}>
              DI {info.disparate_impact_ratio ?? '—'} · {info.passes_80pct_rule ? 'passes 80% rule' : 'fails 80% rule'}
            </Badge>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))', gap: 8 }}>
            {Object.entries(info.by_group ?? {}).map(([g, m]: [string, any]) => (
              <div key={g} style={{ fontSize: 11, color: 'var(--t3)' }}>
                <span style={{ color: 'var(--t2)' }}>{g}</span> — sel {m.selection_rate ?? '—'}, acc {m.accuracy ?? '—'} (n={m.n})
              </div>
            ))}
          </div>
        </div>
      ))}
    </SectionShell>
  );
}

// ── P13: Batch scoring ────────────────────────────────────────────────────────
function BatchSection({ runId }: { runId: string }) {
  const inputId = useId();
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const score = async () => {
    if (!file) return;
    setBusy(true); setErr(null);
    try {
      const fd = new FormData(); fd.append('file', file);
      const res = await fetch(batchPredictUrl(runId), { method: 'POST', body: fd });
      if (!res.ok) throw new Error((await res.json()).detail || 'Batch failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a'); a.href = url; a.download = `scored_${runId.slice(0, 8)}.csv`; a.click();
      URL.revokeObjectURL(url);
    } catch (e) { setErr(e instanceof Error ? e.message : 'Batch failed'); }
    setBusy(false);
  };

  return (
    <SectionShell eyebrow="P13 · Batch Scoring" title="Score a CSV" delay={2}>
      <p style={{ fontSize: 13, color: 'var(--t3)' }}>Upload a CSV of rows; download it with prediction + confidence columns appended. (Deploy the model first.)</p>
      <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <label htmlFor={inputId} className="sr-only">CSV file to score</label>
        <input
          id={inputId}
          type="file" accept=".csv" onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="text-xs text-bone-faint file:mr-3 file:rounded-lg file:border file:border-bone/15 file:bg-obsidian-900 file:px-3 file:py-2 file:text-bone-dim file:text-[11px]"
        />
        <Button onClick={score} loading={busy} disabled={!file}>Score &amp; Download</Button>
      </div>
      <ErrorLine msg={err} />
    </SectionShell>
  );
}

// ── P12: Champion/challenger retrain ──────────────────────────────────────────
function RetrainSection({ runId }: { runId: string }) {
  const [busy, setBusy] = useState(false);
  const [res, setRes] = useState<{ challenger_run_id: string } | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const go = async () => {
    setBusy(true); setErr(null);
    try { setRes(await retrainChallenger(runId)); }
    catch (e) { setErr(e instanceof Error ? e.message : 'Retrain failed'); }
    setBusy(false);
  };

  return (
    <SectionShell eyebrow="P12 · Champion / Challenger" title="Retrain" delay={3}>
      <p style={{ fontSize: 13, color: 'var(--t3)' }}>Starts a challenger on the same dataset. Promote only if it beats this champion.</p>
      <div>
        <Button variant="ghost" onClick={go} loading={busy}>Train Challenger</Button>
      </div>
      <ErrorLine msg={err} />
      {res && (
        <a href={`/runs/${res.challenger_run_id}`} className="ds-mono" style={{ color: 'var(--acc)', fontSize: 13 }}>
          → challenger started: {res.challenger_run_id.slice(0, 8)} (open)
        </a>
      )}
    </SectionShell>
  );
}
