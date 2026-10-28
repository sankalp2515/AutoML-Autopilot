'use client';
import { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { getRegistry, deployRun, undeployRun, type RegistryFamily } from '@/lib/api';
import { toast } from '@/lib/toast';
import { Badge, Button, Card, SkeletonText } from '@/components/ui';

export default function RegistryPage() {
  const router = useRouter();
  const [families, setFamilies] = useState<RegistryFamily[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState('');

  const load = useCallback(() => {
    setLoading(true);
    getRegistry().then((d) => setFamilies(d.families)).catch(() => {}).finally(() => setLoading(false));
  }, []);
  useEffect(() => { load(); }, [load]);

  const promote = async (runId: string) => {
    setBusy(runId);
    try { await deployRun(runId); toast('Promoted to champion — model deployed', 'success'); load(); }
    catch (e) { toast(e instanceof Error ? e.message : 'Promote failed', 'error'); }
    finally { setBusy(''); }
  };
  const rollback = async (runId: string) => {
    setBusy(runId);
    try { await undeployRun(runId); toast('Champion rolled back (deployment stopped)', 'success'); load(); }
    catch (e) { toast(e instanceof Error ? e.message : 'Rollback failed', 'error'); }
    finally { setBusy(''); }
  };

  return (
    <div style={{ padding: '28px', maxWidth: 1100, margin: '0 auto' }}>
      <div style={{ marginBottom: 8 }}>
        <h1 className="ds-display" style={{ fontSize: 22, fontWeight: 700, color: 'var(--t1)', letterSpacing: '-0.6px' }}>Model Registry</h1>
        <p style={{ fontSize: 13, color: 'var(--t2)' }}>
          Completed runs as versioned models, grouped by dataset. The <b>champion</b> is the currently-deployed
          version; <b>challengers</b> can be promoted. Promotion is gated by honest holdout performance, not CV.
        </p>
      </div>

      {loading ? <SkeletonText lines={6} /> : families.length === 0 ? (
        <Card><div style={{ padding: 32, textAlign: 'center', color: 'var(--t3)', fontSize: 13 }}>No completed models yet.</div></Card>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 18, marginTop: 16 }}>
          {families.map((f) => (
            <Card key={f.signature}>
              <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--bd)', display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 8 }}>
                <div>
                  <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--t1)' }}>{f.dataset}</span>
                  <span style={{ fontSize: 12, color: 'var(--t3)', marginLeft: 10 }}>{f.task_type?.replace(/_/g, ' ')} · {f.primary_metric} · {f.versions.length} version{f.versions.length !== 1 ? 's' : ''}</span>
                </div>
                {f.promotable_challenger && (
                  <Badge tone="warning">Challenger beats champion by {f.promotable_challenger.beats_champion_by} {f.primary_metric}</Badge>
                )}
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12, padding: 14 }}>
                {f.versions.map((v) => {
                  const isChamp = v.status === 'champion';
                  return (
                    <div key={v.run_id} style={{ border: `1.5px solid ${isChamp ? 'var(--acc)' : 'var(--bd)'}`, borderRadius: 10, padding: 14, background: isChamp ? 'var(--accw)' : 'transparent' }}>
                      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                        <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)' }}>{f.dataset.replace('.csv', '')}-v{v.version}</span>
                        <Badge tone={isChamp ? 'success' : 'neutral'}>{isChamp ? 'CHAMPION' : 'challenger'}</Badge>
                      </div>
                      <div style={{ fontSize: 12, color: 'var(--t2)' }}>{v.algorithm ?? '—'}</div>
                      <div className="ds-display" style={{ fontSize: 20, fontWeight: 800, color: 'var(--t1)', margin: '6px 0', fontVariantNumeric: 'tabular-nums' }}>
                        {v.score != null ? v.score.toFixed(4) : '—'} <span style={{ fontSize: 11, color: 'var(--t3)', fontWeight: 500 }}>{v.primary_metric}</span>
                      </div>
                      <div style={{ display: 'flex', gap: 8, marginTop: 10 }}>
                        <Button size="sm" variant="ghost" onClick={() => router.push(`/runs/${v.run_id}`)}>View</Button>
                        {isChamp
                          ? <Button size="sm" variant="ghost" disabled={busy === v.run_id} onClick={() => rollback(v.run_id)}>Rollback</Button>
                          : <Button size="sm" disabled={busy === v.run_id} onClick={() => promote(v.run_id)}>Promote</Button>}
                      </div>
                    </div>
                  );
                })}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
