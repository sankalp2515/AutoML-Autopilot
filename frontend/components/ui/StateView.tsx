'use client';
import { cn } from './cn';
import { Spinner } from './Spinner';
import { Button } from './Button';

/**
 * StateView — the async-UI state machine every data panel needs.
 *
 * The panels in this app each hand-roll the same ladder: "if loading → spinner
 * card", "if no data → emoji + title + copy", then the happy path — and they get
 * the accessibility wrong (no live region, error indistinguishable from empty).
 * StateView collapses that into one declarative, correctly-announced component.
 *
 * Precedence (first match wins): loading → error → empty → children.
 * This ordering is deliberate: while refetching we keep showing the spinner
 * rather than briefly flashing a stale empty/error state.
 *
 *   <StateView loading={loading} error={err} isEmpty={rows.length === 0}
 *              onRetry={load} empty={{ icon: '📉', title: 'No data yet' }}>
 *     {rows.map(...)}
 *   </StateView>
 */

export interface EmptyConfig {
  icon?: React.ReactNode;
  title: React.ReactNode;
  description?: React.ReactNode;
  /** Optional CTA rendered under the copy. */
  action?: React.ReactNode;
}

export interface StateViewProps {
  loading?: boolean;
  /** Any truthy value renders the error state; `Error` messages are surfaced. */
  error?: unknown;
  isEmpty?: boolean;
  onRetry?: () => void;
  /** Custom loading UI (e.g. skeletons). Falls back to a centered spinner card. */
  loadingSkeleton?: React.ReactNode;
  loadingLabel?: string;
  empty?: EmptyConfig | React.ReactNode;
  /** Min height so state swaps don't collapse the layout. Default 220. */
  minHeight?: number;
  className?: string;
  children: React.ReactNode;
}

function Centered({ minHeight, children, live, className }: { minHeight: number; live?: 'status' | 'alert'; children: React.ReactNode; className?: string }) {
  return (
    <div
      className={cn('ds-card ds-fade', className)}
      role={live}
      aria-live={live === 'status' ? 'polite' : live === 'alert' ? 'assertive' : undefined}
      style={{ minHeight, padding: 40, display: 'grid', placeItems: 'center', textAlign: 'center' }}
    >
      {children}
    </div>
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  if (typeof error === 'string') return error;
  return 'Something went wrong while loading this view.';
}

export function StateView({
  loading,
  error,
  isEmpty,
  onRetry,
  loadingSkeleton,
  loadingLabel = 'Loading…',
  empty,
  minHeight = 220,
  className,
  children,
}: StateViewProps) {
  if (loading) {
    if (loadingSkeleton) {
      return (
        <div role="status" aria-live="polite" aria-busy="true" className={className}>
          <span className="sr-only">{loadingLabel}</span>
          {loadingSkeleton}
        </div>
      );
    }
    return (
      <Centered minHeight={minHeight} live="status" className={className}>
        <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'center', gap: 12, color: 'var(--t3)' }}>
          <Spinner size={22} label={null} />
          <span style={{ fontSize: 12.5 }}>{loadingLabel}</span>
        </span>
      </Centered>
    );
  }

  if (error) {
    return (
      <Centered minHeight={minHeight} live="alert" className={className}>
        <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'center', gap: 10, maxWidth: 420 }}>
          <span aria-hidden="true" style={{ fontSize: 24, opacity: 0.7 }}>⚠</span>
          <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>Couldn’t load this view</span>
          <p style={{ fontSize: 12.5, color: 'var(--t2)' }}>{errorMessage(error)}</p>
          {onRetry ? (
            <Button variant="ghost" size="sm" onClick={onRetry} style={{ marginTop: 4 }}>
              Try again
            </Button>
          ) : null}
        </span>
      </Centered>
    );
  }

  if (isEmpty && empty) {
    if (isEmptyConfig(empty)) {
      return (
        <Centered minHeight={minHeight} className={className}>
          <span style={{ display: 'inline-flex', flexDirection: 'column', alignItems: 'center', gap: 8, maxWidth: 420 }}>
            {empty.icon ? <span aria-hidden="true" style={{ fontSize: 26, opacity: 0.6 }}>{empty.icon}</span> : null}
            <span style={{ fontSize: 14, fontWeight: 600, color: 'var(--t1)' }}>{empty.title}</span>
            {empty.description ? <p style={{ fontSize: 12.5, color: 'var(--t2)' }}>{empty.description}</p> : null}
            {empty.action ? <span style={{ marginTop: 6 }}>{empty.action}</span> : null}
          </span>
        </Centered>
      );
    }
    return <>{empty}</>;
  }

  return <>{children}</>;
}

function isEmptyConfig(v: EmptyConfig | React.ReactNode): v is EmptyConfig {
  return typeof v === 'object' && v !== null && 'title' in (v as unknown as Record<string, unknown>);
}
