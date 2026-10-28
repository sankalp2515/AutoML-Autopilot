'use client';
import { forwardRef } from 'react';
import { cn } from './cn';
import { Spinner } from './Spinner';

/**
 * Button — the single button primitive for the app.
 *
 * Replaces the repeated pattern of `<button className="ds-btn-acc" style={{height,
 * padding, fontSize}}>` scattered across panels, and fixes two real production bugs
 * those hand-rolled buttons share:
 *   1. Loading was shown by swapping the label to "…" — invisible to screen readers
 *      and it made the button jump width. Here `loading` keeps the label, overlays a
 *      spinner, sets `aria-busy`, and disables interaction without a layout shift.
 *   2. Sizing was re-specified inline everywhere. Here it's a `size` token.
 *
 * Built on the existing `ds-btn-acc` / `ds-ghost` CSS so theming + dual-mode are free.
 */
export type ButtonVariant = 'accent' | 'ghost' | 'danger';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner, sets aria-busy, and blocks clicks — without a width jump. */
  loading?: boolean;
  /** Leading icon/element (hidden while loading so the spinner takes its place). */
  icon?: React.ReactNode;
  /** Stretch to the full width of the container. */
  fullWidth?: boolean;
}

const SIZES: Record<ButtonSize, React.CSSProperties> = {
  sm: { height: 32, padding: '0 14px', fontSize: 12 },
  md: { height: 38, padding: '0 18px', fontSize: 13 },
  lg: { height: 46, padding: '0 24px', fontSize: 14 },
};

const DANGER: React.CSSProperties = {
  color: '#ef4444',
  borderColor: 'rgba(239,68,68,0.3)',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'accent', size = 'md', loading = false, icon, fullWidth, disabled, children, className, style, type, ...rest },
  ref,
) {
  const base = variant === 'accent' ? 'ds-btn-acc' : 'ds-ghost';
  const isDisabled = disabled || loading;

  return (
    <button
      ref={ref}
      // Default to type="button": these buttons live inside forms/panels and an
      // accidental implicit submit is a classic production footgun.
      type={type ?? 'button'}
      className={cn(base, className)}
      disabled={isDisabled}
      aria-busy={loading || undefined}
      style={{
        position: 'relative',
        width: fullWidth ? '100%' : undefined,
        ...SIZES[size],
        ...(variant === 'danger' ? DANGER : null),
        ...style,
      }}
      {...rest}
    >
      {loading && (
        <span style={{ position: 'absolute', inset: 0, display: 'grid', placeItems: 'center' }}>
          <Spinner size={size === 'lg' ? 18 : 15} label={null} />
        </span>
      )}
      <span
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 7,
          // Keep the label in the DOM (stable width) but invisible while loading.
          visibility: loading ? 'hidden' : 'visible',
        }}
      >
        {icon ? <span aria-hidden="true" style={{ display: 'inline-flex' }}>{icon}</span> : null}
        {children}
      </span>
    </button>
  );
});
