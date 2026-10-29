"use client";
import { useEffect, useState } from "react";
import { authEnabled, supabase } from "@/lib/supabase";
import {
  Action,
  Badge,
  Button,
  Notice,
  PageHead,
  Panel,
} from "@/components/repair/ui";
export default function Settings() {
  const [email, setEmail] = useState<string | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    if (supabase)
      void supabase.auth.getUser().then(({ data, error }) => {
        setEmail(data.user?.email || null);
        if (error) setError("Could not confirm the current account.");
      });
  }, []);
  return (
    <>
      <PageHead
        title="Your workspace"
        intro="Identity, access and the boundaries of this deployment."
      />
      <div className="r-grid">
        <Panel title="Account">
          <div className="r-stack">
            <Badge>{email ? "Signed in" : "Browser session"}</Badge>
            <p>
              {email ||
                "Your tasks are scoped to this browser session when account authentication is not enabled."}
            </p>
            {authEnabled ? (
              email ? (
                <Button
                  onClick={async () => {
                    const result = await supabase?.auth.signOut();
                    if (result?.error)
                      setError("Sign-out did not complete. Try again.");
                    else setEmail(null);
                  }}
                >
                  Sign out
                </Button>
              ) : (
                <Action href="/login">Sign in</Action>
              )
            ) : (
              <Notice title="Account sign-in is not configured">
                The public recording path remains available. A server operator
                must configure authentication before it can protect organization
                workspaces.
              </Notice>
            )}
            {error && (
              <Notice title="Account action incomplete" tone="error">
                {error}
              </Notice>
            )}
          </div>
        </Panel>
        <Panel title="Team access">
          <div className="r-stack">
            <Badge>Not enabled</Badge>
            <p>
              Organization workspaces, invitations and reviewer roles need
              server-side membership and authorization. This deployment has not
              exposed those capabilities.
            </p>
            <Button disabled>Invite a teammate</Button>
            <Notice title="Personal scope is not organization access control">
              Do not treat browser-session separation as team authorization. Ask
              the operator about authenticated ownership before connecting
              private projects.
            </Notice>
            <Action href="/repair/docs?section=privacy">
              Privacy & access model
            </Action>
          </div>
        </Panel>
      </div>
    </>
  );
}
