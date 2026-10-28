import type { LLMStat } from '@/lib/types';
import { Badge, Card, Stat, StatGrid } from '@/components/ui';

function agentLabel(name: string) {
  return name.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function CostCell({ cost }: { cost: number }) {
  if (cost === 0) return <Badge tone="success">Free</Badge>;
  return <span className="font-mono text-xs text-bone-dim">${cost.toFixed(5)}</span>;
}

export default function LLMStatsPanel({
  stats,
  totals,
}: {
  stats: LLMStat[];
  totals: LLMStat;
}) {
  // Edge case: a run can finish before any LLM call is recorded (fully-cached /
  // template-only path). Previously this rendered a stat grid of zeros plus a lone
  // "Total" table row — confusing. Show an explicit empty state instead.
  if (!stats.length && !totals.n_calls) {
    return (
      <Card padding={40} style={{ textAlign: 'center' }}>
        <div aria-hidden="true" style={{ fontSize: 24, opacity: 0.6, marginBottom: 8 }}>🪙</div>
        <div style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>No LLM usage recorded</div>
        <p style={{ fontSize: 12.5, color: 'var(--t2)', maxWidth: 360, marginInline: 'auto', marginTop: 4 }}>
          This run completed without any tracked model calls (cached or template-only steps).
        </p>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <StatGrid min={140}>
        <Stat label="Total LLM Calls" value={totals.n_calls} delay={0} />
        <Stat label="Total Tokens" value={totals.total_tokens.toLocaleString()} delay={1} />
        <Stat label="Avg Latency" value={`${totals.avg_latency_ms?.toFixed(0) ?? '—'} ms`} delay={2} />
        <Stat
          label="Estimated Cost"
          value={totals.total_cost_usd === 0 ? '$0.00' : `$${totals.total_cost_usd.toFixed(4)}`}
          color={totals.total_cost_usd === 0 ? '#16a34a' : 'var(--t1)'}
          delay={3}
        />
      </StatGrid>

      {/* Per-agent table — horizontally scrollable on narrow viewports. */}
      <Card padding={0} delay={1}>
        <div className="ds-scroll-x">
          <table className="w-full text-sm ds-table-min">
            <caption className="sr-only">Per-agent LLM usage and cost breakdown</caption>
            <thead>
              <tr className="border-b border-bone/10">
                {['Agent', 'Calls', 'Prompt Tokens', 'Completion Tokens', 'Total', 'Avg Latency', 'Cost'].map((h) => (
                  <th key={h} scope="col" className="text-left py-2 px-3 text-xs font-semibold text-bone-faint uppercase tracking-wider">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {stats.map((s) => (
                <tr key={s.agent_name} className="border-b border-bone/5 hover:bg-obsidian-700/20 transition-colors">
                  <td className="py-2.5 px-3 text-xs text-bone-dim">{agentLabel(s.agent_name)}</td>
                  <td className="py-2.5 px-3 text-xs text-bone-dim tabular-nums">{s.n_calls}</td>
                  <td className="py-2.5 px-3 text-xs text-bone-dim tabular-nums">{s.total_prompt_tokens.toLocaleString()}</td>
                  <td className="py-2.5 px-3 text-xs text-bone-dim tabular-nums">{s.total_completion_tokens.toLocaleString()}</td>
                  <td className="py-2.5 px-3 text-xs text-bone-dim font-medium tabular-nums">{s.total_tokens.toLocaleString()}</td>
                  <td className="py-2.5 px-3 text-xs text-bone-dim tabular-nums">{s.avg_latency_ms?.toFixed(0) ?? '—'} ms</td>
                  <td className="py-2.5 px-3"><CostCell cost={s.total_cost_usd} /></td>
                </tr>
              ))}

              <tr className="border-t-2 border-bone/15 bg-obsidian-700/30">
                <td className="py-2.5 px-3 text-xs font-semibold text-bone">Total</td>
                <td className="py-2.5 px-3 text-xs font-semibold text-bone-dim tabular-nums">{totals.n_calls}</td>
                <td className="py-2.5 px-3 text-xs font-semibold text-bone-dim tabular-nums">{totals.total_prompt_tokens.toLocaleString()}</td>
                <td className="py-2.5 px-3 text-xs font-semibold text-bone-dim tabular-nums">{totals.total_completion_tokens.toLocaleString()}</td>
                <td className="py-2.5 px-3 text-xs font-semibold text-bone tabular-nums">{totals.total_tokens.toLocaleString()}</td>
                <td className="py-2.5 px-3 text-xs font-semibold text-bone-dim tabular-nums">—</td>
                <td className="py-2.5 px-3"><CostCell cost={totals.total_cost_usd} /></td>
              </tr>
            </tbody>
          </table>
        </div>
      </Card>

      <p className="text-xs text-bone-faint">
        Cost is estimated based on public pricing. Groq free-tier calls are shown as $0.00.
      </p>
    </div>
  );
}
