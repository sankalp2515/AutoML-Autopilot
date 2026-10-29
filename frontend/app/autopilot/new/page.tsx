'use client';

import { useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { autopilot, type Sample, type UploadInfo, type UploadSpec } from '@/lib/autopilot';
import { Badge, Button, Card } from '@/components/ui';
import { toast } from '@/lib/toast';
import { Field, Select, DataTable } from '@/components/autopilot/primitives';

const label: React.CSSProperties = { display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--t2)', margin: '12px 0 4px' };
const hint: React.CSSProperties = { fontSize: 11.5, color: 'var(--t3)', margin: '3px 0 0', lineHeight: 1.45 };
const input: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', padding: '8px 10px', borderRadius: 8, border: '1px solid var(--bd)',
  background: 'var(--inp)', color: 'var(--t1)', fontSize: 13, fontFamily: 'inherit',
};

function parsePackages(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of text.split(/\n|,/)) {
    const m = line.trim().match(/^([A-Za-z0-9_.-]+)\s*(?:==|=|\s)\s*([0-9][A-Za-z0-9.+-]*)$/);
    if (m) out[m[1].toLowerCase()] = m[2];
    else if (line.trim()) throw new Error(`Invalid package line: ${line}. Use package==1.2.3.`);
  }
  return out;
}

export default function BringYourOwnIncident() {
  const router = useRouter();
  const [refFile, setRefFile] = useState<File | null>(null);
  const [curFile, setCurFile] = useState<File | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [info, setInfo] = useState<UploadInfo | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [spec, setSpec] = useState<UploadSpec>({
    title: '', symptom: '', notes: '', target: '', positive_label: '', metric: 'f1',
    model_family: 'logistic_regression', serving_threshold: 0.5, trained_threshold: null,
    segment_column: null, segment_value: null, segment_max_drop: 0.05, exclude_columns: [],
  });
  const [pkgBefore, setPkgBefore] = useState('');
  const [pkgAfter, setPkgAfter] = useState('');
  const [docs, setDocs] = useState('');
  const [recent, setRecent] = useState<UploadInfo[]>([]);
  const [stepMode, setStepMode] = useState(false);
  const [planner, setPlanner] = useState('auto');
  const [research, setResearch] = useState('auto');
  const [serveText, setServeText] = useState('0.5');
  const [configured, setConfigured] = useState(false);
  const [validation, setValidation] = useState<string[]>([]);
  const refreshHistory = () => autopilot.uploads().then((r) => setRecent(r.uploads)).catch((e: Error) => toast(e.message, 'error'));

  useEffect(() => { autopilot.samples().then((r) => setSamples(r.samples)).catch(() => {}); }, []);
  useEffect(() => { void refreshHistory(); }, []);
  useEffect(() => { setConfigured(false); setWarnings([]); }, [spec, docs, pkgBefore, pkgAfter, serveText]);
  const packagePreview = (text: string) => { try { return JSON.stringify(parsePackages(text)); } catch (e) { return (e as Error).message; } };

  const set = <K extends keyof UploadSpec>(k: K, v: UploadSpec[K]) => setSpec((s) => ({ ...s, [k]: v }));
  const col = (name?: string | null) => info?.columns.find((c) => c.name === name);
  const targetValues = useMemo(() => col(spec.target)?.values ?? [], [info, spec.target]); // eslint-disable-line react-hooks/exhaustive-deps
  const segmentValues = useMemo(() => col(spec.segment_column)?.values ?? [], [info, spec.segment_column]); // eslint-disable-line react-hooks/exhaustive-deps

  async function doUpload(ref: Blob, cur: Blob, names?: [string, string]) {
    setBusy((previous) => previous?.startsWith('sample:') ? previous : 'upload');
    try {
      const u = await autopilot.upload(ref, cur, names);
      setInfo(u); setConfigured(false); void refreshHistory();
      setSpec((s) => ({ ...s, target: s.target && u.target_candidates.includes(s.target) ? s.target : u.target_candidates[0] ?? '' }));
    } catch (e) {
      toast((e as Error).message, 'error');
    } finally {
      setBusy(null);
    }
  }

  async function loadSample(sm: Sample) {
    setBusy(`sample:${sm.id}`);
    try {
      const [r, c] = await Promise.all(['reference', 'current'].map((w) =>
        fetch(autopilot.sampleUrl(sm.id, w as 'reference' | 'current')).then((x) => { if (!x.ok) throw new Error(`Sample download failed (${x.status})`); return x.blob(); })));
      setSpec((s) => ({ ...s, ...sm.suggested, title: sm.title, symptom: sm.symptom, notes: sm.notes } as UploadSpec));
      setServeText(String(sm.suggested.serving_threshold ?? 0.5));
      await doUpload(r, c, [`${sm.id}-reference.csv`, `${sm.id}-current.csv`]);
    } catch (e) {
      toast((e as Error).message, 'error');
    } finally {
      setBusy(null);
    }
  }

  async function start() {
    if (!info) return;
    setBusy('start');
    try {
      const errors: string[] = [];
      const urls = docs.split(/\s+/).filter(Boolean);
      if (!serveText || Number(serveText) < 0.01 || Number(serveText) > 0.99) errors.push('Serving threshold must be between 0.01 and 0.99.');
      if (spec.trained_threshold != null && (spec.trained_threshold < 0.01 || spec.trained_threshold > 0.99)) errors.push('Training threshold must be between 0.01 and 0.99.');
      if (urls.length > 3 || urls.some((u) => { try { const p = new URL(u); return p.protocol !== 'https:' || !!p.username || !!p.password; } catch { return true; } })) errors.push('Enter up to three public HTTPS documentation URLs.');
      try { parsePackages(pkgBefore); parsePackages(pkgAfter); } catch (e) { errors.push((e as Error).message); }
      setValidation(errors);
      if (errors.length) { setBusy(null); return; }
      const body: UploadSpec = {
        ...spec,
        serving_threshold: Number(serveText),
        trained_threshold: spec.trained_threshold || null,
        segment_column: spec.segment_column || null,
        segment_value: spec.segment_column ? spec.segment_value || null : null,
        packages_before: parsePackages(pkgBefore),
        packages_after: parsePackages(pkgAfter),
        docs_urls: docs.split(/\s+/).map((u) => u.trim()).filter(Boolean),
      };
      if (!configured) {
        const c = await autopilot.configure(info.upload_id, body);
        setWarnings(c.warnings); setConfigured(true);
        if (c.warnings.length) { setBusy(null); return; }
      }
      const r = await autopilot.create({ scenario_id: `upload:${info.upload_id}`, auto_run: !stepMode, planner, research }, `${info.upload_id}-${Date.now()}`);
      router.push(`/autopilot/${r.run_id}`);
    } catch (e) {
      toast((e as Error).message, 'error');
      setBusy(null);
    }
  }

  const ready = !!(info && spec.title.length >= 3 && spec.symptom.length >= 3 && spec.target && spec.positive_label);

  return (
    <div style={{ padding: '24px 20px 48px', maxWidth: 980, margin: '0 auto' }}>
      <Link href="/autopilot" style={{ fontSize: 12, color: 'var(--acc)' }}>← Autopilot</Link>
      <h1 className="ds-display" style={{ fontSize: 'clamp(20px, 2.6vw, 26px)', fontWeight: 700, margin: '8px 0 6px' }}>
        Investigate your own model
      </h1>
      <p style={{ color: 'var(--t2)', fontSize: 13.5, margin: 0, lineHeight: 1.55, maxWidth: 760 }}>
        Upload two windows of your data. Autopilot rebuilds your model family from the healthy window, measures what
        changed in the recent one, tests the competing causes and verifies any fix before recommending it.
      </p>
      <p style={{ ...hint, maxWidth: 760, marginTop: 8 }}>
        Privacy: rows stay on this server. Nemotron receives column names, aggregate statistics and the team notes you supply; remove secrets first. Tavily sees library
        names and versions, plus any public URLs you list. Uploads are visible only to this browser. No model files are
        accepted, because loading an uploaded model would let it run code.
      </p>

      {recent.length > 0 && <Card style={{ marginTop: 16 }}><h2 style={{ fontSize: 14 }}>Your recent uploads</h2><p style={hint}>Files and derived investigations expire after 24 hours. Active investigations are retained until they finish.</p>{recent.map((u) => <div key={u.upload_id} className="ap-actions"><span style={{ flex: 1 }}>{u.title ?? 'Untitled upload'} · {u.rows.reference} reference rows</span><Button size="sm" variant="ghost" onClick={() => { setInfo(u); setConfigured(false); if (u.spec) { setSpec(u.spec); setServeText(String(u.spec.serving_threshold)); setPkgBefore(Object.entries(u.spec.packages_before ?? {}).map(([k,v]) => `${k}==${v}`).join('\n')); setPkgAfter(Object.entries(u.spec.packages_after ?? {}).map(([k,v]) => `${k}==${v}`).join('\n')); setDocs(u.spec.docs_urls?.join('\n') ?? ''); } }}>Reuse</Button><Button size="sm" variant="ghost" onClick={async () => { try { await autopilot.deleteUpload(u.upload_id); if (info?.upload_id === u.upload_id) setInfo(null); await refreshHistory(); } catch (e) { toast((e as Error).message, 'error'); } }}>Delete data and reports</Button></div>)}</Card>}
      <Card style={{ marginTop: 18 }}>
        <h2 style={{ fontSize: 14, margin: 0 }}>1 · Your data</h2>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(260px, 100%), 1fr))', gap: 14, marginTop: 10 }}>
          <div className="ap-drop" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); setRefFile(e.dataTransfer.files[0] ?? null); }}>
            <label style={label} htmlFor="ref">Reference window (CSV)</label>
            <input id="ref" type="file" accept=".csv,text/csv" onChange={(e) => setRefFile(e.target.files?.[0] ?? null)} />
            <p style={hint}>From when the model was trained and healthy, with the outcome column filled in. At least 300 rows.</p>
            <p style={hint}>Drop a CSV here. {refFile?.name}</p>
          </div>
          <div className="ap-drop" onDragOver={(e) => e.preventDefault()} onDrop={(e) => { e.preventDefault(); setCurFile(e.dataTransfer.files[0] ?? null); }}>
            <label style={label} htmlFor="cur">Current window (CSV)</label>
            <input id="cur" type="file" accept=".csv,text/csv" onChange={(e) => setCurFile(e.target.files?.[0] ?? null)} />
            <p style={hint}>Recent production inputs, same columns. Outcomes where they have arrived; blank is fine.</p>
            <p style={hint}>Drop a CSV here. {curFile?.name}</p>
          </div>
        </div>
        <div style={{ display: 'flex', gap: 10, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
          <Button size="sm" disabled={!refFile || !curFile} loading={busy === 'upload'}
            onClick={() => refFile && curFile && doUpload(refFile, curFile, [refFile.name, curFile.name])}>Upload</Button>
          <span style={{ fontSize: 12, color: 'var(--t3)' }}>Max 20 MB and 200,000 rows per file. Binary classification.</span>
        </div>
        {samples.length > 0 && (
          <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid var(--bd2)' }}>
            <div style={{ fontSize: 12, color: 'var(--t2)', marginBottom: 6 }}>No data at hand? Try a sample incident:</div>
            <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
              {samples.map((s) => (
                <span key={s.id} style={{ display: 'inline-flex', gap: 6, alignItems: 'center' }}>
                  <Button size="sm" variant="ghost" disabled={!!busy} loading={busy === `sample:${s.id}`} onClick={() => loadSample(s)}>{s.title.replace('Loan model: ', '')}</Button>
                  <a href={autopilot.sampleUrl(s.id, 'reference')} style={{ fontSize: 11, color: 'var(--acc)' }}>ref.csv</a>
                  <a href={autopilot.sampleUrl(s.id, 'current')} style={{ fontSize: 11, color: 'var(--acc)' }}>cur.csv</a>
                </span>
              ))}
            </div>
          </div>
        )}
        {info && (
          <p style={{ fontSize: 12.5, margin: '12px 0 0' }}>
            <Badge tone="success">uploaded</Badge> {info.rows.reference.toLocaleString()} reference rows ·{' '}
            {info.rows.current.toLocaleString()} current rows · {info.columns.length} columns
          </p>
        )}
      </Card>

      {info && (
        <Card style={{ marginTop: 16 }}>
          <h2 style={{ fontSize: 14 }}>Column review</h2><DataTable label="Uploaded column review" head={['Column', 'Inferred type', 'Missing ref / current', 'Examples']}>{info.columns.map((c) => <tr key={c.name}><td>{c.name}</td><td>{c.kind}{['id_like', 'high_cardinality'].includes(c.kind) ? ' · dropped' : ''}</td><td>{(c.null_rate * 100).toFixed(1)}% / {c.in_current ? `${((c.current_null_rate ?? 0) * 100).toFixed(1)}%` : 'column missing'}</td><td>{(c.samples ?? c.values.slice(0,3)).join(', ')}</td></tr>)}</DataTable>
          <h2 style={{ fontSize: 14, margin: 0 }}>2 · Tell Autopilot about the model</h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(260px, 100%), 1fr))', gap: '0 18px' }}>
            <div>
              <label style={label} htmlFor="title">Name of the incident</label>
              <input id="title" style={input} value={spec.title} onChange={(e) => set('title', e.target.value)} placeholder="Churn model: recall dropped" />
            </div>
            <div>
              <label style={label} htmlFor="symptom">What did you observe?</label>
              <input id="symptom" style={input} value={spec.symptom} onChange={(e) => set('symptom', e.target.value)} placeholder="Recall fell since last week's release" />
            </div>
            <div>
              <label style={label} htmlFor="target">Outcome column</label>
              <Select id="target" style={input} value={spec.target} onChange={(e) => { set('target', e.target.value); set('positive_label', ''); }}>
                <option value="">Choose…</option>
                {info.target_candidates.map((t) => <option key={t}>{t}</option>)}
              </Select>
              <p style={hint}>Only columns with exactly two values are listed.</p>
            </div>
            <div>
              <label style={label} htmlFor="pos">Which value is the positive class?</label>
              <Select id="pos" style={input} value={spec.positive_label} onChange={(e) => set('positive_label', e.target.value)}>
                <option value="">Choose…</option>
                {targetValues.map((v) => <option key={v}>{v}</option>)}
              </Select>
            </div>
            <div>
              <label style={label} htmlFor="metric">Metric that matters</label>
              <Select id="metric" style={input} value={spec.metric} onChange={(e) => set('metric', e.target.value as UploadSpec['metric'])}>
                <option value="f1">F1</option><option value="recall">Recall</option>
                <option value="precision">Precision</option><option value="balanced_accuracy">Balanced accuracy</option>
              </Select>
            </div>
            <div>
              <label style={label} htmlFor="family">Model family in production</label>
              <Select id="family" style={input} value={spec.model_family} onChange={(e) => set('model_family', e.target.value as UploadSpec['model_family'])}>
                <option value="logistic_regression">Logistic regression</option>
                <option value="hist_gradient_boosting">Gradient boosting (trees)</option>
                <option value="random_forest">Random forest</option><option value="xgboost">XGBoost (if installed)</option><option value="lightgbm">LightGBM (if installed)</option>
              </Select>
              <p style={hint}>Autopilot retrains this family on your reference window to stand in for your model.</p>
            </div>
            <div>
              <label style={label} htmlFor="serve">Decision threshold used in production</label>
              <input id="serve" type="number" min={0.01} max={0.99} step={0.01} style={input} value={serveText}
                onChange={(e) => setServeText(e.target.value)} aria-describedby="threshold-help" />
              <p id="threshold-help" style={hint}>Required: 0.01–0.99.{!serveText && ' Enter a serving threshold.'}</p>
            </div>
            <div>
              <label style={label} htmlFor="trained">Threshold it was trained with (if different)</label>
              <input id="trained" type="number" min={0.01} max={0.99} step={0.01} style={input} value={spec.trained_threshold ?? ''}
                onChange={(e) => set('trained_threshold', e.target.value ? Number(e.target.value) : null)} placeholder="same" />
            </div>
            <div>
              <label style={label} htmlFor="seg">Segment that must not get worse (optional)</label>
              <Select id="seg" style={input} value={spec.segment_column ?? ''} onChange={(e) => { set('segment_column', e.target.value || null); set('segment_value', null); }}>
                <option value="">None</option>
                {info.segment_candidates.filter((c) => c !== spec.target).map((c) => <option key={c}>{c}</option>)}
              </Select>
            </div>
            <div>
              <label style={label} htmlFor="segv">Protected group</label>
              <Select id="segv" style={input} disabled={!spec.segment_column} value={spec.segment_value ?? ''} onChange={(e) => set('segment_value', e.target.value || null)}>
                <option value="">Every group</option>
                {segmentValues.map((v) => <option key={v}>{v}</option>)}
              </Select>
              <p style={hint}>A fix is rejected if this group (or any group) loses more than {spec.segment_max_drop}.</p>
            </div>
          </div>
          <Field label="Your model’s probability column (optional)" htmlFor="score-col"><Select id="score-col" style={input} value={spec.score_column ?? ''} onChange={(e) => set('score_column', e.target.value || null)}><option value="">Auto-detect a column named score; otherwise reconstruct</option>{info.columns.filter((c) => c.name !== spec.target && c.kind === 'numeric').map((c) => <option key={c.name}>{c.name}</option>)}</Select><p style={hint}>Probabilities for the positive class must be present in both files. They are excluded from training. Threshold fixes use your scores; input repairs and retrains use the reconstructed model.</p></Field>

          <label style={label} htmlFor="notes">What changed recently? (read by Nemotron)</label>
          <textarea id="notes" rows={4} style={input} value={spec.notes} onChange={(e) => set('notes', e.target.value)}
            placeholder="Paste the on-call thread, release notes or ticket text: deploys, vendor changes, campaigns, config edits…" />
          <p style={hint}>Free text that only the language model can use. It is context, not proof: every cause it suggests is tested.</p>

          <details style={{ marginTop: 12 }}>
            <summary style={{ fontSize: 12.5, cursor: 'pointer', color: 'var(--t2)' }}>Columns to ignore ({spec.exclude_columns?.length ?? 0})</summary>
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px 14px', marginTop: 8 }}>
              {info.columns.filter((c) => c.name !== spec.target).map((c) => (
                <label key={c.name} style={{ fontSize: 12, display: 'inline-flex', gap: 5, alignItems: 'center' }}>
                  <input type="checkbox" checked={spec.exclude_columns?.includes(c.name) ?? false}
                    onChange={(e) => set('exclude_columns', e.target.checked
                      ? [...(spec.exclude_columns ?? []), c.name]
                      : (spec.exclude_columns ?? []).filter((x) => x !== c.name))} />
                  {c.name} <span style={{ color: 'var(--t4)' }}>{c.kind === 'numeric' ? 'num' : c.kind === 'categorical' ? 'cat' : 'ignored'}</span>
                </label>
              ))}
            </div>
          </details>

          <details style={{ marginTop: 10 }}>
            <summary style={{ fontSize: 12.5, cursor: 'pointer', color: 'var(--t2)' }}>Library upgrade, error log and documentation (Tavily research)</summary>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(min(260px, 100%), 1fr))', gap: '0 18px' }}>
              <div>
                <label style={label} htmlFor="pb">Package versions before</label>
                <textarea id="pb" rows={3} style={input} value={pkgBefore} onChange={(e) => setPkgBefore(e.target.value)} placeholder={'scikit-learn==1.1.3\npandas==2.1.4'} />
                <p style={hint}>Parsed: {packagePreview(pkgBefore)}</p>
              </div>
              <div>
                <label style={label} htmlFor="pa">Package versions now</label>
                <textarea id="pa" rows={3} style={input} value={pkgAfter} onChange={(e) => setPkgAfter(e.target.value)} placeholder={'scikit-learn==1.8.0\npandas==2.2.3'} />
                <p style={hint}>Parsed: {packagePreview(pkgAfter)}</p>
              </div>
            </div>
            <p style={hint}>Tavily reads the official release notes of each upgraded package (only names and versions are searched).</p>
            <label style={label} htmlFor="log">Error log from your pipeline (optional)</label>
            <textarea id="log" rows={4} style={{ ...input, fontFamily: 'var(--font-mono), monospace', fontSize: 12 }} value={spec.error_log ?? ''}
              onChange={(e) => set('error_log', e.target.value)} placeholder="TypeError: OneHotEncoder.__init__() got an unexpected keyword argument 'sparse'" />
            <p style={hint}>Only the exception, class and argument names are kept; paths and values are dropped.</p>
            <label style={label} htmlFor="docs">Public documentation to read (optional, up to 3 https URLs)</label>
            <textarea id="docs" rows={2} style={input} value={docs} onChange={(e) => setDocs(e.target.value)} placeholder="https://vendor.example.com/api/changelog" />
            <p style={hint}>Vendor changelogs or data dictionaries. Tavily Extract reads them; Nemotron uses them as context.</p>
          </details>
        </Card>
      )}

      {info && (
        <Card style={{ marginTop: 16 }}>
          <h2 style={{ fontSize: 14, margin: '0 0 8px' }}>3 · Investigate</h2>
          <details><summary>Advanced investigation options</summary><label className="ap-field"><input type="checkbox" checked={stepMode} onChange={(e) => setStepMode(e.target.checked)} /> Step mode: confirm each stage</label><label className="ap-field">Planner<Select value={planner} onChange={(e) => setPlanner(e.target.value)}><option value="auto">Nemotron with rules fallback</option><option value="deterministic">Rules only</option></Select></label><label className="ap-field">Research<Select value={research} onChange={(e) => setResearch(e.target.value)}><option value="auto">When evidence is needed</option><option value="off">Off</option></Select></label><label className="ap-field">Maximum segment quality drop<input type="number" min="0" max="0.5" step="0.01" value={spec.segment_max_drop} onChange={(e) => set('segment_max_drop', Number(e.target.value))} /></label></details>
          {validation.map((v) => <p key={v} role="alert" style={{ color: 'var(--danger)' }}>{v}</p>)}
          {warnings.map((w) => <p key={w} style={{ fontSize: 12, color: 'var(--warning)', margin: '0 0 6px' }}>{w}</p>)}
          <Button disabled={!ready || (!!busy && busy !== 'start')} loading={busy === 'start'} onClick={start} fullWidth>{configured && warnings.length ? 'Continue anyway' : 'Start investigation'}</Button>
          {!ready && <p style={hint}>Name the incident, describe what you saw, and choose the outcome column and positive class.</p>}
        </Card>
      )}
    </div>
  );
}
