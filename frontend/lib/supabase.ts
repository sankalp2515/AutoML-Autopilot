// Supabase browser client (Phase 5 auth). Opt-in: if the env vars are absent the
// client is null and the app behaves exactly as before (public, no login).
// Requires `npm install @supabase/supabase-js` and, in frontend/.env.local:
//   NEXT_PUBLIC_SUPABASE_URL=...        NEXT_PUBLIC_SUPABASE_ANON_KEY=...
import { createClient, type SupabaseClient } from '@supabase/supabase-js';

const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anon = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

export const supabase: SupabaseClient | null =
  url && anon ? createClient(url, anon) : null;

export const authEnabled = !!supabase;

/** Current access token (JWT) to send to the backend, or null if not signed in. */
export async function getAccessToken(): Promise<string | null> {
  if (!supabase) return null;
  const { data } = await supabase.auth.getSession();
  return data.session?.access_token ?? null;
}
