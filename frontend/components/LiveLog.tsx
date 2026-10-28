'use client';
import { useEffect, useRef } from 'react';
import type { ProgressMessage } from '@/lib/types';

// Restrained palette: alternating gold / jade / bone tones — no rainbow.
const AGENT_COLORS: Record<string, string> = {
  data_auditor: 'bg-gold-500/15 text-gold-300',
  problem_framer: 'bg-bone/10 text-bone-dim',
  baseline_builder: 'bg-jade-500/15 text-jade-300',
  eda_agent: 'bg-gold-500/15 text-gold-400',
  preprocessor: 'bg-bone/10 text-bone-dim',
  feature_engineer: 'bg-jade-500/15 text-jade-400',
  model_selector: 'bg-gold-500/15 text-gold-300',
  tuner: 'bg-bone/10 text-bone-dim',
  evaluator: 'bg-jade-500/15 text-jade-300',
  exporter: 'bg-gold-500/15 text-gold-300',
  orchestrator: 'bg-bone/[0.06] text-bone-faint',
};

function agentLabel(agent: string): string {
  return agent.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatTime(iso: string): string {
  try {
    return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  } catch {
    return '';
  }
}

function DataBadge({ data }: { data: Record<string, unknown> }) {
  const entries = Object.entries(data).filter(
    ([, v]) => v !== null && v !== undefined && typeof v !== 'object'
  );
  if (!entries.length) return null;

  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {entries.slice(0, 6).map(([k, v]) => (
        <span
          key={k}
          className="text-xs bg-obsidian-700 text-bone-dim rounded px-2 py-0.5 font-mono"
        >
          {k}: <span className="text-bone-dim">{String(v)}</span>
        </span>
      ))}
    </div>
  );
}

export default function LiveLog({
  messages,
  isConnected,
  status = 'running',
}: {
  messages: ProgressMessage[];
  isConnected: boolean;
  status?: string;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);
  const isRunning = status === 'running' || status === 'queued';

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  // Header state: live only while running; a finished run shows a calm
  // "replayed" state instead of an alarming "Disconnected".
  const dotClass = isRunning
    ? isConnected
      ? 'bg-jade-400 animate-pulse'
      : 'bg-gold-400 animate-pulse'
    : 'bg-obsidian-600';
  const headerText = isRunning
    ? isConnected
      ? 'Live — receiving updates'
      : 'Reconnecting…'
    : status === 'failed'
    ? 'Run ended — replayed log'
    : 'Run complete — replayed log';

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center gap-2 px-4 py-3 border-b border-bone/10 flex-shrink-0">
        <div className={`w-2 h-2 rounded-full ${dotClass}`} />
        <span className="text-xs font-medium text-bone-dim">{headerText}</span>
        <span className="ml-auto text-xs text-bone-faint">
          {messages.length} events
        </span>
      </div>

      {/* Messages — a polite live region so assistive tech announces new events
          as the pipeline streams them (additions only, so it won't re-read history). */}
      <div
        className="flex-1 overflow-y-auto p-4 space-y-3 font-mono text-sm"
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        aria-label="Pipeline live log"
      >
        {messages.length === 0 ? (
          <div className="flex flex-col items-center justify-center h-full min-h-32 text-bone-faint space-y-2 text-center px-6">
            <div className="text-2xl">{isRunning ? '⌛' : '✓'}</div>
            <p className="text-xs">
              {isRunning
                ? 'Waiting for pipeline to start…'
                : 'No live log was retained for this run.'}
            </p>
            {!isRunning && (
              <p className="text-[11px] text-bone-ghost">
                The step-by-step trace is on the <span className="text-gold-400">Pipeline</span> stepper, and every
                decision is in the <span className="text-gold-400">Decision Log</span> below.
              </p>
            )}
          </div>
        ) : (
          messages.map((msg, i) => {
            const colorClass =
              AGENT_COLORS[msg.agent] || 'bg-obsidian-700/50 text-bone-dim';
            return (
              <div key={i} className="group">
                <div className="flex items-start gap-2.5">
                  {/* Agent badge */}
                  <span
                    className={`flex-shrink-0 text-[11px] font-semibold px-1.5 py-0.5 rounded uppercase tracking-wider ${colorClass}`}
                  >
                    {agentLabel(msg.agent)}
                  </span>

                  {/* Message */}
                  <div className="flex-1 min-w-0">
                    <span className="text-bone-dim text-xs leading-relaxed">
                      {msg.message}
                    </span>
                    {msg.data && Object.keys(msg.data).length > 0 && (
                      <DataBadge data={msg.data} />
                    )}
                  </div>

                  {/* Timestamp */}
                  <span className="text-[11px] text-bone-ghost flex-shrink-0 mt-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                    {formatTime(msg.timestamp)}
                  </span>
                </div>
              </div>
            );
          })
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
