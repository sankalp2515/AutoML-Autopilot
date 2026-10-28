'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { supabase, authEnabled } from '@/lib/supabase';

// Minimal email/password auth screen (Phase 5). Styling uses the existing
// "Atelier" vocabulary classes. If Supabase isn't configured, it explains how.
export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<'signin' | 'signup'>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (!authEnabled) {
    return (
      <main className="mx-auto max-w-md p-8">
        <h1 className="font-display text-2xl text-bone-100">Sign in</h1>
        <p className="mt-4 text-bone-300">
          Authentication is not configured. Set <code>NEXT_PUBLIC_SUPABASE_URL</code> and{' '}
          <code>NEXT_PUBLIC_SUPABASE_ANON_KEY</code> in <code>frontend/.env.local</code>, then
          run <code>npm install @supabase/supabase-js</code> and rebuild.
        </p>
      </main>
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const fn =
        mode === 'signin'
          ? supabase!.auth.signInWithPassword({ email, password })
          : supabase!.auth.signUp({ email, password });
      const { error } = await fn;
      if (error) {
        setMsg(error.message);
      } else if (mode === 'signup') {
        setMsg('Check your email to confirm your account, then sign in.');
      } else {
        router.push('/');
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto max-w-md p-8">
      <h1 className="font-display text-3xl text-bone-100">
        {mode === 'signin' ? 'Sign in' : 'Create account'}
      </h1>
      <form onSubmit={submit} className="lux-card mt-6 space-y-4 p-6">
        <label className="block">
          <span className="eyebrow">Email</span>
          <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)}
            className="mt-1 w-full bg-transparent border border-bone-700 rounded px-3 py-2 text-bone-100" />
        </label>
        <label className="block">
          <span className="eyebrow">Password</span>
          <input type="password" required minLength={6} value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full bg-transparent border border-bone-700 rounded px-3 py-2 text-bone-100" />
        </label>
        <button type="submit" disabled={busy} className="btn-gold w-full">
          {busy ? 'Working…' : mode === 'signin' ? 'Sign in' : 'Sign up'}
        </button>
        {msg && <p className="text-terra-400 text-sm">{msg}</p>}
      </form>
      <button
        onClick={() => setMode(mode === 'signin' ? 'signup' : 'signin')}
        className="btn-ghost mt-4 w-full text-sm"
      >
        {mode === 'signin' ? 'Need an account? Sign up' : 'Have an account? Sign in'}
      </button>
    </main>
  );
}
