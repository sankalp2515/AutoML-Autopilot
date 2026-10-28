import type { AgentStep } from '@/lib/types';

// Two pipelines share this stepper. The tabular graph runs 10 agents; the
// time-series graph runs its own 4 + the shared exporter. We pick the running
// order from which agents actually appear, so a TS run no longer looks like it
// "skipped" everything.
const TABULAR_ORDER = [
  'data_auditor',
  'problem_framer',
  'baseline_builder',
  'eda_agent',
  'preprocessor',
  'feature_engineer',
  'model_selector',
  'tuner',
  'evaluator',
  'exporter',
];

const TS_ORDER = ['ts_framer', 'ts_auditor', 'ts_feature_builder', 'ts_modeler', 'exporter'];

const AGENT_LABELS: Record<string, string> = {
  data_auditor: 'Data Auditor',
  problem_framer: 'Problem Framer',
  baseline_builder: 'Baseline Builder',
  eda_agent: 'EDA + Error Analysis',
  preprocessor: 'Preprocessor',
  feature_engineer: 'Feature Engineer',
  model_selector: 'Model Selector',
  tuner: 'Hyperparameter Tuner',
  evaluator: 'Evaluator',
  exporter: 'Exporter',
  ts_framer: 'TS Problem Framer',
  ts_auditor: 'TS Series Auditor',
  ts_feature_builder: 'TS Feature Builder',
  ts_modeler: 'TS Modeler',
};

const AGENT_ICONS: Record<string, string> = {
  data_auditor: '🔍',
  problem_framer: '🎯',
  baseline_builder: '📐',
  eda_agent: '📊',
  preprocessor: '⚙️',
  feature_engineer: '🔧',
  model_selector: '🤖',
  tuner: '🎛️',
  evaluator: '📈',
  exporter: '📦',
  ts_framer: '🎯',
  ts_auditor: '🔍',
  ts_feature_builder: '🔧',
  ts_modeler: '📈',
};

function resolveOrder(steps: AgentStep[], activeAgent?: string): string[] {
  const present = Array.from(
    new Set([...steps.map((s) => s.agent_name), ...(activeAgent ? [activeAgent] : [])])
  );
  const isTS = present.some((a) => a.startsWith('ts_'));
  const base = isTS ? TS_ORDER : TABULAR_ORDER;
  const extras = present.filter((a) => !base.includes(a));
  return [...base, ...extras];
}

function durationStr(step: AgentStep): string {
  if (!step.started_at) return '';
  const end = step.completed_at ? new Date(step.completed_at) : new Date();
  const s = (end.getTime() - new Date(step.started_at).getTime()) / 1000;
  if (s < 60) return `${s.toFixed(0)}s`;
  return `${(s / 60).toFixed(1)}m`;
}

export default function AgentTimeline({
  steps,
  activeAgent,
}: {
  steps: AgentStep[];
  activeAgent?: string;
}) {
  const stepMap: Record<string, AgentStep> = {};
  for (const s of steps) {
    stepMap[s.agent_name] = s;
  }
  const order = resolveOrder(steps, activeAgent);

  return (
    <div className="space-y-0.5" role="list" aria-label="Pipeline agent progress">
      {order.map((agent, idx) => {
        const step = stepMap[agent];
        const isActive = activeAgent === agent || step?.status === 'running';
        const isDone = step?.status === 'completed';
        const isFailed = step?.status === 'failed';
        const isPending = !step;
        const statusWord = isActive ? 'in progress' : isFailed ? 'failed' : isDone ? 'completed' : 'pending';

        return (
          <div
            key={agent}
            role="listitem"
            aria-label={`${AGENT_LABELS[agent] ?? agent} — ${statusWord}`}
            aria-current={isActive ? 'step' : undefined}
            className={`flex items-start gap-3 px-3 py-2.5 rounded-lg transition-colors ${
              isActive ? 'bg-gold-500/10' : isDone ? '' : ''
            }`}
          >
            {/* Connector line */}
            <div className="flex flex-col items-center gap-0 mt-0.5 flex-shrink-0">
              {/* Status dot */}
              <div
                className={`w-5 h-5 rounded-full flex items-center justify-center flex-shrink-0 ${
                  isActive
                    ? 'bg-gold-500/20'
                    : isDone
                    ? 'bg-jade-500/20'
                    : isFailed
                    ? 'bg-terra-500/20'
                    : 'bg-obsidian-700'
                }`}
              >
                {isActive && (
                  <div className="w-2.5 h-2.5 rounded-full bg-gold-400 animate-pulse" />
                )}
                {isDone && (
                  <svg className="w-3 h-3 text-jade-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                  </svg>
                )}
                {isFailed && (
                  <svg className="w-3 h-3 text-terra-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={3}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                )}
                {isPending && (
                  <div className="w-2 h-2 rounded-full bg-obsidian-600" />
                )}
              </div>
              {/* Vertical line */}
              {idx < order.length - 1 && (
                <div
                  className={`w-px h-4 mt-0.5 ${
                    isDone ? 'bg-jade-600' : 'bg-obsidian-700'
                  }`}
                />
              )}
            </div>

            {/* Content */}
            <div className="flex-1 min-w-0">
              <div className="flex items-center justify-between gap-2">
                <span
                  className={`text-xs font-medium truncate ${
                    isActive
                      ? 'text-gold-300'
                      : isDone
                      ? 'text-bone-dim'
                      : isFailed
                      ? 'text-terra-400'
                      : 'text-bone-faint'
                  }`}
                >
                  <span className="mr-1" aria-hidden="true">{AGENT_ICONS[agent]}</span>
                  {AGENT_LABELS[agent]}
                </span>
                {step && (
                  <span className="text-xs text-bone-faint flex-shrink-0">
                    {durationStr(step)}
                  </span>
                )}
              </div>
              {isFailed && step?.error_message && (
                <p className="text-xs text-terra-500 mt-0.5 truncate">
                  {step.error_message}
                </p>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
