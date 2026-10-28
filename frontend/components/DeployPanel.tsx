'use client';
import { useCallback, useEffect, useState } from 'react';
import {
  API_BASE,
  deployModel,
  stopDeployment,
  listDeployments,
  getPredictionLog,
} from '@/lib/api';
import type { PredictionLogEntry } from '@/lib/types';
import { Button, Card } from '@/components/ui';

// Deploy toggle + endpoint. The prediction Playground and Drift Monitor now live
// in their own tabs; this panel is just the deployment switch + recent activity.

function DeployCard({
  runId,
  isDeployed,
  nPredictions,
  onToggle,
  busy,
}: {
  runId: string;
  isDeployed: boolean;
  nPredictions: number;
  onToggle: () => void;
  busy: boolean;
}) {
  const curl = `curl -X POST ${API_BASE}/api/v1/runs/${runId}/predict \\
  -H "Content-Type: application/json" \\
  -d '{"rows": [{"feature1": "value", ...}]}'`;
  const [copied, setCopied] = useState(false);

  return (
    <Card delay={false} style={{ borderColor: isDeployed ? 'rgba(34,197,94,0.4)' : 'var(--bd)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
          <div aria-hidden="true" style={{ width: 44, height: 44, borderRadius: 12, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 20, background: isDeployed ? 'rgba(34,197,94,0.15)' : 'var(--inp)' }}>{isDeployed ? '🟢' : '⚪'}</div>
          <div>
            <p style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>{isDeployed ? 'Model is live' : 'Model not deployed'}</p>
            <p style={{ fontSize: 12, color: 'var(--t3)', marginTop: 2 }}>{isDeployed ? `Serving predictions · ${nPredictions} served` : 'Deploy to enable the REST endpoint + Playground'}</p>
          </div>
        </div>
        <Button variant={isDeployed ? 'danger' : 'accent'} loading={busy} onClick={onToggle}>
          {isDeployed ? 'Stop' : '🚀 Deploy'}
        </Button>
      </div>
      {isDeployed && (
        <div style={{ marginTop: 16, position: 'relative' }}>
          <pre className="ds-mono" style={{ fontSize: 11, color: '#9fe6c8', background: 'var(--term)', borderRadius: 8, padding: 12, overflowX: 'auto', lineHeight: 1.6 }}>{curl}</pre>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => { navigator.clipboard.writeText(curl); setCopied(true); setTimeout(() => setCopied(false), 1500); }}
            style={{ position: 'absolute', top: 8, right: 8, height: 28, padding: '0 10px', fontSize: 11 }}
          >
            {copied ? '✓ Copied' : 'Copy'}
          </Button>
        </div>
      )}
    </Card>
  );
}

function RecentPredictions({ runId, refreshKey }: { runId: string; refreshKey: number }) {
  const [logs, setLogs] = useState<PredictionLogEntry[]>([]);
  useEffect(() => {
    getPredictionLog(runId, 15).then((d) => setLogs(d.predictions)).catch(() => {});
  }, [runId, refreshKey]);
  if (logs.length === 0) return null;
  return (
    <Card padding={18} delay={false}>
      <div className="ds-mono" style={{ fontSize: 10, color: 'var(--t2)', textTransform: 'uppercase', letterSpacing: '0.5px', marginBottom: 12 }}>Recent Predictions</div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6, maxHeight: 260, overflowY: 'auto' }}>
        {logs.map((p) => (
          <div key={p.id} className="ds-row" style={{ display: 'flex', alignItems: 'center', gap: 12, fontSize: 12, padding: '7px 10px', borderRadius: 8 }}>
            <span style={{ fontWeight: 600, color: 'var(--acc)', minWidth: 80 }}>{p.prediction}</span>
            {p.confidence != null && <span style={{ color: 'var(--t3)' }}>{(p.confidence * 100).toFixed(0)}%</span>}
            <span className="ds-mono" style={{ color: 'var(--t3)', fontSize: 11, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', flex: 1 }}>{JSON.stringify(p.features).slice(0, 80)}…</span>
            <span style={{ color: 'var(--t4)', fontSize: 11, flexShrink: 0 }}>{p.latency_ms?.toFixed(0)}ms</span>
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function DeployPanel({ runId }: { runId: string }) {
  const [isDeployed, setIsDeployed] = useState(false);
  const [nPredictions, setNPredictions] = useState(0);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(() => {
    listDeployments().then((d) => {
      const dep = d.deployments.find((x) => x.run_id === runId);
      setIsDeployed(dep?.status === 'active');
      setNPredictions(dep?.n_predictions ?? 0);
    }).catch(() => {});
  }, [runId]);

  useEffect(() => { refresh(); }, [refresh]);

  const toggle = async () => {
    setBusy(true);
    try {
      if (isDeployed) await stopDeployment(runId);
      else await deployModel(runId);
      refresh();
    } catch { /* surfaced via refresh */ } finally { setBusy(false); }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <DeployCard runId={runId} isDeployed={isDeployed} nPredictions={nPredictions} onToggle={toggle} busy={busy} />
      {isDeployed && <RecentPredictions runId={runId} refreshKey={nPredictions} />}
    </div>
  );
}
