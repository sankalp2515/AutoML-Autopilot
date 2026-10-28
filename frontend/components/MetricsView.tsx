'use client';
import type { Results, EvalCurves } from '@/lib/types';

// ── tiny SVG chart helpers (theme-aware via currentColor / CSS vars) ──────────
function LineChart({ xs, ys, color, xLabel, yLabel, diagonal }: { xs: number[]; ys: number[]; color: string; xLabel: string; yLabel: string; diagonal?: boolean }) {
  const W = 220, H = 180, pad = 22;
  const sx = (x: number) => pad + x * (W - 2 * pad);
  const sy = (y: number) => H - pad - y * (H - 2 * pad);
  const pts = xs.map((x, i) => `${sx(x).toFixed(1)},${sy(ys[i]).toFixed(1)}`).join(' ');
  const fill = `${sx(xs[xs.length - 1] ?? 0)},${sy(0)} ${pts} ${sx(xs[0] ?? 0)},${sy(0)}`;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: 'block', overflow: 'visible' }}>
      {diagonal && <line x1={sx(0)} y1={sy(0)} x2={sx(1)} y2={sy(1)} stroke="var(--bd)" strokeWidth="1.5" strokeDasharray="5,4" />}
      <polygon points={fill} fill={color} opacity="0.12" />
      <polyline points={pts} fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} stroke="var(--bd)" strokeWidth="1" />
      <line x1={pad} y1={pad} x2={pad} y2={H - pad} stroke="var(--bd)" strokeWidth="1" />
      <text x={W / 2} y={H - 4} fontSize="9" fill="var(--t3)" textAnchor="middle">{xLabel}</text>
      <text x={8} y={H / 2} fontSize="9" fill="var(--t3)" textAnchor="middle" transform={`rotate(-90,8,${H / 2})`}>{yLabel}</text>
    </svg>
  );
}

function Scatter({ xs, ys, color, xLabel, yLabel, diagonal, zeroLine }: { xs: number[]; ys: number[]; color: string; xLabel: string; yLabel: string; diagonal?: boolean; zeroLine?: boolean }) {
  const W = 220, H = 180, pad = 24;
  const xmin = Math.min(...xs), xmax = Math.max(...xs), ymin = Math.min(...ys), ymax = Math.max(...ys);
  const nx = (x: number) => (xmax === xmin ? 0.5 : (x - xmin) / (xmax - xmin));
  const ny = (y: number) => (ymax === ymin ? 0.5 : (y - ymin) / (ymax - ymin));
  const sx = (x: number) => pad + nx(x) * (W - 2 * pad);
  const sy = (y: number) => H - pad - ny(y) * (H - 2 * pad);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: 'block', overflow: 'visible' }}>
      {diagonal && <line x1={sx(xmin)} y1={sy(xmin)} x2={sx(xmax)} y2={sy(xmax)} stroke="var(--bd)" strokeWidth="1.5" strokeDasharray="5,4" />}
      {zeroLine && <line x1={pad} y1={sy(0)} x2={W - pad} y2={sy(0)} stroke="var(--bd)" strokeWidth="1.5" strokeDasharray="5,4" />}
      {xs.map((x, i) => <circle key={i} cx={sx(x)} cy={sy(ys[i])} r="2.2" fill={color} opacity="0.5" />)}
      <text x={W / 2} y={H - 4} fontSize="9" fill="var(--t3)" textAnchor="middle">{xLabel}</text>
      <text x={8} y={H / 2} fontSize="9" fill="var(--t3)" textAnchor="middle" transform={`rotate(-90,8,${H / 2})`}>{yLabel}</text>
    </svg>
  );
}

// DL-2b — probabilistic forecast band (P10/50/90 fan over the hold-out).
function QuantileBand({ band }: { band: { p10: number[]; p50: number[]; p90: number[]; empirical_coverage_p10_p90?: number; actual?: number[] } }) {
  const W = 460, H = 200, pad = 28;
  const p10 = band.p10 ?? [], p50 = band.p50 ?? [], p90 = band.p90 ?? [], actual = band.actual ?? [];
  const n = p50.length;
  if (n < 2) return null;
  const all = [...p10, ...p90, ...actual];
  const ymin = Math.min(...all), ymax = Math.max(...all);
  const sx = (i: number) => pad + (i / (n - 1)) * (W - 2 * pad);
  const sy = (y: number) => H - pad - (ymax === ymin ? 0.5 : (y - ymin) / (ymax - ymin)) * (H - 2 * pad);
  const line = (ys: number[]) => ys.map((y, i) => `${sx(i).toFixed(1)},${sy(y).toFixed(1)}`).join(' ');
  const bandPoly = `${p90.map((y, i) => `${sx(i).toFixed(1)},${sy(y).toFixed(1)}`).join(' ')} ${[...p10].reverse().map((y, i) => `${sx(n - 1 - i).toFixed(1)},${sy(y).toFixed(1)}`).join(' ')}`;
  const cov = band.empirical_coverage_p10_p90;
  return (
    <div className="ds-card ds-fade-1" style={{ padding: 20, marginBottom: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)' }}>Probabilistic forecast band (P10–P90)</div>
        {cov != null && <span className="ds-chip" style={{ background: 'var(--accw)', color: 'var(--acc)' }}>Coverage {(cov * 100).toFixed(0)}% (target 80%)</span>}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: 'block', overflow: 'visible' }}>
        <polygon points={bandPoly} fill="#0B6E74" opacity="0.14" />
        <polyline points={line(p50)} fill="none" stroke="#0B6E74" strokeWidth="2" />
        {actual.length === n && <polyline points={line(actual)} fill="none" stroke="#d97706" strokeWidth="1.5" strokeDasharray="4,3" />}
        <line x1={pad} y1={H - pad} x2={W - pad} y2={H - pad} stroke="var(--bd)" strokeWidth="1" />
        <text x={W / 2} y={H - 6} fontSize="9" fill="var(--t3)" textAnchor="middle">Hold-out step</text>
      </svg>
      <div style={{ fontSize: 11.5, color: 'var(--t3)', marginTop: 6 }}>
        Teal = median forecast, shaded = 10th–90th percentile uncertainty{actual.length === n ? ', dashed amber = actual' : ''}. Empirical coverage near 80% means the band is well-calibrated.
      </div>
    </div>
  );
}

function Curves({ curves }: { curves: EvalCurves }) {
  const cards: React.ReactNode[] = [];
  if (curves.roc) cards.push(
    <div key="roc" className="ds-card ds-fade-1" style={{ padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)' }}>ROC Curve</div>
        {curves.roc.auc != null && <span className="ds-chip" style={{ background: 'var(--accw)', color: 'var(--acc)' }}>AUC {curves.roc.auc.toFixed(3)}</span>}
      </div>
      <LineChart xs={curves.roc.fpr} ys={curves.roc.tpr} color="#0B6E74" xLabel="False Positive Rate" yLabel="TPR" diagonal />
    </div>
  );
  if (curves.pr) cards.push(
    <div key="pr" className="ds-card ds-fade-2" style={{ padding: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)' }}>Precision-Recall</div>
        {curves.pr.ap != null && <span className="ds-chip" style={{ background: 'rgba(34,197,94,0.12)', color: '#16a34a' }}>AP {curves.pr.ap.toFixed(3)}</span>}
      </div>
      <LineChart xs={curves.pr.recall} ys={curves.pr.precision} color="#22c55e" xLabel="Recall" yLabel="Precision" />
    </div>
  );
  if (curves.regression) cards.push(
    <div key="pva" className="ds-card ds-fade-1" style={{ padding: 20 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)', marginBottom: 10 }}>Predicted vs Actual</div>
      <Scatter xs={curves.regression.y_true} ys={curves.regression.y_pred} color="#0B6E74" xLabel="Actual" yLabel="Predicted" diagonal />
    </div>,
    <div key="res" className="ds-card ds-fade-2" style={{ padding: 20 }}>
      <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)', marginBottom: 10 }}>Residuals</div>
      <Scatter xs={curves.regression.y_pred} ys={curves.regression.residuals} color="#d97706" xLabel="Predicted" yLabel="Residual" zeroLine />
    </div>
  );
  if (cards.length === 0) return null;
  return <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(300px, 1fr))', gap: 20 }}>{cards}</div>;
}

// Which metrics are "headline" for each task family. We render whatever the
// evaluator actually produced — this just decides ordering/prominence, so the
// view adapts to classification / regression / time-series / DL automatically.
const HEADLINE: Record<string, string[]> = {
  classification: ['roc_auc', 'auc', 'pr_auc', 'average_precision', 'f1', 'f1_score', 'f1_macro', 'precision', 'recall', 'accuracy', 'balanced_accuracy', 'log_loss'],
  regression: ['r2', 'r2_score', 'rmse', 'mae', 'mape', 'smape', 'mse', 'medae'],
  timeseries: ['rmse', 'mae', 'mape', 'smape', 'sharpe', 'sortino', 'max_drawdown', 'hit_rate', 'total_return', 'turnover'],
};

function family(taskType: string | null): keyof typeof HEADLINE | 'other' {
  const t = (taskType ?? '').toLowerCase();
  if (t.includes('regress')) return 'regression';
  if (t.includes('series') || t.includes('forecast') || t.includes('quant')) return 'timeseries';
  if (t.includes('class') || t.includes('label')) return 'classification';
  return 'other';
}

function fmt(v: number): string {
  if (!isFinite(v)) return '—';
  const a = Math.abs(v);
  if (a !== 0 && a < 1) return v.toFixed(4);
  if (a < 1000) return v.toFixed(3);
  return v.toLocaleString(undefined, { maximumFractionDigits: 1 });
}

function pretty(k: string): string {
  return k.replace(/_/g, ' ').replace(/\b(roc|auc|pr|rmse|mae|mape|r2|f1)\b/gi, (m) => m.toUpperCase());
}

const LOWER_BETTER = /log.?loss|rmse|\bmae\b|mape|smape|\bmse\b|error|drawdown|hamming/i;
function isBounded01(k: string, v: number): boolean {
  return v >= 0 && v <= 1 && !LOWER_BETTER.test(k);
}

export default function MetricsView({ results }: { results: Results }) {
  const metrics = results.evaluation_report?.metrics ?? {};
  const keys = Object.keys(metrics);
  const fam = family(results.task_type);
  const order = fam === 'other' ? [] : HEADLINE[fam];
  const headlineKeys = order.filter((k) => k in metrics).slice(0, 4);
  const restKeys = keys.filter((k) => !headlineKeys.includes(k));
  const feats = results.shap_top_features ?? [];
  const cal = results.evaluation_report?.calibration ?? null;
  const curves = results.evaluation_report?.curves ?? null;
  const band = (results.evaluation_report as { quantile_band?: { p10: number[]; p50: number[]; p90: number[]; empirical_coverage_p10_p90?: number; actual?: number[] } })?.quantile_band ?? null;

  const baseline = results.baseline_score;
  const final = results.final_score;
  // Directional lift: positive ALWAYS means better. Lower-is-better metrics
  // (rmse/mae/log_loss/mape/error) invert, so an rmse improvement isn't shown
  // red/negative to the customer. Mirrors the backend metric-direction rule.
  const LOWER_BETTER = /rmse|mae|mape|smape|\bmse\b|log.?loss|error|hamming|drawdown/i;
  const primaryMetric = (results.primary_metric ?? '') as string;
  const lowerBetter = LOWER_BETTER.test(primaryMetric);
  const lift = baseline != null && final != null
    ? (lowerBetter ? baseline - final : final - baseline)
    : null;

  const familyLabel = { classification: 'Classification', regression: 'Regression', timeseries: 'Time-Series / Quant', other: 'Model' }[fam];

  // Confusion matrix if the evaluator emitted tn/fp/fn/tp.
  const cm = ['tn', 'fp', 'fn', 'tp'].every((k) => k in metrics)
    ? { tn: metrics.tn, fp: metrics.fp, fn: metrics.fn, tp: metrics.tp }
    : null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span className="ds-chip" style={{ background: 'var(--accw)', color: 'var(--acc)' }}>{familyLabel} metrics</span>
        {results.winner_model && <span style={{ fontSize: 12, color: 'var(--t3)' }}>winner: <b style={{ color: 'var(--t1)' }}>{results.winner_model}</b></span>}
        <span style={{ fontSize: 11, color: 'var(--t4)' }}>· computed on the held-out test split</span>
      </div>

      {/* Headline cards */}
      {headlineKeys.length > 0 && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: 14 }}>
          {headlineKeys.map((k, i) => {
            const v = metrics[k];
            const lower = LOWER_BETTER.test(k);
            const bounded = isBounded01(k, v);
            return (
              <div key={k} className={`ds-card ds-card-hover ds-fade-${Math.min(i, 3)}`} style={{ padding: 18 }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8, marginBottom: 6 }}>
                  <span className="ds-mono" style={{ fontSize: 10, color: 'var(--t2)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>{pretty(k)}</span>
                  <span className="ds-chip" style={{ background: lower ? 'rgba(217,119,6,0.12)' : 'rgba(34,197,94,0.12)', color: lower ? '#d97706' : '#16a34a', fontSize: 9 }}>{lower ? '↓ lower' : '↑ higher'}</span>
                </div>
                <div className="ds-display" style={{ fontSize: 26, fontWeight: 800, color: 'var(--t1)', letterSpacing: '-0.8px' }}>{bounded ? `${(v * 100).toFixed(1)}%` : fmt(v)}</div>
                {bounded && (
                  <div style={{ height: 6, background: 'var(--inp)', borderRadius: 100, overflow: 'hidden', marginTop: 10 }}>
                    <div className="ds-bar-fill" style={{ height: 6, width: `${v * 100}%`, background: 'var(--acc)', borderRadius: 100 }} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}

      {/* Baseline → final lift */}
      {lift != null && (
        <div className="ds-card ds-fade" style={{ padding: 18 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
            <span className="ds-mono" style={{ fontSize: 10, color: 'var(--t2)', textTransform: 'uppercase', letterSpacing: '0.5px' }}>Baseline → Final ({results.primary_metric ?? 'score'})</span>
            <span style={{ fontSize: 12, fontWeight: 700, color: lift >= 0 ? '#16a34a' : '#ef4444' }}>{lift >= 0 ? '+' : ''}{lift.toFixed(4)}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 13 }}>
            <span style={{ color: 'var(--t3)', fontVariantNumeric: 'tabular-nums' }}>{baseline!.toFixed(4)}</span>
            <div style={{ flex: 1, height: 8, background: 'var(--inp)', borderRadius: 100, overflow: 'hidden' }}>
              <div className="ds-bar-fill" style={{ height: 8, width: `${Math.min(100, Math.abs(final!) * 100)}%`, background: 'var(--acc)', borderRadius: 100 }} />
            </div>
            <span style={{ color: 'var(--t1)', fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>{final!.toFixed(4)}</span>
          </div>
        </div>
      )}

      {band && <QuantileBand band={band} />}

      {curves && <Curves curves={curves} />}

      <div style={{ display: 'grid', gridTemplateColumns: cm || feats.length ? 'repeat(auto-fit, minmax(300px, 1fr))' : '1fr', gap: 20 }}>
        {/* All metrics */}
        {restKeys.length > 0 && (
          <div className="ds-card ds-fade-1" style={{ padding: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)', marginBottom: 14 }}>All metrics</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
              {restKeys.map((k) => (
                <div key={k} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 10, borderBottom: '1px solid var(--bd2)', paddingBottom: 7 }}>
                  <span className="ds-mono" style={{ fontSize: 12, color: 'var(--t2)' }}>{pretty(k)}</span>
                  <span style={{ fontSize: 13, fontWeight: 600, color: 'var(--t1)', fontVariantNumeric: 'tabular-nums' }}>{fmt(metrics[k])}</span>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Confusion matrix (classification) or feature importance */}
        {cm ? (
          <div className="ds-card ds-fade-2" style={{ padding: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)', marginBottom: 14 }}>Confusion Matrix</div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
              {([['TN', cm.tn, '#16a34a'], ['FP', cm.fp, '#ef4444'], ['FN', cm.fn, '#ef4444'], ['TP', cm.tp, '#16a34a']] as const).map(([l, v, c], i) => (
                <div key={i} style={{ height: 78, borderRadius: 8, background: `${c}1f`, border: `1px solid ${c}33`, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
                  <span style={{ fontSize: 20, fontWeight: 800, color: c }}>{Math.round(v).toLocaleString()}</span>
                  <span style={{ fontSize: 9, color: c, opacity: 0.8, fontWeight: 600 }}>{l}</span>
                </div>
              ))}
            </div>
          </div>
        ) : feats.length > 0 ? (
          <div className="ds-card ds-fade-2" style={{ padding: 20 }}>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)', marginBottom: 14 }}>Top Features (SHAP)</div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
              {feats.slice(0, 8).map((f, i) => {
                const w = ((feats.length - i) / feats.length) * 100;
                return (
                  <div key={f}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 3 }}>
                      <span className="ds-mono" style={{ fontSize: 11, color: 'var(--t2)' }}>{f}</span>
                      <span style={{ fontSize: 11, color: 'var(--t4)' }}>#{i + 1}</span>
                    </div>
                    <div style={{ height: 6, background: 'var(--inp)', borderRadius: 3, overflow: 'hidden' }}>
                      <div className="ds-bar-fill" style={{ height: 6, width: `${w}%`, background: 'var(--acc)', borderRadius: 3 }} />
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        ) : null}
      </div>

      {/* Calibration */}
      {cal && (
        <div className="ds-card ds-fade-3" style={{ padding: 18, display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)' }}>Calibration</div>
            <div style={{ fontSize: 12, color: 'var(--t3)' }}>Mean calibration error {cal.mean_calibration_error?.toFixed(4)}</div>
          </div>
          <span className="ds-chip" style={{ background: cal.well_calibrated ? 'rgba(34,197,94,0.12)' : 'rgba(217,119,6,0.12)', color: cal.well_calibrated ? '#16a34a' : '#d97706' }}>{cal.well_calibrated ? 'Well calibrated' : 'Needs calibration'}</span>
        </div>
      )}

      {keys.length === 0 && <div className="ds-card" style={{ padding: 32, textAlign: 'center', color: 'var(--t4)', fontSize: 13 }}>No evaluation metrics were recorded for this run.</div>}
    </div>
  );
}
