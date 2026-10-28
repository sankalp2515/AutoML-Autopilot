'use client';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import {
  getRun,
  getResults,
  listArtifacts,
  getLLMStats,
  connectWebSocket,
  retryRun,
} from '@/lib/api';
import { toast } from '@/lib/toast';
import type { RunDetail, Results, Artifact, LLMStat, ProgressMessage } from '@/lib/types';
import AgentTimeline from '@/components/AgentTimeline';
import LiveLog from '@/components/LiveLog';
import MetricsView from '@/components/MetricsView';
import ArtifactsPanel from '@/components/ArtifactsPanel';
import LLMStatsPanel from '@/components/LLMStatsPanel';
import NotebookViewer from '@/components/NotebookViewer';
import DeployPanel from '@/components/DeployPanel';
import ModelToolsPanel from '@/components/ModelToolsPanel';
import PlaygroundPanel from '@/components/PlaygroundPanel';
import DriftPanel from '@/components/DriftPanel';
import DecisionPanel from '@/components/DecisionPanel';
import ComparePanel from '@/components/ComparePanel';
import TrustPanel from '@/components/TrustPanel';
import InvestigatePanel from '@/components/InvestigatePanel';

type Tab = 'overview' | 'pipeline' | 'results' | 'trust' | 'investigate' | 'compare' | 'playground' | 'decision' | 'drift' | 'notebook' | 'deploy' | 'tools' | 'artifacts' | 'llm';

function chip(status: string) {
  const m: Record<string, { bg: string; c: string; l: string }> = {
    completed: { bg: 'rgba(34,197,94,0.12)', c: '#16a34a', l: 'Complete' },
    running: { bg: 'rgba(11,110,116,0.12)', c: 'var(--acc)', l: 'Running' },
    queued: { bg: 'rgba(11,110,116,0.10)', c: 'var(--acc)', l: 'Queued' },
    failed: { bg: 'rgba(239,68,68,0.12)', c: '#ef4444', l: 'Failed' },
  };
  return m[status] ?? { bg: 'var(--inp)', c: 'var(--t3)', l: status };
}

function useElapsed(createdAt: string, status: string): string {
  const [elapsed, setElapsed] = useState('');
  useEffect(() => {
    const update = () => {
      const s = Math.floor((Date.now() - new Date(createdAt).getTime()) / 1000);
      if (s < 60) setElapsed(`${s}s`);
      else if (s < 3600) setElapsed(`${Math.floor(s / 60)}m ${s % 60}s`);
      else setElapsed(`${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`);
    };
    update();
    if (status === 'running' || status === 'queued') {
      const id = setInterval(update, 1000);
      return () => clearInterval(id);
    }
  }, [createdAt, status]);
  return elapsed;
}

export default function RunPage() {
  const params = useParams<{ run_id: string }>();
  const router = useRouter();
  const runId = params.run_id;

  const [run, setRun] = useState<RunDetail | null>(null);
  const [messages, setMessages] = useState<ProgressMessage[]>([]);
  const [isWsConnected, setIsWsConnected] = useState(false);
  const [activeAgent, setActiveAgent] = useState<string | undefined>(undefined);
  const [tab, setTab] = useState<Tab>('pipeline');
  const [results, setResults] = useState<Results | null>(null);
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [llmStats, setLlmStats] = useState<{ stats: LLMStat[]; totals: LLMStat } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retrying, setRetrying] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const statusRef = useRef<string>('running');

  const fetchRun = useCallback(async () => {
    try {
      const data = await getRun(runId);
      setRun(data);
      return data;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load run');
      return null;
    }
  }, [runId]);

  const fetchCompletionData = useCallback(async () => {
    try {
      const [res, arts, stats] = await Promise.all([
        getResults(runId).catch(() => null),
        listArtifacts(runId).catch(() => [] as Artifact[]),
        getLLMStats(runId).catch(() => null),
      ]);
      if (res) setResults(res);
      setArtifacts(arts);
      if (stats) setLlmStats(stats);
    } catch {
      /* ignore */
    }
  }, [runId]);

  const connectWS = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;
    const ws = connectWebSocket(
      runId,
      (msg) => {
        if (!msg.agent || !msg.message) return;
        const pm = msg as unknown as ProgressMessage;
        setMessages((prev) => [...prev, pm]);
        if (pm.agent && pm.agent !== 'orchestrator') setActiveAgent(pm.agent);
        if (typeof pm.message === 'string' && (pm.message.includes('completed') || pm.message.includes('failed'))) {
          fetchRun().then((updated) => {
            if (updated?.status === 'completed' || updated?.status === 'failed') {
              fetchCompletionData();
              setTab('overview');
              setActiveAgent(undefined);
            }
          });
        }
      },
      () => {
        setIsWsConnected(false);
        // Only keep reconnecting while the run is actually live. For a finished
        // run we connect once just to replay the stored log, then let it rest.
        if (statusRef.current === 'running' || statusRef.current === 'queued') {
          setTimeout(() => {
            if (wsRef.current?.readyState !== WebSocket.OPEN) connectWS();
          }, 2000);
        }
      }
    );
    ws.onopen = () => setIsWsConnected(true);
    wsRef.current = ws;
  }, [runId, fetchRun, fetchCompletionData]);

  useEffect(() => {
    fetchRun().then((initial) => {
      if (!initial) return;
      statusRef.current = initial.status;
      // Always connect once: the backend replays the stored progress log on
      // connect, so even a finished run's Live Log is populated (fixes the
      // "empty / Disconnected" state when revisiting a completed run).
      connectWS();
      if (initial.status === 'running' || initial.status === 'queued') {
        setTab('pipeline');
        pollRef.current = setInterval(() => fetchRun(), 4000);
      } else if (initial.status === 'completed' || initial.status === 'failed') {
        fetchCompletionData();
        setTab('overview');
      }
    });
    return () => {
      wsRef.current?.close();
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [runId]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (run?.status) statusRef.current = run.status;
    if (run?.status === 'completed' || run?.status === 'failed') {
      if (pollRef.current) {
        clearInterval(pollRef.current);
        pollRef.current = null;
      }
    }
  }, [run?.status]);

  const elapsed = useElapsed(run?.created_at ?? new Date().toISOString(), run?.status ?? 'completed');

  const handleRetry = async () => {
    setRetrying(true);
    try {
      await retryRun(runId);
      setMessages([]);
      setResults(null);
      setActiveAgent(undefined);
      statusRef.current = 'queued';
      setTab('pipeline');
      await fetchRun();
      connectWS();
      if (!pollRef.current) pollRef.current = setInterval(() => fetchRun(), 4000);
      toast('Retrying the pipeline from the start…', 'success');
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Retry failed', 'error');
    } finally {
      setRetrying(false);
    }
  };

  if (error) {
    return (
      <div style={{ padding: 48, textAlign: 'center' }}>
        <p style={{ color: '#ef4444', marginBottom: 12 }}>{error}</p>
        <button className="ds-ghost" style={{ padding: '8px 14px', fontSize: 13 }} onClick={() => router.push('/runs')}>← Back to runs</button>
      </div>
    );
  }
  if (!run) {
    return <div style={{ padding: 60, textAlign: 'center', color: 'var(--t3)', fontSize: 13 }}>Loading run…</div>;
  }

  const isActive = run.status === 'running' || run.status === 'queued';
  const isCompleted = run.status === 'completed';
  const s = chip(run.status);

  const TABS: { id: Tab; label: string; gated?: boolean }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'pipeline', label: 'Pipeline & Logs' },
    { id: 'results', label: 'Metrics', gated: !results && !isCompleted },
    { id: 'trust', label: 'Trust ★', gated: !isCompleted },
    { id: 'investigate', label: 'Investigate ★', gated: !isCompleted },
    { id: 'compare', label: 'Compare', gated: !isCompleted },
    { id: 'playground', label: 'Playground', gated: !isCompleted },
    { id: 'decision', label: 'Decisions ★', gated: !isCompleted },
    { id: 'drift', label: 'Drift Monitor', gated: !isCompleted },
    { id: 'notebook', label: 'Notebook', gated: !isCompleted },
    { id: 'artifacts', label: 'Artifacts', gated: artifacts.length === 0 && !isCompleted },
    { id: 'deploy', label: 'Deploy', gated: !isCompleted },
    { id: 'tools', label: 'Tools', gated: !isCompleted },
    { id: 'llm', label: 'LLM Stats', gated: !llmStats && !isCompleted },
  ];

  return (
    <div>
      {/* Header */}
      <div style={{ padding: '22px 28px 0', borderBottom: '1px solid var(--bd)', background: 'var(--sb)' }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 14, marginBottom: 16 }}>
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 4, flexWrap: 'wrap' }}>
              <h1 className="ds-display" style={{ fontSize: 18, fontWeight: 700, color: 'var(--t1)', letterSpacing: '-0.4px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 520 }}>{run.user_goal}</h1>
              <span className="ds-chip" style={{ background: s.bg, color: s.c }}>{s.l}</span>
              {isActive && activeAgent && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 5, fontSize: 11, color: 'var(--acc)' }}>
                  <div style={{ width: 6, height: 6, borderRadius: '50%', background: 'var(--acc)', animation: 'ds-pulse 1.2s infinite' }} />
                  {activeAgent.replace(/_/g, ' ')}
                </div>
              )}
            </div>
            <div style={{ fontSize: 12, color: 'var(--t2)' }}>
              {run.dataset_filename}{run.task_type ? ` · ${run.task_type.replace(/_/g, ' ')}` : ''}{run.target_column ? ` · 🎯 ${run.target_column}` : ''} · {elapsed}
            </div>
          </div>
          {isCompleted && (
            <div style={{ display: 'flex', gap: 8, flexShrink: 0 }}>
              <button className="ds-ghost" style={{ height: 34, padding: '0 14px', fontSize: 12 }} onClick={() => setTab('compare')}>Compare</button>
              <button className="ds-btn-acc" style={{ height: 34, padding: '0 14px', fontSize: 12 }} onClick={() => setTab('deploy')}>Deploy Model</button>
            </div>
          )}
          {run.status === 'failed' && (
            <button className="ds-btn-acc" style={{ height: 34, padding: '0 16px', fontSize: 12, flexShrink: 0 }} disabled={retrying} onClick={handleRetry}>
              {retrying ? 'Retrying…' : '↻ Retry run'}
            </button>
          )}
        </div>
        {run.status === 'failed' && run.error_message && (
          <div style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(239,68,68,0.2)', borderRadius: 8, padding: '8px 12px', fontSize: 12, color: '#ef4444', marginBottom: 14 }}>⚠ {run.error_message}</div>
        )}
        {/* Tabs */}
        <div style={{ display: 'flex', gap: 0, overflowX: 'auto' }}>
          {TABS.map((t) => (
            <button key={t.id} className={`ds-tab${tab === t.id ? ' active' : ''}`} disabled={t.gated} style={t.gated ? { opacity: 0.4, cursor: 'not-allowed' } : undefined} onClick={() => !t.gated && setTab(t.id)}>{t.label}</button>
          ))}
        </div>
      </div>

      {/* Body */}
      <div style={{ padding: '24px 28px' }}>
        {tab === 'overview' && <Overview run={run} results={results} onTab={setTab} />}

        {tab === 'pipeline' && (
          <div className="ds-grid-2col" style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 18, alignItems: 'start' }}>
            <div className="ds-card" style={{ padding: 16 }}>
              <div className="ds-mono" style={{ fontSize: 10, color: 'var(--t3)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 12 }}>The Process</div>
              <AgentTimeline steps={run.agent_steps ?? []} activeAgent={activeAgent} />
            </div>
            <div className="ds-card" style={{ overflow: 'hidden', height: 785 }}>
              <LiveLog messages={messages} isConnected={isWsConnected} status={run.status} />
            </div>
          </div>
        )}

        {tab === 'results' && (
          <div className="ds-fade">
            {results ? <MetricsView results={results} /> : <div className="ds-card" style={{ padding: 32, textAlign: 'center', color: 'var(--t3)', fontSize: 13 }}>{isActive ? 'Metrics available after the pipeline completes…' : 'Loading metrics…'}</div>}
          </div>
        )}
        {tab === 'trust' && <div className="ds-fade"><TrustPanel runId={runId} /></div>}
        {tab === 'investigate' && <div className="ds-fade"><InvestigatePanel runId={runId} /></div>}
        {tab === 'compare' && <div className="ds-fade"><ComparePanel logs={run.decision_logs ?? []} winnerModel={run.winner_model} primaryMetric={run.primary_metric} /></div>}
        {tab === 'playground' && <div className="ds-fade"><PlaygroundPanel runId={runId} /></div>}
        {tab === 'decision' && <div className="ds-fade"><DecisionPanel runId={runId} /></div>}
        {tab === 'drift' && <div className="ds-fade"><DriftPanel runId={runId} /></div>}
        {tab === 'notebook' && <div className="ds-card" style={{ padding: 20 }}><NotebookViewer runId={runId} /></div>}
        {tab === 'artifacts' && <div className="ds-card" style={{ padding: 20 }}><ArtifactsPanel artifacts={artifacts} runId={runId} /></div>}
        {tab === 'deploy' && <div className="ds-card" style={{ padding: 20 }}><DeployPanel runId={runId} /></div>}
        {tab === 'tools' && <div className="ds-card" style={{ padding: 20 }}><ModelToolsPanel runId={runId} /></div>}
        {tab === 'llm' && (
          <div className="ds-card" style={{ padding: 20 }}>
            {llmStats ? <LLMStatsPanel stats={llmStats.stats} totals={llmStats.totals} /> : <div style={{ padding: 32, textAlign: 'center', color: 'var(--t3)', fontSize: 13 }}>{isActive ? 'LLM stats available after the pipeline completes…' : 'Loading…'}</div>}
          </div>
        )}

        {run.decision_logs && run.decision_logs.length > 0 && <DecisionLog logs={run.decision_logs} />}
      </div>
    </div>
  );
}

function fmtDur(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function framework(m: string | null): string {
  if (!m) return '—';
  const s = m.toLowerCase();
  if (s.includes('xgb')) return 'XGBoost';
  if (s.includes('lgbm') || s.includes('lightgbm')) return 'LightGBM';
  if (s.includes('catboost')) return 'CatBoost';
  if (s.includes('torch') || s.includes('mlp')) return 'PyTorch';
  return 'scikit-learn';
}

function Overview({ run, results, onTab }: { run: RunDetail; results: Results | null; onTab: (t: Tab) => void }) {
  const end = run.status === 'running' || run.status === 'queued' ? Date.now() : new Date(run.updated_at).getTime();
  const duration = fmtDur(end - new Date(run.created_at).getTime());
  const steps = run.agent_steps ?? [];
  const done = steps.filter((s) => s.status === 'completed').length;
  const total = Math.max(steps.length, 10);
  const meta = results?.meta;
  const sizeStr = meta?.model_size_kb != null ? (meta.model_size_kb >= 1024 ? `${(meta.model_size_kb / 1024).toFixed(1)} MB` : `${meta.model_size_kb} KB`) : null;
  const cards = [
    { label: 'Final Score', value: run.final_score != null ? run.final_score.toFixed(4) : '—', sub: run.primary_metric ?? 'score' },
    { label: 'Duration', value: duration, sub: 'pipeline time' },
    { label: 'Algorithm', value: run.winner_model ?? '—', sub: framework(run.winner_model) },
  ];
  const details: { label: string; value: string }[] = [
    { label: 'Algorithm', value: run.winner_model ?? '—' },
    { label: 'Framework', value: meta?.framework_version ? `${framework(run.winner_model)} ${meta.framework_version}` : framework(run.winner_model) },
    ...(meta?.hpo_trials != null ? [{ label: 'HPO trials', value: String(meta.hpo_trials) }] : []),
    { label: 'Task type', value: run.task_type ? run.task_type.replace(/_/g, ' ') : '—' },
    { label: 'Target', value: run.target_column ?? '—' },
    { label: 'Primary metric', value: run.primary_metric ?? '—' },
    ...(meta?.n_rows != null ? [{ label: 'Dataset rows', value: meta.n_rows.toLocaleString() }] : []),
    ...(meta?.n_columns != null ? [{ label: 'Columns', value: String(meta.n_columns) }] : []),
    ...(sizeStr ? [{ label: 'Model size', value: sizeStr }] : []),
    { label: 'Iterations', value: String(run.iteration_count ?? 0) },
    { label: 'Baseline → Final', value: run.baseline_score != null && run.final_score != null ? `${run.baseline_score.toFixed(3)} → ${run.final_score.toFixed(3)}` : '—' },
  ];
  const scorePct = run.final_score != null ? Math.min(100, Math.abs(run.final_score) * 100) : 0;

  return (
    <div className="ds-grid-side" style={{ display: 'grid', gridTemplateColumns: '1fr 320px', gap: 22 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3,1fr)', gap: 14 }}>
          {cards.map((c, i) => (
            <div key={c.label} className={`ds-card ds-fade-${Math.min(i, 3)}`} style={{ padding: 18 }}>
              <div className="ds-mono" style={{ fontSize: 10, color: 'var(--t2)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 7 }}>{c.label}</div>
              <div className="ds-display" style={{ fontSize: 20, fontWeight: 800, color: c.label === 'Final Score' ? 'var(--acc)' : 'var(--t1)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.value}</div>
              <div style={{ fontSize: 11, color: 'var(--t4)' }}>{c.sub}</div>
            </div>
          ))}
        </div>
        <div className="ds-card ds-fade" style={{ padding: 18 }}>
          <div className="ds-mono" style={{ fontSize: 10, color: 'var(--t2)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 10 }}>ML Goal</div>
          <p style={{ fontSize: 13, color: 'var(--t2)', lineHeight: 1.65 }}>{run.user_goal}</p>
        </div>
        {meta?.model_recommendations && (
          <div className="ds-card ds-fade-1" style={{ padding: 18, borderLeft: '3px solid var(--acc)' }}>
            <div className="ds-mono" style={{ fontSize: 10, color: 'var(--t2)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 10 }}>Scout Recommendations (not installed)</div>
            <p style={{ fontSize: 12.5, color: 'var(--t2)', lineHeight: 1.6, whiteSpace: 'pre-line' }}>{meta.model_recommendations}</p>
          </div>
        )}
        <div className="ds-card ds-fade-1" style={{ padding: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
            <div className="ds-mono" style={{ fontSize: 10, color: 'var(--t2)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Pipeline Progress</div>
            <div style={{ fontSize: 12, fontWeight: 600, color: done >= total ? '#16a34a' : 'var(--acc)' }}>{done}/{total} stages</div>
          </div>
          <div style={{ display: 'flex', gap: 3 }}>
            {Array.from({ length: total }).map((_, i) => (
              <div key={i} onClick={() => onTab('pipeline')} style={{ flex: 1, height: 7, borderRadius: 2, cursor: 'pointer', background: i < done ? '#22c55e' : 'var(--inp)' }} />
            ))}
          </div>
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div className="ds-card ds-fade" style={{ padding: 22, textAlign: 'center', background: 'var(--accw)', borderColor: 'rgba(11,110,116,0.22)' }}>
          <div className="ds-mono" style={{ fontSize: 10, color: 'var(--acc)', textTransform: 'uppercase', letterSpacing: '0.6px', marginBottom: 7 }}>Final Score</div>
          <div className="ds-display" style={{ fontSize: 46, fontWeight: 800, color: 'var(--t1)', letterSpacing: '-2px', lineHeight: 1 }}>{run.final_score != null ? run.final_score.toFixed(3) : '—'}</div>
          <div style={{ fontSize: 12, color: 'var(--acc)', marginTop: 4, marginBottom: 14 }}>{run.primary_metric ?? 'score'}</div>
          <div style={{ height: 5, borderRadius: 100, background: 'var(--inp)', overflow: 'hidden' }}><div className="ds-bar-fill" style={{ height: 5, borderRadius: 100, background: 'var(--acc)', width: `${scorePct}%` }} /></div>
        </div>
        <div className="ds-card ds-fade-1" style={{ padding: 18 }}>
          <div className="ds-mono" style={{ fontSize: 10, color: 'var(--t2)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 14 }}>Model Details</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            {details.map((d) => (
              <div key={d.label} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10 }}>
                <span style={{ fontSize: 12, color: 'var(--t2)' }}>{d.label}</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: 'var(--t1)', textAlign: 'right', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 170 }}>{d.value}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="ds-card ds-fade-2" style={{ padding: 18 }}>
          <div className="ds-mono" style={{ fontSize: 10, color: 'var(--t2)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 11 }}>Quick Actions</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
            <button className="ds-btn-acc" style={{ width: '100%', height: 34, fontSize: 12 }} onClick={() => onTab('trust')}>Run Trust Audit ★</button>
            <button className="ds-ghost" style={{ width: '100%', height: 34, fontSize: 12 }} onClick={() => onTab('deploy')}>Deploy Model</button>
            <button className="ds-ghost" style={{ width: '100%', height: 34, fontSize: 12 }} onClick={() => onTab('playground')}>Try Playground</button>
            <button className="ds-ghost" style={{ width: '100%', height: 34, fontSize: 12 }} onClick={() => onTab('results')}>View Metrics</button>
          </div>
        </div>
      </div>
    </div>
  );
}

function DecisionLog({ logs }: { logs: RunDetail['decision_logs'] }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState('');
  const filtered = search
    ? logs.filter((l) => l.decision.toLowerCase().includes(search.toLowerCase()) || l.agent_name.toLowerCase().includes(search.toLowerCase()))
    : logs;
  return (
    <div className="ds-card" style={{ overflow: 'hidden', marginTop: 18 }}>
      <button onClick={() => setOpen(!open)} style={{ width: '100%', display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', background: 'transparent', border: 'none', cursor: 'pointer' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--t1)' }}>🧾 Decision Log <span style={{ color: 'var(--t4)', fontWeight: 400 }}>· {logs.length} decisions</span></span>
        <span style={{ color: 'var(--t4)', transform: open ? 'rotate(180deg)' : 'none', transition: 'transform .2s' }}>▼</span>
      </button>
      {open && (
        <div style={{ borderTop: '1px solid var(--bd)' }}>
          <div style={{ padding: 12, borderBottom: '1px solid var(--bd2)' }}>
            <input className="ds-input" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filter decisions…" style={{ width: '100%', height: 34, padding: '0 12px', fontSize: 12 }} />
          </div>
          <div style={{ maxHeight: 400, overflowY: 'auto' }}>
            {filtered.map((log, i) => (
              <div key={i} style={{ padding: '12px 18px', borderBottom: '1px solid var(--bd2)' }}>
                <div style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                  <span className="ds-chip ds-mono" style={{ background: 'var(--accw)', color: 'var(--acc)', flexShrink: 0, textTransform: 'uppercase', fontSize: 10 }}>{log.agent_name.replace(/_/g, ' ')}</span>
                  <div style={{ minWidth: 0 }}>
                    <p style={{ fontSize: 12.5, color: 'var(--t1)', fontWeight: 600 }}>{log.decision}</p>
                    <p style={{ fontSize: 12, color: 'var(--t2)', marginTop: 2, lineHeight: 1.5 }}>{log.reasoning}</p>
                    {log.result_summary && <p className="ds-mono" style={{ fontSize: 11, color: 'var(--t3)', marginTop: 4 }}>→ {log.result_summary}</p>}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
