'use client';
import { Card } from './Card';
import { cn } from './cn';

/**
 * Stat / StatGrid — the KPI-tile pattern used across drift, metrics, compare, etc.
 * Previously copy-pasted as a `grid repeat(4,1fr)` of hand-styled cards.
 *
 * StatGrid is responsive by default: it uses auto-fit so tiles reflow to 2-up /
 * 1-up on narrow viewports instead of overflowing (the old fixed 4-col grid did).
 */
export interface StatProps {
  label: React.ReactNode;
  value: React.ReactNode;
  /** Value color — pass a token/hex for emphasis (e.g. red when a count > 0). */
  color?: string;
  hint?: React.ReactNode;
  delay?: 0 | 1 | 2 | 3 | false;
}

export function Stat({ label, value, color = 'var(--t1)', hint, delay = 0 }: StatProps) {
  return (
    <Card padding={18} delay={delay}>
      <div style={{ fontSize: 11, color: 'var(--t2)', marginBottom: 8 }}>{label}</div>
      <div className="ds-display" style={{ fontSize: 26, fontWeight: 800, color, letterSpacing: '-0.5px', lineHeight: 1.1 }}>
        {value}
      </div>
      {hint ? <div style={{ fontSize: 10.5, color: 'var(--t3)', marginTop: 6 }}>{hint}</div> : null}
    </Card>
  );
}

export interface StatGridProps {
  /** Minimum tile width before wrapping. Default 150px. */
  min?: number;
  gap?: number;
  className?: string;
  style?: React.CSSProperties;
  children: React.ReactNode;
}

export function StatGrid({ min = 150, gap = 14, className, style, children }: StatGridProps) {
  return (
    <div
      className={cn(className)}
      style={{ display: 'grid', gridTemplateColumns: `repeat(auto-fit, minmax(${min}px, 1fr))`, gap, ...style }}
    >
      {children}
    </div>
  );
}
