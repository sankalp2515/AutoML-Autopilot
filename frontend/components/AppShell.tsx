'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import Link from 'next/link';
import { listRuns, createRun, listStudios, type StudioInfo } from '@/lib/api';
import type { Run } from '@/lib/types';
import type { ToastDetail } from '@/lib/toast';
import { supabase, authEnabled } from '@/lib/supabase';
import AuthGate from '@/components/AuthGate';
import { Button } from '@/components/ui';

// Hackathon build: Autopilot only (the orchestrator pages need services it does not run).
const AUTOPILOT_ONLY = process.env.NEXT_PUBLIC_AUTOPILOT_ONLY === 'true';

const EXAMPLE_GOALS = [
  'Predict which customers will churn in the next 30 days',
  'Predict house sale prices from property features',
  'Classify loan default risk from applicant data',
];

function statusDot(status: string): string {
  if (status === 'completed') return 'var(--jade, #22c55e)';
  if (status === 'running' || status === 'queued') return 'var(--acc)';
  if (status === 'failed') return '#ef4444';
  return 'var(--t4)';
}

// ── Toasts ────────────────────────────────────────────────────────────────────
function Toasts() {
  const [items, setItems] = useState<{ id: number; msg: string; type: string }[]>([]);
  useEffect(() => {
    const handler = (e: Event) => {
      const d = (e as CustomEvent<ToastDetail>).detail;
      const id = Date.now() + Math.random();
      setItems((p) => [...p, { id, msg: d.msg, type: d.type }]);
      setTimeout(() => setItems((p) => p.filter((t) => t.id !== id)), 4200);
    };
    window.addEventListener('app-toast', handler);
    return () => window.removeEventListener('app-toast', handler);
  }, []);

  return (
    <div role="region" aria-label="Notifications" aria-live="polite" style={{ position: 'fixed', bottom: 24, right: 24, zIndex: 9999, display: 'flex', flexDirection: 'column', gap: 8, pointerEvents: 'none' }}>
      {items.map((t) => {
        const c = t.type === 'success' ? '#16a34a' : t.type === 'error' ? '#ef4444' : 'var(--acc)';
        return (
          <div key={t.id} className="ds-card" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 10, minWidth: 280, maxWidth: 380, boxShadow: 'var(--shadow)', pointerEvents: 'all', animation: 'ds-toast-in .25s ease' }}>
            <div style={{ width: 18, height: 18, borderRadius: '50%', background: c, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 11, fontWeight: 800, flexShrink: 0 }}>
              {t.type === 'error' ? '!' : t.type === 'success' ? '✓' : 'i'}
            </div>
            <span style={{ fontSize: 13, fontWeight: 500, color: 'var(--t1)', flex: 1 }}>{t.msg}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── New Run modal ───────────────────────────────────────────────────────────
function NewRunModal({ onClose }: { onClose: () => void }) {
  const router = useRouter();
  const fileRef = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState(1);
  const [file, setFile] = useState<File | null>(null);
  const [pipeline, setPipeline] = useState<string>('tabular');
  // Platform seam: studios come from the registry, with a static fallback.
  const [studios, setStudios] = useState<StudioInfo[]>([
    { id: 'tabular', label: 'AutoML Studio', tagline: 'Classification & regression', available: true, dataset_contract: '' },
    { id: 'timeseries', label: 'Forecasting Studio', tagline: 'Forecasting · walk-forward', available: true, dataset_contract: '' },
  ]);
  useEffect(() => {
    listStudios().then((d) => { if (d.studios?.length) setStudios(d.studios); }).catch(() => {});
  }, []);
  const [goal, setGoal] = useState('');
  const [excludeColumns, setExcludeColumns] = useState('');
  const [fpFnPref, setFpFnPref] = useState('');
  const [interpretability, setInterpretability] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const steps = ['Dataset', 'Goal', 'Options'];

  // Escape closes the dialog — keyboard users shouldn't be trapped.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  const next = async () => {
    setError(null);
    if (step === 1) {
      if (!file) return setError('Select a CSV file to continue.');
      return setStep(2);
    }
    if (step === 2) {
      if (goal.trim().length < 10) return setError('Describe your goal (at least 10 characters).');
      return setStep(3);
    }
    // step 3 → submit
    setSubmitting(true);
    const fd = new FormData();
    fd.append('file', file!);
    fd.append('user_goal', goal.trim());
    fd.append('exclude_columns', excludeColumns.trim());
    fd.append('fp_fn_preference', fpFnPref.trim());
    fd.append('interpretability_required', String(interpretability));
    fd.append('pipeline', pipeline);
    try {
      const run = await createRun(fd);
      onClose();
      router.push(`/runs/${run.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to start run');
      setSubmitting(false);
    }
  };

  const label = 'block' as const;
  const lblStyle = { display: label, fontSize: 12, fontWeight: 600, color: 'var(--t2)', marginBottom: 6 };

  return (
    <div onClick={onClose} style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)', zIndex: 500, display: 'flex', alignItems: 'center', justifyContent: 'center', backdropFilter: 'blur(3px)' }}>
      <div role="dialog" aria-modal="true" aria-labelledby="newrun-title" onClick={(e) => e.stopPropagation()} className="ds-card" style={{ width: 'min(540px, 94vw)', maxHeight: '88vh', overflowY: 'auto', boxShadow: 'var(--shadow)', borderRadius: 18, animation: 'ds-modal-in .2s ease' }}>
        {/* Header + steps */}
        <div style={{ padding: '24px 28px 20px', borderBottom: '1px solid var(--bd)' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 20 }}>
            <span id="newrun-title" className="ds-display" style={{ fontSize: 16, fontWeight: 700, color: 'var(--t1)', letterSpacing: '-0.3px' }}>New Pipeline Run</span>
            <button type="button" onClick={onClose} aria-label="Close dialog" style={{ width: 28, height: 28, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: 'var(--t3)', fontSize: 18, background: 'var(--inp)', border: 'none' }}>×</button>
          </div>
          <div style={{ display: 'flex', alignItems: 'center' }}>
            {steps.map((s, i) => {
              const n = i + 1;
              const active = n <= step;
              return (
                <div key={s} style={{ display: 'flex', alignItems: 'center' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
                    <div style={{ width: 24, height: 24, borderRadius: '50%', background: active ? 'var(--acc)' : 'var(--inp)', border: `2px solid ${active ? 'var(--acc)' : 'var(--bd)'}`, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 800, color: active ? '#fff' : 'var(--t3)' }}>{n}</div>
                    <span style={{ fontSize: 12, fontWeight: 500, color: active ? 'var(--t1)' : 'var(--t3)' }}>{s}</span>
                  </div>
                  {i < steps.length - 1 && <div style={{ width: 40, height: 1, background: 'var(--bd)', margin: '0 10px' }} />}
                </div>
              );
            })}
          </div>
        </div>

        {/* Body */}
        <div style={{ padding: '24px 28px' }}>
          {step === 1 && (
            <>
              <div style={{ marginBottom: 18 }}>
                <label style={lblStyle}>Studio</label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 9 }}>
                  {studios.map((s) => (
                    <div key={s.id} onClick={() => s.available && setPipeline(s.id)} title={s.dataset_contract}
                      style={{ padding: '10px 13px', border: `1.5px solid ${pipeline === s.id ? 'var(--acc)' : 'var(--bd)'}`, borderRadius: 8, cursor: s.available ? 'pointer' : 'not-allowed', opacity: s.available ? 1 : 0.45, background: pipeline === s.id ? 'var(--accw)' : 'transparent' }}>
                      <div style={{ fontSize: 12, fontWeight: 600, color: pipeline === s.id ? 'var(--acc)' : 'var(--t1)' }}>
                        {s.label}{!s.available && <span style={{ fontWeight: 400, color: 'var(--t4)' }}> — soon</span>}
                      </div>
                      <div style={{ fontSize: 11, color: 'var(--t3)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{s.tagline}</div>
                    </div>
                  ))}
                </div>
              </div>
              <label style={lblStyle}>Dataset</label>
              <div onClick={() => fileRef.current?.click()} style={{ border: '2px dashed var(--bd)', borderRadius: 10, padding: 28, textAlign: 'center', cursor: 'pointer' }}>
                <input ref={fileRef} type="file" accept=".csv" style={{ display: 'none' }} onChange={(e) => { const f = e.target.files?.[0]; if (f) { setFile(f); setError(null); } }} />
                {file ? (
                  <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8, background: 'rgba(34,197,94,0.08)', border: '1px solid rgba(34,197,94,0.2)', borderRadius: 8, padding: '8px 14px' }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: '#16a34a' }}>✓ {file.name} — {(file.size / 1024).toFixed(0)} KB</span>
                  </div>
                ) : (
                  <>
                    <div style={{ fontSize: 13, fontWeight: 500, color: 'var(--t2)' }}>Drop a CSV file</div>
                    <div style={{ fontSize: 11, color: 'var(--t4)', marginTop: 3 }}>or <span style={{ color: 'var(--acc)' }}>browse</span></div>
                  </>
                )}
              </div>
            </>
          )}

          {step === 2 && (
            <>
              <label style={lblStyle}>Describe Your ML Goal</label>
              <textarea className="ds-input" value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="e.g. Predict which customers will churn in the next 30 days…" style={{ width: '100%', padding: '10px 12px', minHeight: 90, resize: 'vertical', lineHeight: 1.6 }} />
              <div style={{ display: 'flex', gap: 6, marginTop: 8, flexWrap: 'wrap' }}>
                {EXAMPLE_GOALS.map((g) => (
                  <div key={g} onClick={() => setGoal(g)} style={{ padding: '4px 10px', background: 'var(--inp)', border: '1px solid var(--bd)', borderRadius: 100, fontSize: 11, color: 'var(--t2)', cursor: 'pointer' }}>{g.slice(0, 36)}…</div>
                ))}
              </div>
            </>
          )}

          {step === 3 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
              <div>
                <label style={lblStyle}>Exclude columns (optional)</label>
                <input className="ds-input" value={excludeColumns} onChange={(e) => setExcludeColumns(e.target.value)} placeholder="id, name, ticket" style={{ width: '100%', height: 38, padding: '0 12px' }} />
              </div>
              <div>
                <label style={lblStyle}>Error preference (optional)</label>
                <input className="ds-input" value={fpFnPref} onChange={(e) => setFpFnPref(e.target.value)} placeholder="minimize false negatives" style={{ width: '100%', height: 38, padding: '0 12px' }} />
              </div>
              <label style={{ display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
                <div onClick={() => setInterpretability(!interpretability)} className="ds-toggle" style={{ background: interpretability ? 'var(--accw)' : 'var(--inp)' }}>
                  <div className="ds-toggle-knob" style={{ left: interpretability ? 19 : 1, background: interpretability ? 'var(--acc)' : 'var(--t4)' }} />
                </div>
                <span style={{ fontSize: 12, color: 'var(--t2)' }}>Interpretability required — linear models only</span>
              </label>
              <div style={{ background: 'var(--accw)', border: '1px solid rgba(11,110,116,0.15)', borderRadius: 10, padding: '14px 16px', fontSize: 12, color: 'var(--acc)', lineHeight: 1.55 }}>
                The Orchestrator selects preprocessing, feature engineering, models and tuning automatically — and hands back a deployable model with a full audit trail.
              </div>
            </div>
          )}

          {error && <div style={{ marginTop: 14, fontSize: 12, color: '#ef4444' }}>⚠ {error}</div>}
        </div>

        {/* Footer */}
        <div style={{ padding: '16px 28px 24px', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
          <Button variant="ghost" size="sm" onClick={() => (step === 1 ? onClose() : setStep(step - 1))}>{step === 1 ? 'Cancel' : 'Back'}</Button>
          <Button size="sm" loading={submitting} onClick={next} style={{ boxShadow: '0 4px 14px rgba(11,110,116,0.3)' }}>
            {step === 3 ? 'Run Pipeline' : 'Continue'}
          </Button>
        </div>
      </div>
    </div>
  );
}

// ── Shell ─────────────────────────────────────────────────────────────────────
export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [runs, setRuns] = useState<Run[]>([]);
  const [showModal, setShowModal] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [email, setEmail] = useState<string | null>(null);
  const [isDark, setIsDark] = useState(false);

  // Login page renders without the shell chrome.
  const bare = pathname === '/login';

  useEffect(() => {
    setIsDark(document.documentElement.classList.contains('dark'));
    const open = () => setShowModal(true);
    window.addEventListener('open-new-run', open);
    return () => window.removeEventListener('open-new-run', open);
  }, []);

  // Close the mobile drawer whenever the route changes.
  useEffect(() => { setSidebarOpen(false); }, [pathname]);

  useEffect(() => {
    if (bare) return;
    let alive = true;
    const load = () => (AUTOPILOT_ONLY ? Promise.resolve() : listRuns(0, 8).then((d) => { if (alive) setRuns(d.runs); }).catch(() => {}));
    load();
    const id = setInterval(load, 8000);
    return () => { alive = false; clearInterval(id); };
  }, [bare, pathname]);

  useEffect(() => {
    if (!authEnabled || !supabase) return;
    supabase.auth.getUser().then(({ data }) => setEmail(data.user?.email ?? null));
    const { data: sub } = supabase.auth.onAuthStateChange((_e, s) => setEmail(s?.user?.email ?? null));
    return () => sub.subscription.unsubscribe();
  }, []);

  const toggleTheme = useCallback(() => {
    const next = !isDark;
    document.documentElement.classList.toggle('dark', next);
    document.documentElement.classList.toggle('light', !next);
    localStorage.setItem('atelier-theme', next ? 'dark' : 'light');
    setIsDark(next);
  }, [isDark]);

  if (bare) return <>{children}</>;

  const dashActive = pathname === '/' || pathname.startsWith('/runs');
  const settingsActive = pathname.startsWith('/settings');
  const showBack = pathname.startsWith('/runs/') || pathname.startsWith('/autopilot/') || settingsActive;
  const pageTitle = pathname === '/' ? 'Home' : settingsActive ? 'Settings' : pathname === '/runs' ? 'Runs Dashboard' : pathname.startsWith('/runs/') ? 'Run Detail' : pathname === '/autopilot' ? 'Autopilot' : pathname.startsWith('/autopilot/benchmark') ? 'Autopilot · Benchmark' : pathname.startsWith('/autopilot/') ? 'Investigation' : 'AutoML Orchestrator';

  const avatar = (email ? email[0] : 'A').toUpperCase();

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: 'var(--bg)', color: 'var(--t1)', fontSize: 13 }}>
      {/* Sidebar */}
      {sidebarOpen && <div className="ds-sidebar-backdrop" onClick={() => setSidebarOpen(false)} />}
      <aside className={`ds-sidebar${sidebarOpen ? ' open' : ''}`} style={{ width: 216, minWidth: 216, background: 'var(--sb)', borderRight: '1px solid var(--bd)', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
        <div style={{ padding: '18px 16px 14px', borderBottom: '1px solid var(--bd)' }}>
          <Link href="/" style={{ display: 'flex', alignItems: 'center', gap: 10, textDecoration: 'none' }}>
            <div style={{ width: 32, height: 32, borderRadius: 7, background: 'var(--acc)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
              <svg width="19" height="19" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round"><line x1="5" y1="15" x2="5" y2="20" /><line x1="5" y1="4" x2="5" y2="11" /><line x1="12" y1="17" x2="12" y2="20" /><line x1="12" y1="4" x2="12" y2="13" /><line x1="19" y1="11" x2="19" y2="20" /><line x1="19" y1="4" x2="19" y2="7" /></svg>
            </div>
            <div>
              <div className="ds-display" style={{ fontSize: 14, fontWeight: 700, color: 'var(--t1)', letterSpacing: '-0.3px', lineHeight: 1.05 }}>{AUTOPILOT_ONLY ? 'AutoML Autopilot' : 'Orchestrator'}</div>
              <div className="ds-mono" style={{ fontSize: 8.5, color: 'var(--acc)', fontWeight: 600, letterSpacing: '1.4px', textTransform: 'uppercase' }}>{AUTOPILOT_ONLY ? 'ML incident resolver' : 'ML Control Plane'}</div>
            </div>
          </Link>
        </div>

        <nav style={{ padding: '10px 8px', display: 'flex', flexDirection: 'column', gap: 1 }}>
          <div style={{ fontSize: 9, fontWeight: 600, color: 'var(--t4)', letterSpacing: '0.8px', textTransform: 'uppercase', padding: '8px 8px 4px' }}>Platform</div>
          <button className={`ds-nav-item${pathname.startsWith('/autopilot') ? ' active' : ''}`} onClick={() => router.push('/autopilot')}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><circle cx="11" cy="11" r="7" /><line x1="21" y1="21" x2="16.65" y2="16.65" /><path d="M8 11h6M11 8v6" /></svg>
            Autopilot
          </button>
          {AUTOPILOT_ONLY ? (
            <button className={`ds-nav-item${pathname.startsWith('/autopilot/benchmark') ? ' active' : ''}`} onClick={() => router.push('/autopilot/benchmark')}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><line x1="6" y1="20" x2="6" y2="10" /><line x1="12" y1="20" x2="12" y2="4" /><line x1="18" y1="20" x2="18" y2="14" /></svg>
              Benchmark
            </button>
          ) : (<>
          <button className={`ds-nav-item${dashActive ? ' active' : ''}`} onClick={() => router.push('/runs')}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><rect x="3" y="3" width="7" height="7" rx="1" /><rect x="14" y="3" width="7" height="7" rx="1" /><rect x="3" y="14" width="7" height="7" rx="1" /><rect x="14" y="14" width="7" height="7" rx="1" /></svg>
            Dashboard
          </button>
          <button className="ds-nav-item" onClick={() => setShowModal(true)}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="16" /><line x1="8" y1="12" x2="16" y2="12" /></svg>
            New Run
          </button>
          <button className={`ds-nav-item${pathname.startsWith('/registry') ? ' active' : ''}`} onClick={() => router.push('/registry')}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M4 7h16M4 12h16M4 17h16" /><circle cx="8" cy="7" r="1.5" fill="currentColor" /><circle cx="14" cy="12" r="1.5" fill="currentColor" /></svg>
            Registry
          </button>
          <button className={`ds-nav-item${settingsActive ? ' active' : ''}`} onClick={() => router.push('/settings')}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="3" /><path d="M19.07 4.93l-1.41 1.41M4.93 4.93l1.41 1.41M12 2v2M12 20v2M2 12h2M20 12h2M19.07 19.07l-1.41-1.41M4.93 19.07l1.41-1.41" /></svg>
            Settings
          </button>
          </>)}

          {!AUTOPILOT_ONLY && <>
          <div style={{ fontSize: 9, fontWeight: 600, color: 'var(--t4)', letterSpacing: '0.8px', textTransform: 'uppercase', padding: '14px 8px 4px' }}>Recent Runs</div>
          {runs.length === 0 && <div style={{ padding: '4px 10px', fontSize: 11, color: 'var(--t4)' }}>No runs yet</div>}
          {runs.map((r) => (
            <div key={r.id} className="ds-run" onClick={() => router.push(`/runs/${r.id}`)}>
              <div style={{ width: 6, height: 6, borderRadius: '50%', flexShrink: 0, background: statusDot(r.status) }} />
              <span style={{ fontSize: 12, color: pathname === `/runs/${r.id}` ? 'var(--t1)' : 'var(--t2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.user_goal || r.dataset_filename}</span>
            </div>
          ))}
          </>}
        </nav>

        <div style={{ flex: 1 }} />
        <div style={{ padding: '11px 14px', borderTop: '1px solid var(--bd)', display: 'flex', alignItems: 'center', gap: 9 }}>
          <div style={{ width: 26, height: 26, borderRadius: '50%', background: 'var(--acc)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, color: '#fff', flexShrink: 0 }}>{avatar}</div>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--t1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{email ?? 'Local'}</div>
            <div style={{ fontSize: 10, color: 'var(--t4)' }}>{authEnabled ? (email ? 'Signed in' : 'Signed out') : 'Public mode'}</div>
          </div>
          <div onClick={toggleTheme} title="Toggle theme" style={{ width: 28, height: 28, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: 'var(--t3)', background: 'var(--inp)', flexShrink: 0 }}>
            {isDark ? (
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="5" /><line x1="12" y1="1" x2="12" y2="3" /><line x1="12" y1="21" x2="12" y2="23" /><line x1="4.2" y1="4.2" x2="5.6" y2="5.6" /><line x1="18.4" y1="18.4" x2="19.8" y2="19.8" /><line x1="1" y1="12" x2="3" y2="12" /><line x1="21" y1="12" x2="23" y2="12" /><line x1="4.2" y1="19.8" x2="5.6" y2="18.4" /><line x1="18.4" y1="5.6" x2="19.8" y2="4.2" /></svg>
            ) : (
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" /></svg>
            )}
          </div>
        </div>
      </aside>

      {/* Main */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', minWidth: 0 }}>
        <header style={{ height: 50, minHeight: 50, borderBottom: '1px solid var(--bd)', display: 'flex', alignItems: 'center', padding: '0 16px', gap: 8, background: 'var(--sb)' }}>
          <div className="ds-hamburger" onClick={() => setSidebarOpen(true)} title="Menu" style={{ width: 28, height: 28, borderRadius: 6, alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: 'var(--t2)', background: 'var(--inp)' }}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><line x1="3" y1="6" x2="21" y2="6" /><line x1="3" y1="12" x2="21" y2="12" /><line x1="3" y1="18" x2="21" y2="18" /></svg>
          </div>
          {showBack && (
            <div onClick={() => router.back()} title="Back" style={{ width: 26, height: 26, borderRadius: 6, display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: 'var(--t2)', background: 'var(--inp)' }}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><polyline points="15,18 9,12 15,6" /></svg>
            </div>
          )}
          <span style={{ fontSize: 13, color: 'var(--t1)', fontWeight: 600 }}>{pageTitle}</span>
          <div style={{ flex: 1 }} />
          <button className="ds-btn-acc" style={{ height: 32, padding: '0 13px', fontSize: 12, boxShadow: '0 2px 10px rgba(11,110,116,0.25)' }} onClick={() => (AUTOPILOT_ONLY ? router.push('/autopilot') : setShowModal(true))}>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="#fff" strokeWidth="2.5"><line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" /></svg>
            {AUTOPILOT_ONLY ? 'New investigation' : 'New Run'}
          </button>
          {!AUTOPILOT_ONLY && <div onClick={() => router.push('/settings')} style={{ width: 28, height: 28, borderRadius: '50%', background: 'var(--acc)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 10, fontWeight: 700, color: '#fff', cursor: 'pointer' }}>{avatar}</div>}
        </header>

        <main style={{ flex: 1, overflowY: 'auto', background: 'var(--bg)' }}>
          <AuthGate>{children}</AuthGate>
        </main>
      </div>

      {showModal && <NewRunModal onClose={() => setShowModal(false)} />}
      <Toasts />
    </div>
  );
}
