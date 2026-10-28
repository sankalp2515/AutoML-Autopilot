'use client';
import { cn } from './cn';

/**
 * Spinner — accessible, theme-aware loading indicator.
 *
 * - Uses `currentColor` so it inherits the color of whatever it sits inside
 *   (a button, a muted state view, an accent banner) — no hardcoded palette.
 * - Announces itself to screen readers via a visually-hidden label unless the
 *   parent already owns the live region (`aria-hidden` + `label={null}`).
 * - Honors `prefers-reduced-motion`: the animation is disabled in CSS below.
 */
export interface SpinnerProps {
  /** Diameter in px. Default 16 (inline with body text / buttons). */
  size?: number;
  /** Stroke width in px. Default scales with size. */
  thickness?: number;
  /** SR-only label. Pass `null` when a parent live region already narrates. */
  label?: string | null;
  className?: string;
  style?: React.CSSProperties;
}

export function Spinner({ size = 16, thickness, label = 'Loading', className, style }: SpinnerProps) {
  const sw = thickness ?? Math.max(1.5, size / 8);
  return (
    <span
      className={cn('ui-spinner', className)}
      role={label ? 'status' : undefined}
      aria-hidden={label ? undefined : true}
      style={{ display: 'inline-flex', width: size, height: size, ...style }}
    >
      <svg width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <circle cx="12" cy="12" r={10} stroke="currentColor" strokeOpacity="0.22" strokeWidth={sw} />
        <path
          d="M22 12a10 10 0 0 0-10-10"
          stroke="currentColor"
          strokeWidth={sw}
          strokeLinecap="round"
        />
      </svg>
      {label ? <span className="sr-only">{label}</span> : null}
    </span>
  );
}
