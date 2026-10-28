'use client';
import { forwardRef } from 'react';
import { cn } from './cn';

/**
 * Card — the surface primitive. Wraps `ds-card` and standardizes padding + the
 * staggered entrance animation (`ds-fade-*`) that panels were applying by hand.
 *
 * `elevated` uses the `ds-card-2` recessed surface; `hover` opts into the lift.
 * `delay` (0–3) maps to the `ds-fade-{n}` stagger classes already in globals.css.
 */
export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  elevated?: boolean;
  hover?: boolean;
  /** Inner padding in px. Default 20. Pass 0 for flush content (e.g. tables). */
  padding?: number;
  /** Entrance-stagger index 0–3 → ds-fade / ds-fade-1..3. Pass `false` to disable. */
  delay?: 0 | 1 | 2 | 3 | false;
}

export const Card = forwardRef<HTMLDivElement, CardProps>(function Card(
  { elevated, hover, padding = 20, delay = 0, className, style, children, ...rest },
  ref,
) {
  const fade = delay === false ? undefined : delay === 0 ? 'ds-fade' : `ds-fade-${delay}`;
  return (
    <div
      ref={ref}
      className={cn(elevated ? 'ds-card-2' : 'ds-card', hover && 'ds-card-hover', fade, className)}
      style={{ padding, ...style }}
      {...rest}
    >
      {children}
    </div>
  );
});

/** Optional header row: title + subtitle on the left, actions on the right. */
export interface CardHeaderProps {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
}

export function CardHeader({ title, subtitle, actions, className }: CardHeaderProps) {
  return (
    <div
      className={cn(className)}
      style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 14, marginBottom: subtitle ? 14 : 12 }}
    >
      <div style={{ minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--t1)' }}>{title}</div>
        {subtitle ? <p style={{ fontSize: 11.5, color: 'var(--t3)', marginTop: 3 }}>{subtitle}</p> : null}
      </div>
      {actions ? <div style={{ flexShrink: 0, display: 'flex', gap: 8 }}>{actions}</div> : null}
    </div>
  );
}
