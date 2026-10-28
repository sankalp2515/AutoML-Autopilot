// Decoupled toast bus: any module calls toast(); the AppShell listens and renders.
export type ToastType = 'success' | 'error' | 'info';

export interface ToastDetail {
  msg: string;
  type: ToastType;
}

export function toast(msg: string, type: ToastType = 'info'): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent<ToastDetail>('app-toast', { detail: { msg, type } }));
}
