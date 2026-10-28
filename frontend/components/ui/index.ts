/**
 * UI primitives — the reusable, accessible, theme-aware building blocks.
 *
 * Import from the barrel so panels have one stable entry point:
 *   import { Button, Card, StateView, Stat, StatGrid, Badge } from '@/components/ui';
 *
 * These wrap the existing `ds-*` token CSS (globals.css), so every primitive is
 * dual-theme (dark/light) with zero extra work and no visual regression.
 */
export { cn } from './cn';
export { Spinner } from './Spinner';
export type { SpinnerProps } from './Spinner';
export { Button } from './Button';
export type { ButtonProps, ButtonVariant, ButtonSize } from './Button';
export { Card, CardHeader } from './Card';
export type { CardProps, CardHeaderProps } from './Card';
export { Badge } from './Badge';
export type { BadgeProps, BadgeTone } from './Badge';
export { Skeleton, SkeletonText } from './Skeleton';
export type { SkeletonProps } from './Skeleton';
export { StateView } from './StateView';
export type { StateViewProps, EmptyConfig } from './StateView';
export { Stat, StatGrid } from './Stat';
export type { StatProps, StatGridProps } from './Stat';
