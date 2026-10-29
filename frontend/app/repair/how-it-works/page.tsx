"use client";
import { Status, useResource, VERIFIED_REPLAY } from "@/lib/repair";
import {
  Action,
  Badge,
  Button,
  Icon,
  Notice,
  PageHead,
  Panel,
} from "@/components/repair/ui";
export default function How() {
  const metrics = useResource<{
    tasks: number;
    queue_depth: number;
    finished: number;
    run_seconds_p50: number | null;
    run_seconds_p95: number | null;
    model_tokens: number;
    cost_usd: number;
    window_hours: number;
  }>("/metrics", 30000);
  const status = useResource<Status>("/status", 30000),
    ready = useResource<Record<string, string | boolean>>("/ready", 30000);
  return (
    <>
      <PageHead
        title="The agent proposes. Evidence decides."
        intro="Software correctness and ML integrity are different questions. Autopilot makes both visible before you review a repair."
        action={
          <Action href={VERIFIED_REPLAY} primary icon="play">
            See the workflow
          </Action>
        }
      />
      <div className="r-how-strip">
        {[
          ["Understand", "Freeze the repository commit and repair contract."],
          ["Investigate", "Form hypotheses and inspect measured failures."],
          ["Repair", "Write scoped code edits and regression tests."],
          ["Verify", "Check a frozen candidate independently."],
          ["Review", "Deliver a diff and evidence for human judgment."],
        ].map(([t, p], i) => (
          <div key={t} className="r-how-step">
            <span>{i + 1}</span>
            <h3>{t}</h3>
            <p>{p}</p>
          </div>
        ))}
      </div>
      <Panel
        title="Three trust zones"
        subtitle="Target deployment boundaries. Runtime availability is reported separately below."
      >
        <div className="r-architecture">
          <div>
            <Icon name="settings" />
            <h3>Trusted control</h3>
            <p>
              Contracts, allowed edits, durable state, budgets and worker
              ownership. The model cannot expand its own authority.
            </p>
            <Badge>Platform authority</Badge>
          </div>
          <div>
            <Icon name="terminal" />
            <h3>Untrusted execution</h3>
            <p>
              Repository code and candidate edits run in bounded environments.
              Runtime network and host access are denied by isolated runners.
            </p>
            <Badge>Candidate code</Badge>
          </div>
          <div>
            <Icon name="shield" />
            <h3>Independent grading</h3>
            <p>
              Protected tests and applicable ML checks. Sealed labels stay with
              the verifier; model artifacts are not loaded into its trusted
              process.
            </p>
            <Badge>Verification authority</Badge>
          </div>
        </div>
        <Notice title="Development evidence is not the final holdout">
          Candidate exploration uses development and selection checks. The final
          evaluation is reserved for a frozen finalist. A finite check suite is
          not a mathematical proof of correctness.
        </Notice>
      </Panel>
      <section className="r-section">
        <div className="r-grid">
          <Panel title="What the agent can do">
            <ul>
              <li>Read admitted repository files and inspect failures.</li>
              <li>Propose source edits inside authorized folders.</li>
              <li>Add regression tests and run declared commands.</li>
              <li>Use cited external documentation as contextual evidence.</li>
              <li>Offer a patch or draft PR for your review.</li>
            </ul>
          </Panel>
          <Panel title="What it cannot approve for itself">
            <ul>
              <li>
                Change protected grading rules or existing immutable tests.
              </li>
              <li>Open sealed labels during candidate search.</li>
              <li>Use an LLM explanation as a successful test result.</li>
              <li>Expand budgets or execution privileges.</li>
              <li>Merge a PR or promote a production model automatically.</li>
            </ul>
          </Panel>
        </div>
      </section>
      <section id="runtime" className="r-section">
        <Panel
          title="Runtime status"
          subtitle="Configuration and successful execution are separate signals."
          action={
            <Button
              onClick={() => {
                status.reload();
                ready.reload();
                metrics.reload();
              }}
            >
              Refresh status
            </Button>
          }
        >
          {status.error ? (
            <Notice
              title="The service has not reported runtime status"
              tone="warning"
            >
              {status.error}
            </Notice>
          ) : status.data ? (
            <dl className="r-detail-list">
              {[
                [
                  "Provider configured",
                  status.data.configured
                    ? "Yes — this alone does not prove a call succeeded"
                    : "No",
                ],
                [
                  "Successful calls, last 7 days",
                  String(status.data.successful_calls_7d),
                ],
                [
                  "Last successful model",
                  status.data.last_model_used || "Not reported",
                ],
                ["Runner", status.data.runner],
                [
                  "Isolation reported",
                  status.data.isolated ? "Isolated" : "Unisolated",
                ],
                ["Verifier", status.data.verifier || "Not reported"],
                [
                  "Live access",
                  status.data.live_runs
                    ? "Enabled, subject to server admission"
                    : "Disabled",
                ],
                [
                  "Worker heartbeat",
                  status.data.worker_seen_s_ago == null
                    ? "Not reported"
                    : `${status.data.worker_seen_s_ago} seconds ago`,
                ],
              ].map(([k, v]) => (
                <div key={k}>
                  <dt>{k}</dt>
                  <dd>{v}</dd>
                </div>
              ))}
            </dl>
          ) : (
            <p>Checking runtime…</p>
          )}
          <hr />
          {ready.data ? (
            <div className="r-actions">
              {Object.entries(ready.data).map(([k, v]) => (
                <Badge key={k} value={v === "ok" ? "PASS" : undefined}>
                  {k}: {String(v)}
                </Badge>
              ))}
            </div>
          ) : (
            <p>
              {ready.error
                ? "Readiness checks are unavailable."
                : "Checking service readiness…"}
            </p>
          )}
        </Panel>
      </section>
      <section className="r-section">
        <Panel
          title="Service activity"
          subtitle="Deployment totals, not a comparison benchmark. Refreshes every 30 seconds."
        >
          {metrics.error ? (
            <Notice title="Activity unavailable" tone="warning">
              {metrics.error}
            </Notice>
          ) : metrics.data ? (
            <dl className="r-detail-list">
              {Object.entries(metrics.data)
                .filter(([key]) =>
                  [
                    "window_hours",
                    "tasks",
                    "queue_depth",
                    "finished",
                    "run_seconds_p50",
                    "run_seconds_p95",
                    "model_tokens",
                    "cost_usd",
                  ].includes(key),
                )
                .map(([key, value]) => (
                  <div key={key}>
                    <dt>{key.replaceAll("_", " ")}</dt>
                    <dd>{value == null ? "Not measured" : String(value)}</dd>
                  </div>
                ))}
            </dl>
          ) : (
            <p>Loading measured activity...</p>
          )}
        </Panel>
      </section>
      <section className="r-section">
        <Notice title="What VERIFIED means">
          The configured checks passed for a particular patch, protocol and
          dataset. It does not certify untested situations, guarantee a correct
          diagnosis or authorize deployment. REJECTED and INCONCLUSIVE outcomes
          remain useful evidence.
        </Notice>
      </section>
    </>
  );
}
