/**
 * cn — minimal, dependency-free className joiner.
 *
 * We deliberately avoid `clsx`/`tailwind-merge`: this codebase mixes the `ds-*`
 * CSS classes with inline styles, so there is no Tailwind class-conflict to
 * resolve — a falsy-filtering join is all we need and it ships zero bytes of dep.
 *
 *   cn('ds-card', isActive && 'active', undefined)  // → "ds-card active"
 */
export type ClassValue = string | false | null | undefined;

export function cn(...parts: ClassValue[]): string {
  return parts.filter(Boolean).join(' ');
}
