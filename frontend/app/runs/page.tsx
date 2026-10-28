'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { listRuns } from '@/lib/api';
import type { Run } from '@/lib/types';

function chip(status: string) {
  const m: Record<string, { bg: string; c: string; l: string }> = {
    completed: { bg: 'rgba(34,197,94,0.12)', c: '#16a34a', l: 'Complete' },
    running: { bg: 'rgba(11,110,116,0.12)', c: 'var(--acc)', l: 'Running' },
    queued: { bg: 'rgba(11,110,116,0.10)', c: 'var(--acc)', l: 'Queued' },
    failed: { bg: 'rgba(239,68,68,0.12)', c: '#ef4444', l: 'Failed' },
  };
  return m[status] ?? { bg: 'var(--inp)', c: 'var(--t3)', l: status };
}

function timeAgo(iso: string): string {
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

const FILTERS = ['all', 'completed', 'running', 'queued', 'failed'] as const;
const GRID = '2fr 1.3fr 1.2fr 0.9fr 0.9fr 0.8fr 0.8fr';

function gradeChip(grade?: string | null) {
  if (!grade || grade === '?') return null;
  const c = grade === 'A' || grade === 'B' ? '#16a34a' : grade === 'C' ? '#d97706' : '#ef4444';
  return { c, bg: `${c}1f` };
}

export default function RunsPage() {
  const router = useRouter();
  const [runs, setRuns] = useState<Run[]>([]);
  const [query, setQuery] = useState('');
  const [filter, setFilter] = useState<string>('all');
  const [loading, setLoading] = useState(true);

  const fetchRuns = useCallback(async () => {
    try {
      const d = await listRuns(0, 50);
      setRuns(d.runs);
    } catch {
      /* ignore */
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchRuns();
    const id = setInterval(fetchRuns, 5000);
    return () => clearInterval(id);
  }, [fetchRuns]);

  const stats = useMemo(() => {
    const done = runs.filter((r) => r.status === 'completed');
    const scores = done.map((r) => r.final_score).filter((s): s is number => s != null);
    const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
    return [
      { label: 'Total Runs', value: String(runs.length), color: 'var(--t1)' },
      { label: 'Completed', value: String(done.length), color: '#16a34a' },
      { label: 'Running', value: String(runs.filter((r) => r.status === 'running' || r.status === 'queued').length), color: 'var(--acc)' },
      { label: 'Avg Score', value: avg != null ? avg.toFixed(3) : '—', color: 'var(--t1)' },
    ];
  }, [runs]);

  const filtered = useMemo(() => {
    return runs.filter((r) => {
      if (filter !== 'all' && r.status !== filter) return false;
      if (query) {
        const q = query.toLowerCase();
        return (r.user_goal || '').toLowerCase().includes(q) || (r.dataset_filename || '').toLowerCase().includes(q);
      }
      return true;
    });
  }, [runs, filter, query]);

  return (
    <div style={{ padding: 32 }}>
      <div style={{ marginBottom: 24 }}>
        <h1 className="ds-display" style={{ fontSize: 22, fontWeight: 700, color: 'var(--t1)', letterSpacing: '-0.6px', marginBottom: 3 }}>Runs Dashboard</h1>
        <p style={{ fontSize: 13, color: 'var(--t2)' }}>Monitor and manage all your AutoML pipeline runs</p>
      </div>

      {/* Stat cards */}
      <div className="ds-grid-stats" style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 14, marginBottom: 24 }}>
        {stats.map((s) => (
          <div key={s.label} className="ds-card" style={{ padding: 18 }}>
            <div style={{ fontSize: 11, color: 'var(--t2)', fontWeight: 500, marginBottom: 8 }}>{s.label}</div>
            <div className="ds-display" style={{ fontSize: 26, fontWeight: 800, color: s.color, letterSpacing: '-0.5px' }}>{s.value}</div>
          </div>
        ))}
      </div>

      {/* Search + filters */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 14, flexWrap: 'wrap' }}>
        <div style={{ position: 'relative', flex: 1, minWidth: 200 }}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--t4)" strokeWidth="2" style={{ position: 'absolute', left: 11, top: '50%', transform: 'translateY(-50%)', pointerEvents: 'none' }}><circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" /></svg>
          <input className="ds-input" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search runs…" style={{ width: '100%', height: 36, padding: '0 12px 0 34px', fontSize: 12 }} />
        </div>
        {FILTERS.map((f) => (
          <button key={f} onClick={() => setFilter(f)} style={{ height: 36, padding: '0 14px', borderRadius: 8, fontSize: 12, fontWeight: 500, cursor: 'pointer', textTransform: 'capitalize', border: `1px solid ${filter === f ? 'var(--acc)' : 'var(--bd)'}`, background: filter === f ? 'var(--accw)' : 'var(--card)', color: filter === f ? 'var(--acc)' : 'var(--t2)' }}>{f}</button>
        ))}
      </div>

      {/* Table */}
      <div className="ds-card ds-scroll-x" style={{ overflow: 'hidden auto' }}>
       <div className="ds-table-min">
        <div style={{ display: 'grid', gridTemplateColumns: GRID, padding: '10px 18px', borderBottom: '1px solid var(--bd2)', gap: 12 }}>
          {['Run', 'Dataset', 'Task', 'Status', 'Score', 'Trust', 'Actions'].map((h) => (
            <div key={h} className="ds-mono" style={{ fontSize: 10, fontWeight: 700, color: 'var(--t4)', textTransform: 'uppercase', letterSpacing: '0.6px' }}>{h}</div>
          ))}
        </div>
        {loading && <div style={{ padding: 48, textAlign: 'center', color: 'var(--t4)', fontSize: 13 }}>Loading…</div>}
        {!loading && filtered.length === 0 && <div style={{ padding: 48, textAlign: 'center', color: 'var(--t4)', fontSize: 13 }}>No runs match your search</div>}
        {filtered.map((r) => {
          const s = chip(r.status);
          return (
            <div key={r.id} className="ds-row" onClick={() => router.push(`/runs/${r.id}`)} style={{ display: 'grid', gridTemplateColumns: GRID, padding: '14px 18px', borderBottom: '1px solid var(--bd2)', gap: 12, alignItems: 'center', cursor: 'pointer' }}>
              <div style={{ minWidth: 0 }}>
                <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--t1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.user_goal}</div>
                <div style={{ fontSize: 11, color: 'var(--t4)' }}>{timeAgo(r.created_at)}</div>
              </div>
              <div style={{ fontSize: 12, color: 'var(--t2)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{r.dataset_filename}</div>
              <div style={{ fontSize: 12, color: 'var(--t2)' }}>{r.task_type ? r.task_type.replace(/_/g, ' ') : '—'}</div>
              <div><span className="ds-chip" style={{ background: s.bg, color: s.c }}>{s.l}</span></div>
              <div className="ds-display" style={{ fontSize: 14, fontWeight: 700, color: 'var(--t1)', fontVariantNumeric: 'tabular-nums' }}>{r.final_score != null ? r.final_score.toFixed(3) : '—'}</div>
              <div>{(() => { const g = gradeChip(r.trust_grade); return g ? <span className="ds-chip ds-display" style={{ background: g.bg, color: g.c, fontWeight: 800, width: 26, justifyContent: 'center' }}>{r.trust_grade}</span> : <span style={{ fontSize: 12, color: 'var(--t4)' }}>—</span>; })()}</div>
              <div><span style={{ display: 'inline-flex', padding: '5px 10px', background: 'var(--accw)', border: '1px solid rgba(11,110,116,0.2)', borderRadius: 6, fontSize: 11, color: 'var(--acc)', fontWeight: 600 }}>View</span></div>
            </div>
          );
        })}
       </div>
      </div>
    </div>
  );
}
