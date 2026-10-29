"use client";
import { useState } from "react";
import Link from "next/link";
import { api, useResource } from "@/lib/repair";
import {
  Role,
  Workspace,
  selectWorkspace,
  signOut,
  useIdentity,
} from "@/lib/repair-identity";
import {
  Badge,
  Button,
  Field,
  Notice,
  PageHead,
  Panel,
} from "@/components/repair/ui";
type Member = { login: string; name: string; role: Role; account_id: string };
type RepoLink = {
  repo: string;
  installed: boolean;
  auto_pr: boolean;
  recent: { event: string; outcome: string; task_id?: string; at: number }[];
};
const roles: Role[] = ["viewer", "member", "admin", "owner"];
export default function Settings() {
  const identity = useIdentity(),
    me = identity.data;
  const signIn = useResource<{ url: string; state: string }>(
    identity.signedIn ? null : "/auth/github",
  );
  const members = useResource<{ members: Member[] }>(
    me ? `/workspaces/${me.current_workspace}/members` : null,
  );
  const links = useResource<{ links: RepoLink[] }>(me ? "/github/links" : null);
  const [name, setName] = useState(""),
    [login, setLogin] = useState(""),
    [role, setRole] = useState<Role>("member");
  const [repo, setRepo] = useState(""),
    [autoPr, setAutoPr] = useState(false);
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [message, setMessage] = useState("");
  async function action(run: () => Promise<void>) {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      await run();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function member(who: string, next: Role | null) {
    await action(async () => {
      await api(
        `/workspaces/${me!.current_workspace}/members/${encodeURIComponent(who)}`,
        { method: "PUT", body: JSON.stringify({ role: next }) },
      );
      members.reload();
      identity.reload();
      setMessage(`Access updated for ${who}.`);
    });
  }
  return (
    <>
      <PageHead
        title="Your workspace"
        intro="Choose where your repairs belong and who can review or run them."
      />
      {(error || identity.error) && (
        <Notice title="Workspace action incomplete" tone="error">
          {error || identity.error}
        </Notice>
      )}
      {message && <Notice title="Saved">{message}</Notice>}
      <div className="r-grid">
        <Panel title="Account">
          <div className="r-stack">
            <Badge>
              {identity.signedIn
                ? me
                  ? `Signed in as ${me.account.login}`
                  : "Checking sign-in"
                : "Browser session"}
            </Badge>
            <p>
              {me
                ? me.account.name || me.account.login
                : "Anonymous repairs belong to this browser. Sign in with GitHub to work with your team."}
            </p>
            {identity.signedIn ? (
              <Button onClick={signOut}>Sign out</Button>
            ) : signIn.error ? (
              <Notice title="GitHub sign-in is unavailable">
                {signIn.error}
              </Notice>
            ) : (
              <Button
                disabled={busy || signIn.loading}
                onClick={() =>
                  action(async () => {
                    const result = await api<{ url: string; state: string }>(
                      "/auth/github",
                    );
                    const url = new URL(result.url);
                    if (
                      url.origin !== "https://github.com" ||
                      url.pathname !== "/login/oauth/authorize"
                    )
                      throw new Error(
                        "The server returned an invalid GitHub sign-in URL.",
                      );
                    sessionStorage.setItem("repair-oauth-state", result.state);
                    window.location.assign(result.url);
                  })
                }
              >
                Sign in with GitHub
              </Button>
            )}
            <p className="r-muted">
              GitHub sign-in must be configured by the operator. Connecting a
              repository for draft pull requests is a separate choice.
            </p>
          </div>
        </Panel>
        <Panel title="Workspaces">
          <div className="r-stack">
            {me ? (
              <>
                <Field label="Current workspace">
                  <select
                    value={me.current_workspace}
                    onChange={(e) => selectWorkspace(e.target.value)}
                  >
                    {me.workspaces.map((w) => (
                      <option value={w.id} key={w.id}>
                        {w.name} ({w.role})
                      </option>
                    ))}
                  </select>
                </Field>
                <Badge>{me.role} access</Badge>
                <form
                  className="r-stack"
                  onSubmit={(e) => {
                    e.preventDefault();
                    void action(async () => {
                      const w = await api<Workspace>("/workspaces", {
                        method: "POST",
                        body: JSON.stringify({ name }),
                      });
                      selectWorkspace(w.id);
                    });
                  }}
                >
                  <Field label="New workspace name">
                    <input
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      required
                      maxLength={100}
                    />
                  </Field>
                  <Button type="submit" disabled={busy || !name.trim()}>
                    Create workspace
                  </Button>
                </form>
              </>
            ) : (
              <p>
                Sign in to create a workspace or choose one your team has added
                you to.
              </p>
            )}
          </div>
        </Panel>
        <Panel title="Team access">
          <div className="r-stack">
            <p>
              Viewers can read. Members can run and deliver repairs. Admins
              manage repository links and members. Owners manage ownership.
            </p>
            {members.error && (
              <Notice title="Could not load members" tone="error">
                {members.error}
              </Notice>
            )}
            {members.data?.members.map((m) => (
              <div key={m.account_id} className="r-stack">
                <strong>
                  {m.name || m.login} (@{m.login})
                </strong>
                {identity.canAdmin &&
                (me?.role === "owner" || m.role !== "owner") ? (
                  <div className="r-actions">
                    <select
                      aria-label={`Role for ${m.login}`}
                      value={m.role}
                      disabled={busy}
                      onChange={(e) =>
                        void member(m.login, e.target.value as Role)
                      }
                    >
                      {roles
                        .filter((r) => me?.role === "owner" || r !== "owner")
                        .map((r) => (
                          <option key={r}>{r}</option>
                        ))}
                    </select>
                    <Button
                      kind="quiet"
                      disabled={busy}
                      onClick={() => void member(m.login, null)}
                    >
                      Remove {m.login}
                    </Button>
                  </div>
                ) : (
                  <Badge>{m.role}</Badge>
                )}
              </div>
            ))}
            {identity.canAdmin && (
              <form
                className="r-stack"
                onSubmit={(e) => {
                  e.preventDefault();
                  void member(login, role);
                }}
              >
                <Field
                  label="Teammate GitHub login"
                  hint="They must have signed in to AutoML Autopilot once."
                >
                  <input
                    value={login}
                    onChange={(e) => setLogin(e.target.value)}
                    required
                    pattern="[A-Za-z0-9-]+"
                  />
                </Field>
                <Field label="Teammate role">
                  <select
                    value={role}
                    onChange={(e) => setRole(e.target.value as Role)}
                  >
                    {roles
                      .filter((r) => me?.role === "owner" || r !== "owner")
                      .map((r) => (
                        <option key={r}>{r}</option>
                      ))}
                  </select>
                </Field>
                <Button type="submit" disabled={busy}>
                  Add teammate
                </Button>
              </form>
            )}
            {!identity.canAdmin && (
              <p className="r-muted">
                A workspace admin or owner manages team access.
              </p>
            )}
          </div>
        </Panel>
        <Panel title="Repair failed CI runs">
          <div className="r-stack">
            <p>
              Link a repository to admit failed CI runs through the same repair
              checks. Automatic pull requests are drafts; merges stay with your
              team.
            </p>
            {links.error && (
              <Notice title="Could not load repository links" tone="error">
                {links.error}
              </Notice>
            )}
            {links.data?.links.map((l) => (
              <div className="r-stack" key={l.repo}>
                <strong>{l.repo}</strong>
                <Badge>
                  {l.installed ? "App connected" : "Installation not confirmed"}
                </Badge>
                {!l.installed && (
                  <p>
                    Install the GitHub App on this repository. Ask the operator
                    for the installation link for this deployment; no
                    installation has been confirmed yet.
                  </p>
                )}
                <p>
                  {l.auto_pr
                    ? "Draft PR after verification"
                    : "Review repairs before delivery"}
                </p>
                {l.recent.map((r, i) => (
                  <p key={i}>
                    {r.outcome}
                    {r.task_id && (
                      <>
                        {" "}
                        <Link
                          href={`/repair/tasks/${encodeURIComponent(r.task_id)}`}
                        >
                          Open repair
                        </Link>
                      </>
                    )}
                  </p>
                ))}
                {identity.canAdmin && (
                  <Button
                    kind="quiet"
                    disabled={busy}
                    onClick={() =>
                      action(async () => {
                        await api(
                          `/github/links/${l.repo.split("/").map(encodeURIComponent).join("/")}`,
                          { method: "DELETE" },
                        );
                        links.reload();
                        setMessage("Repository unlinked.");
                      })
                    }
                  >
                    Unlink {l.repo}
                  </Button>
                )}
              </div>
            ))}
            {identity.canAdmin && (
              <form
                className="r-stack"
                onSubmit={(e) => {
                  e.preventDefault();
                  void action(async () => {
                    await api("/github/links", {
                      method: "POST",
                      body: JSON.stringify({ repo, auto_pr: autoPr }),
                    });
                    links.reload();
                    setRepo("");
                    setMessage(
                      "Repository linked. Confirm the GitHub App installation next.",
                    );
                  });
                }}
              >
                <Field label="GitHub repository" hint="owner/repository">
                  <input
                    value={repo}
                    onChange={(e) => setRepo(e.target.value)}
                    required
                    pattern="[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+"
                  />
                </Field>
                <label>
                  <input
                    type="checkbox"
                    checked={autoPr}
                    onChange={(e) => setAutoPr(e.target.checked)}
                  />{" "}
                  Open a draft PR after verification
                </label>
                <Button type="submit" disabled={busy}>
                  Link repository
                </Button>
              </form>
            )}
          </div>
        </Panel>
      </div>
    </>
  );
}
