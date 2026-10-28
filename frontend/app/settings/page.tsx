'use client';

import { useEffect, useMemo, useState } from 'react';
import { getConfig, patchConfig, type ConfigField } from '@/lib/api';
import { supabase, authEnabled } from '@/lib/supabase';
import { toast } from '@/lib/toast';

const DASHBOARDS = [
  { label: 'Grafana', sub: 'Dashboards', url: 'http://localhost:3001' },
  { label: 'MLflow', sub: 'Experiments', url: 'http://localhost:5000' },
  { label: 'Prometheus', sub: 'Metrics', url: 'http://localhost:9090' },
];

export default function SettingsPage() {
  const [fields, setFields] = useState<ConfigField[]>([]);
  const [groups, setGroups] = useState<string[]>([]);
  const [protectedApi, setProtectedApi] = useState(false);
  const [draft, setDraft] = useState<Record<string, string | number | boolean>>({});
  const [adminKey, setAdminKey] = useState('');
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [email, setEmail] = useState<string | null>(null);

  const load = () => {
    getConfig()
      .then((d) => {
        setFields(d.fields);
        setGroups(d.groups);
        setProtectedApi(d.api_key_protected);
        const init: Record<string, string | number | boolean> = {};
        for (const f of d.fields) init[f.key] = f.secret ? '' : f.value;
        setDraft(init);
      })
      .catch((e) => toast(e instanceof Error ? e.message : 'Failed to load config', 'error'))
      .finally(() => setLoading(false));
  };

  useEffect(load, []);

  useEffect(() => {
    if (!authEnabled || !supabase) return;
    supabase.auth.getUser().then(({ data }) => setEmail(data.user?.email ?? null));
  }, []);

  const byGroup = useMemo(() => {
    const m: Record<string, ConfigField[]> = {};
    for (const f of fields) (m[f.group] ??= []).push(f);
    return m;
  }, [fields]);

  const changes = useMemo(() => {
    const out: Record<string, unknown> = {};
    for (const f of fields) {
      const v = draft[f.key];
      if (f.secret) {
        if (typeof v === 'string' && v.length > 0) out[f.key] = v;
      } else if (v !== f.value) {
        out[f.key] = v;
      }
    }
    return out;
  }, [fields, draft]);

  const nChanges = Object.keys(changes).length;

  const save = async () => {
    if (nChanges === 0) return;
    setSaving(true);
    try {
      const res = await patchConfig(changes, adminKey || undefined);
      if (res.warning) toast(res.warning, 'info');
      toast(`Saved ${res.applied.length} setting${res.applied.length !== 1 ? 's' : ''}.`, 'success');
      if (res.restart_required.length) toast(`Restart required for: ${res.restart_required.join(', ')}`, 'info');
      load();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Save failed', 'error');
    } finally {
      setSaving(false);
    }
  };

  const set = (key: string, val: string | number | boolean) => setDraft((d) => ({ ...d, [key]: val }));

  return (
    <div style={{ maxWidth: 760, margin: '0 auto', padding: 32 }}>
      <div style={{ marginBottom: 20 }}>
        <h1 className="ds-display" style={{ fontSize: 22, fontWeight: 700, color: 'var(--t1)', letterSpacing: '-0.6px', marginBottom: 3 }}>Settings</h1>
        <p style={{ fontSize: 13, color: 'var(--t2)' }}>Edit runtime configuration. Most changes apply live; some need a backend restart.</p>
      </div>

      {!protectedApi && (
        <div className="ds-card" style={{ padding: '12px 16px', marginBottom: 18, background: 'rgba(239,68,68,0.06)', borderColor: 'rgba(239,68,68,0.25)', display: 'flex', gap: 10, alignItems: 'center' }}>
          <span style={{ color: '#ef4444', fontWeight: 700 }}>⚠</span>
          <span style={{ fontSize: 12.5, color: 'var(--t2)' }}>This panel is <b>unprotected</b> — anyone who can reach the server can change config. Set an <b>Admin API key</b> (Auth &amp; tenancy) and restart to require it.</span>
        </div>
      )}

      {loading ? (
        <div style={{ padding: 48, textAlign: 'center', color: 'var(--t3)' }}>Loading…</div>
      ) : (
        <>
          {protectedApi && (
            <div className="ds-card" style={{ padding: 16, marginBottom: 16 }}>
              <label style={{ display: 'block', fontSize: 12, fontWeight: 600, color: 'var(--t2)', marginBottom: 6 }}>Admin API key (required to save)</label>
              <input className="ds-input" type="password" value={adminKey} onChange={(e) => setAdminKey(e.target.value)} placeholder="X-API-Key" style={{ width: '100%', height: 38, padding: '0 12px' }} />
            </div>
          )}

          {groups.map((g) => (
            <div key={g} className="ds-card" style={{ padding: 20, marginBottom: 16 }}>
              <div className="ds-display" style={{ fontSize: 14, fontWeight: 700, color: 'var(--t1)', marginBottom: 16 }}>{g}</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
                {(byGroup[g] ?? []).map((f) => (
                  <div key={f.key} style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 16 }}>
                    <div style={{ minWidth: 0, flex: 1 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--t1)' }}>{f.label}</span>
                        {f.restart && <span className="ds-chip" style={{ background: 'var(--inp)', color: 'var(--t3)', fontSize: 9 }}>restart</span>}
                      </div>
                      {f.help && <div style={{ fontSize: 11.5, color: 'var(--t3)', marginTop: 2, lineHeight: 1.5 }}>{f.help}</div>}
                    </div>
                    <div style={{ flexShrink: 0, width: f.type === 'bool' ? 'auto' : 220 }}>
                      {f.type === 'bool' ? (
                        <div onClick={() => set(f.key, !draft[f.key])} className="ds-toggle" style={{ background: draft[f.key] ? 'var(--accw)' : 'var(--inp)' }}>
                          <div className="ds-toggle-knob" style={{ left: draft[f.key] ? 19 : 1, background: draft[f.key] ? 'var(--acc)' : 'var(--t4)' }} />
                        </div>
                      ) : f.secret ? (
                        <input className="ds-input" type="password" value={String(draft[f.key] ?? '')} onChange={(e) => set(f.key, e.target.value)} placeholder={f.is_set ? '•••••••• (set — leave blank to keep)' : 'not set'} style={{ width: '100%', height: 36, padding: '0 12px', fontSize: 12 }} />
                      ) : f.type === 'int' || f.type === 'float' ? (
                        <input className="ds-input" type="number" step={f.type === 'float' ? '0.01' : '1'} value={String(draft[f.key] ?? '')} onChange={(e) => set(f.key, f.type === 'float' ? parseFloat(e.target.value) : parseInt(e.target.value || '0', 10))} style={{ width: '100%', height: 36, padding: '0 12px', fontSize: 12 }} />
                      ) : (
                        <input className="ds-input" value={String(draft[f.key] ?? '')} onChange={(e) => set(f.key, e.target.value)} style={{ width: '100%', height: 36, padding: '0 12px', fontSize: 12 }} />
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          ))}

          {/* Discovery Agent — pending model proposals (ask-mode gate) */}
          <DiscoveryCard adminKey={adminKey} />

          {/* Dashboards + account */}
          <div className="ds-card" style={{ padding: 20, marginBottom: 16 }}>
            <div className="ds-display" style={{ fontSize: 14, fontWeight: 700, color: 'var(--t1)', marginBottom: 14 }}>Observability dashboards</div>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 10 }}>
              {DASHBOARDS.map((d) => (
                <a key={d.url} href={d.url} target="_blank" rel="noopener noreferrer" className="ds-card-2" style={{ padding: 14, textDecoration: 'none' }}>
                  <div className="ds-mono" style={{ fontSize: 11, color: 'var(--acc)', fontWeight: 600, textTransform: 'uppercase', letterSpacing: '0.1em' }}>{d.label} ↗</div>
                  <div style={{ fontSize: 11, color: 'var(--t4)', marginTop: 3 }}>{d.sub}</div>
                </a>
              ))}
            </div>
          </div>

          {authEnabled && (
            <div className="ds-card" style={{ padding: 20, marginBottom: 16, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
              <div>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t1)' }}>Account</div>
                <div style={{ fontSize: 12, color: 'var(--t3)' }}>{email ?? 'Signed out'}</div>
              </div>
              {email && <button className="ds-ghost" style={{ height: 34, padding: '0 14px', fontSize: 12 }} onClick={() => supabase!.auth.signOut()}>Sign out</button>}
            </div>
          )}
        </>
      )}

      {/* Sticky save bar */}
      <div style={{ position: 'sticky', bottom: 0, paddingTop: 8 }}>
        <div className="ds-card" style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', boxShadow: 'var(--shadow)' }}>
          <span style={{ fontSize: 12.5, color: 'var(--t2)' }}>{nChanges === 0 ? 'No unsaved changes' : `${nChanges} unsaved change${nChanges !== 1 ? 's' : ''}`}</span>
          <button className="ds-btn-acc" style={{ height: 36, padding: '0 20px', fontSize: 13 }} disabled={nChanges === 0 || saving} onClick={save}>{saving ? 'Saving…' : 'Save changes'}</button>
        </div>
      </div>
    </div>
  );
}

// ── Discovery Agent card ──────────────────────────────────────────────────────
interface Proposal {
  package: string; pinned: string; status: string; claim: string;
  vet: { ok: boolean; tier: string; license?: string; reason?: string };
  next_step?: string; times_proposed: number;
}

function DiscoveryCard({ adminKey }: { adminKey: string }) {
  const [proposals, setProposals] = useState<Proposal[]>([]);
  const [installed, setInstalled] = useState<string[]>([]);
  const [busy, setBusy] = useState('');
  const API = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000';

  const load = () => {
    fetch(`${API}/api/v1/discovery`).then((r) => r.json())
      .then((d) => { setProposals(d.proposals ?? []); setInstalled(d.installed ?? []); })
      .catch(() => {});
  };
  useEffect(load, []); // eslint-disable-line react-hooks/exhaustive-deps

  const decide = async (pkg: string, action: 'approve' | 'reject') => {
    setBusy(pkg);
    try {
      const res = await fetch(`${API}/api/v1/discovery/${pkg}/${action}`, {
        method: 'POST',
        headers: adminKey ? { 'X-API-Key': adminKey } : {},
      });
      if (!res.ok) { const b = await res.json().catch(() => ({})); toast(b.detail || `HTTP ${res.status}`, 'error'); }
      else toast(action === 'approve' ? `${pkg} approved — run scripts/apply_discovery.ps1 to install` : `${pkg} rejected`, 'success');
      load();
    } finally { setBusy(''); }
  };

  const chip = (s: string) =>
    s === 'approved' ? { bg: 'rgba(34,197,94,0.12)', c: '#16a34a' }
    : s === 'rejected' ? { bg: 'var(--inp)', c: 'var(--t4)' }
    : s === 'blocked' ? { bg: 'rgba(239,68,68,0.1)', c: '#ef4444' }
    : { bg: 'var(--accw)', c: 'var(--acc)' };

  return (
    <div className="ds-card" style={{ padding: 20, marginBottom: 16 }}>
      <div className="ds-display" style={{ fontSize: 14, fontWeight: 700, color: 'var(--t1)', marginBottom: 4 }}>Model Discovery</div>
      <p style={{ fontSize: 12, color: 'var(--t3)', marginBottom: 14 }}>
        The scout proposes vetted, pinned packages it wants installed; nothing changes without your approval,
        and installs happen only via a host-side image rebuild (the sandbox stays offline).
        {installed.length > 0 && <> Installed: <b>{installed.join(', ')}</b>.</>}
      </p>
      {proposals.length === 0 && <div style={{ fontSize: 12, color: 'var(--t4)' }}>No proposals yet — the scout raises them when a not-installed model would suit a dataset.</div>}
      {proposals.map((p) => {
        const c = chip(p.status);
        return (
          <div key={p.package} className="ds-card-2" style={{ padding: 14, marginBottom: 8, display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
            <div style={{ flex: 1, minWidth: 220 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t1)' }}>
                {p.pinned} <span className="ds-chip" style={{ background: c.bg, color: c.c, marginLeft: 6 }}>{p.status}</span>
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--t3)', marginTop: 3 }}>
                {p.vet?.ok ? `Tier ${p.vet.tier} · ${p.vet.license} · proposed ${p.times_proposed}×` : p.vet?.reason}
              </div>
              <div style={{ fontSize: 11.5, color: 'var(--t2)', marginTop: 3 }}>{p.claim}</div>
              {p.status === 'approved' && p.next_step && <div style={{ fontSize: 11, color: 'var(--acc)', marginTop: 4 }}>{p.next_step}</div>}
            </div>
            {p.status === 'pending' && (
              <div style={{ display: 'flex', gap: 8 }}>
                <button className="ds-btn-acc" style={{ height: 32, padding: '0 14px', fontSize: 12 }} disabled={busy === p.package} onClick={() => decide(p.package, 'approve')}>Approve</button>
                <button className="ds-ghost" style={{ height: 32, padding: '0 14px', fontSize: 12 }} disabled={busy === p.package} onClick={() => decide(p.package, 'reject')}>Reject</button>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
