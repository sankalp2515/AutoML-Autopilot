'use client';

import { useEffect, useState } from 'react';
import { usePathname, useRouter } from 'next/navigation';
import { supabase, authEnabled } from '@/lib/supabase';

// Routes judges/testers can use without an account (the hackathon demo path).
// The backend still enforces tenant scoping on every Autopilot request.
const PUBLIC_PREFIXES = ['/autopilot'];

// Redirects to /login when auth is enabled and the visitor isn't signed in.
// No-op when Supabase isn't configured (public mode) — renders children directly.
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const isPublic = PUBLIC_PREFIXES.some((p) => pathname === p || pathname.startsWith(`${p}/`));
  const [ready, setReady] = useState(!authEnabled);

  useEffect(() => {
    if (!authEnabled || !supabase || isPublic) {
      setReady(true);
      return;
    }
    let active = true;
    supabase.auth.getSession().then(({ data }) => {
      if (!active) return;
      if (!data.session && pathname !== '/login') {
        router.replace('/login');
      } else {
        setReady(true);
      }
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      if (!session && pathname !== '/login') router.replace('/login');
      else setReady(true);
    });
    return () => {
      active = false;
      sub.subscription.unsubscribe();
    };
  }, [pathname, router, isPublic]);

  if (!ready && !isPublic) return null;
  return <>{children}</>;
}
