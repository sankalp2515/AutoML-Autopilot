'use client';
import { cn } from './cn';

/**
 * Skeleton — content-shaped loading placeholder. Preferred over a spinner for
 * regions whose final layout is known (stat grids, lists, tables): it preserves
 * page height so nothing jumps when data arrives, and it reads as "content is
 * coming" rather than "the page is stuck."
 *
 * Shimmer honors `prefers-reduced-motion` (see globals.css `.ui-skeleton`).
 * Skeletons are decorative; wrap the whole loading region in one live region
 * (e.g. `<StateView loading>`), not each skeleton.
 */
export interface SkeletonProps {
  width?: number | string;
  height?: number | string;
  radius?: number;
  className?: string;
  style?: React.CSSProperties;
}

export function Skeleton({ width = '100%', height = 14, radius = 6, className, style }: SkeletonProps) {
  return (
    <span
      aria-hidden="true"
      className={cn('ui-skeleton', className)}
      style={{ display: 'block', width, height, borderRadius: radius, ...style }}
    />
  );
}

/** Convenience: N stacked text lines with a shortened last line. */
export function SkeletonText({ lines = 3, gap = 8 }: { lines?: number; gap?: number }) {
  return (
    <span aria-hidden="true" style={{ display: 'flex', flexDirection: 'column', gap }}>
      {Array.from({ length: lines }).map((_, i) => (
        <Skeleton key={i} width={i === lines - 1 ? '60%' : '100%'} />
      ))}
    </span>
  );
}
