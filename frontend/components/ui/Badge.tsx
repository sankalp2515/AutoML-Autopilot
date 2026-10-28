'use client';
import { cn } from './cn';

/**
 * Badge — semantic status chip. Replaces the repeated inline
 * `<span className="ds-chip" style={{ background, color }}>` pattern and the
 * ad-hoc PSI/severity color logic copy-pasted between panels.
 *
 * `tone` maps to the design tokens so dark/light both stay legible. Use `Badge`
 * for meaning (success/warn/danger/…), not for arbitrary brand colors.
 */
export type BadgeTone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger';

const TONES: Record<BadgeTone, { bg: string; c: string }> = {
  neutral: { bg: 'var(--sur)', c: 'var(--t2)' },
  accent: { bg: 'var(--accw)', c: 'var(--acc)' },
  success: { bg: 'rgba(34,197,94,0.12)', c: '#16a34a' },
  warning: { bg: 'rgba(217,119,6,0.12)', c: '#d97706' },
  danger: { bg: 'rgba(239,68,68,0.12)', c: '#ef4444' },
};

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  tone?: BadgeTone;
  /** Renders a small leading status dot in the tone color. */
  dot?: boolean;
}

export function Badge({ tone = 'neutral', dot, children, className, style, ...rest }: BadgeProps) {
  const t = TONES[tone];
  return (
    <span className={cn('ds-chip', className)} style={{ background: t.bg, color: t.c, ...style }} {...rest}>
      {dot ? <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: '50%', background: t.c }} /> : null}
      {children}
    </span>
  );
}
