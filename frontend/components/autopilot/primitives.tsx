'use client';
import type { ReactNode, SelectHTMLAttributes } from 'react';

export function Field({ label, htmlFor, children, hint }: { label: string; htmlFor: string; children: ReactNode; hint?: string }) {
  return <div className="ap-field"><label htmlFor={htmlFor}>{label}</label>{children}{hint && <small style={{ color: 'var(--t2)' }}>{hint}</small>}</div>;
}
export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) { return <select className="ap-select" {...props} />; }
export function DataTable({ head, label, children }: { head: string[]; label: string; children: ReactNode }) {
  return <div style={{ overflowX: 'auto' }}><table className="ap-table" aria-label={label}><thead><tr>{head.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead><tbody>{children}</tbody></table></div>;
}
export function Metric({ label, value }: { label: string; value: ReactNode }) {
  return <div style={{ background: 'var(--sur)', borderRadius: 8, padding: '10px 12px' }}><div style={{ fontSize: 12, color: 'var(--t2)' }}>{label}</div><div className="ds-mono" style={{ fontSize: 14, fontWeight: 600, marginTop: 3 }}>{value}</div></div>;
}
