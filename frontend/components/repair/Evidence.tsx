"use client";
import { useState } from "react";
import Link from "next/link";
import {
  api,
  Candidate,
  Check,
  download,
  duration,
  label,
  money,
  Report,
  safeGithub,
  tiers,
  totalCost,
} from "@/lib/repair";
import DiffViewer from "./DiffViewer";
import {
  Action,
  Badge,
  Button,
  Copy,
  Field,
  Icon,
  Modal,
  Notice,
  Panel,
} from "./ui";

export function Checks({ checks }: { checks: Check[] }) {
  return (
    <div>
      {checks.length ? (
        checks.map((c, i) => (
          <div
            key={i}
            className={`r-check ${c.status === "FAIL" ? "fail" : ""}`}
          >
            <Icon
              name={
                c.status === "PASS"
                  ? "check"
                  : c.status === "FAIL"
                    ? "close"
                    : "alert"
              }
              size={17}
            />
            <div>
              <strong>{label(c.name)}</strong> <Badge value={c.status} />
              <p>{c.detail}</p>
              {Object.keys(c.evidence || {}).length > 0 && (
                <details>
                  <summary>Measured evidence</summary>
                  <pre tabIndex={0}>{JSON.stringify(c.evidence, null, 2)}</pre>
                </details>
              )}
            </div>
          </div>
        ))
      ) : (
        <p className="r-muted">No checks have been published.</p>
      )}
    </div>
  );
}
export function Comparison({ candidates }: { candidates: Candidate[] }) {
  return (
    <div className="r-grid">
      {candidates.map((run) => {
        const failed = run.screen_receipt?.checks.find(
          (c) => c.status === "FAIL",
        );
        return (
          <Panel
            key={run.candidate.candidate_id}
            title={`Candidate ${run.candidate.candidate_id.replace(/^c/, "")}`}
            subtitle={
              run.candidate.summary || "Source changes and independent checks"
            }
          >
            <div className="r-compare-header">
              <code>
                {run.candidate.hypothesis_id || "No hypothesis attached"}
              </code>
              <Badge
                value={
                  failed
                    ? "REJECTED"
                    : run.code_receipt
                      ? run.eligible
                        ? "PASS"
                        : "INCONCLUSIVE"
                      : undefined
                }
              >
                {failed
                  ? "Not eligible"
                  : run.code_receipt
                    ? run.eligible
                      ? "Eligible for final grading"
                      : "Not selected"
                    : "Awaiting checks"}
              </Badge>
            </div>
            {run.code_receipt?.decision === "VERIFIED" && failed ? (
              <div className="r-ineligible">
                <strong>Tests green — rejected by ML check</strong>
                <p>{failed.detail}</p>
              </div>
            ) : run.eligible ? (
              <div className="r-eligible">
                Development checks passed. Final verification is a separate
                decision.
              </div>
            ) : null}
            <h3>Software checks</h3>
            {run.code_receipt ? (
              <Checks checks={run.code_receipt.checks} />
            ) : (
              <p className="r-muted">No code receipt yet.</p>
            )}
            <h3 style={{ marginTop: 20 }}>ML screening</h3>
            {run.screen_receipt ? (
              <Checks checks={run.screen_receipt.checks} />
            ) : (
              <p className="r-muted">No ML screen has been published.</p>
            )}
            <details style={{ marginTop: 20 }}>
              <summary>
                Inspect candidate diff ({run.diff_lines} changed lines)
              </summary>
              <DiffViewer diff={run.candidate.diff} />
            </details>
            {run.stop_reason && (
              <p style={{ fontSize: 12, marginTop: 12 }}>
                Agent stop reason: {run.stop_reason}
              </p>
            )}
          </Panel>
        );
      })}
    </div>
  );
}

export function Budget({ report }: { report?: Report | null }) {
  return (
    <Panel
      title="Execution budget"
      subtitle="Provider estimates are shown separately from verified outcomes."
    >
      <div className="r-budget">
        {report ? (
          tiers(report).map(([name, tier]) => {
            const used =
              (tier.prompt_tokens || 0) + (tier.completion_tokens || 0);
            return (
              <div key={name}>
                <label>
                  <strong>{label(name)}</strong>
                  <span>
                    {used.toLocaleString()}
                    {tier.budget
                      ? ` / ${tier.budget.toLocaleString()}`
                      : ""}{" "}
                    tokens
                  </span>
                </label>
                <progress
                  value={used}
                  max={Math.max(used, tier.budget || used || 1)}
                  aria-label={`${name} token use`}
                />
                <p>
                  {tier.calls} calls · {money(tier.est_cost_usd)} estimated
                  {tier.model ? ` · ${tier.model}` : ""}
                </p>
              </div>
            );
          })
        ) : (
          <p>
            Usage will appear when the worker publishes measured call records.
            No cost is estimated from elapsed time.
          </p>
        )}
        <div className="r-detail-list">
          <div>
            <span>Estimated inference</span>
            <strong>
              {report ? money(totalCost(report)) : "Not reported"}
            </strong>
          </div>
          <div>
            <span>Recorded execution</span>
            <strong>{report ? duration(report.seconds) : "In progress"}</strong>
          </div>
          <div>
            <span>Tool calls</span>
            <strong>
              {report
                ? report.candidates.reduce((sum, c) => sum + c.steps.length, 0)
                : "Not reported"}
            </strong>
          </div>
        </div>
        <p>Estimates exclude local compute, storage and unreported charges.</p>
      </div>
    </Panel>
  );
}
export function Verdict({ report }: { report: Report }) {
  const pass = report.decision === "VERIFIED",
    fail = report.decision === "REJECTED";
  return (
    <section
      className={`r-verdict ${pass ? "" : fail ? "rejected" : "inconclusive"}`}
      aria-label="Repair verdict"
    >
      <Icon name={pass ? "shield" : fail ? "close" : "alert"} size={30} />
      <div>
        <Badge value={report.decision} />
        <h2>
          {pass
            ? "A repair with evidence behind it."
            : fail
              ? "The evidence says: don’t merge this repair."
              : "The evidence is not sufficient to verify a repair."}
        </h2>
        <p>
          {pass
            ? "The selected patch passed the configured software and ML checks on the recorded inputs. Review the change and its limitations before merging."
            : fail
              ? "A candidate failed one or more required checks. Passing visible tests did not establish that the repair preserved ML behavior."
              : "Some required checks could not establish an acceptable outcome. This is not an approval to merge or deploy."}
        </p>
        {report.reasons?.length > 0 && (
          <p style={{ marginTop: 10 }}>
            {report.reasons.map(label).join("; ")}
          </p>
        )}
      </div>
    </section>
  );
}

export function ReceiptView({
  report,
  taskId,
  recorded,
}: {
  report: Report;
  taskId?: string;
  recorded: boolean;
}) {
  const [share, setShare] = useState(false),
    [shared, setShared] = useState(""),
    [error, setError] = useState("");
  const finalist = report.candidates.find(
    (c) => c.candidate.candidate_id === report.finalist,
  );
  const metric = report.final_ml_receipt?.metrics?.holdout,
    delta = report.final_ml_receipt?.metrics?.delta;
  return (
    <div className="r-stack">
      <Verdict report={report} />
      <div className="r-actions">
        <Button
          icon="download"
          disabled={!finalist}
          onClick={() =>
            finalist &&
            download("autopilot-repair.patch", finalist.candidate.diff)
          }
        >
          {recorded ? "Download recorded patch" : "Download .patch"}
        </Button>
        <Button
          icon="download"
          onClick={() =>
            download(
              "autopilot-receipt.json",
              JSON.stringify(report, null, 2),
              "application/json",
            )
          }
        >
          Download receipt JSON
        </Button>
        <Button onClick={() => window.print()}>Print receipt</Button>
        {recorded ? (
          <Copy
            value={typeof window === "undefined" ? "" : window.location.href}
            title="Copy replay link"
          />
        ) : (
          <Button onClick={() => setShare(true)}>
            Share a read-only replay
          </Button>
        )}
      </div>
      {error && (
        <Notice title="Sharing was not completed" tone="error">
          {error}
        </Notice>
      )}
      {shared && (
        <Notice title="Read-only replay published">
          <Copy value={shared} title="Copy share link" />
          <p>{shared}</p>
        </Notice>
      )}
      {metric && (
        <Panel
          title="Held-out predictive quality"
          subtitle="Measured only after the finalist was frozen."
        >
          <div className="r-metric-row">
            <div>
              <strong>{metric.candidate.toFixed(4)}</strong>
              <span>
                {label(metric.metric)} · {metric.rows.toLocaleString()} rows
              </span>
            </div>
            {metric.baseline != null && (
              <div>
                <strong>{metric.baseline.toFixed(4)}</strong>
                <span>Baseline {label(metric.metric)}</span>
              </div>
            )}
            {delta && (
              <div>
                <strong>{delta.delta.toFixed(4)}</strong>
                <span>
                  Paired change · 95% CI [{delta.ci_low.toFixed(4)},{" "}
                  {delta.ci_high.toFixed(4)}]
                </span>
              </div>
            )}
          </div>
          <p>
            Predictive quality was preserved in this recording; this is not a
            claim of an accuracy increase.
          </p>
        </Panel>
      )}
      {finalist && (
        <DiffViewer
          diff={finalist.candidate.diff}
          title="Finalist source changes"
        />
      )}
      {finalist && (
        <Panel title="Regression test added by the agent">
          {finalist.candidate.files_changed.filter((f) => f.includes("test"))
            .length ? (
            <div className="r-stack">
              {finalist.candidate.files_changed
                .filter((f) => f.includes("test"))
                .map((f) => (
                  <code key={f}>{f}</code>
                ))}
              <p>
                Inspect the added test in the diff above. Generated tests are
                supporting evidence; the independent checks remain separate.
              </p>
            </div>
          ) : (
            <Notice title="No added regression test is listed" tone="warning">
              This recording does not establish a newly added regression test.
              Review maintainability and coverage before merging.
            </Notice>
          )}
        </Panel>
      )}
      <div className="r-grid">
        <Panel
          title="Independent software receipt"
          subtitle={
            finalist?.code_receipt?.receipt_digest || "No selected finalist"
          }
        >
          {finalist?.code_receipt ? (
            <Checks checks={finalist.code_receipt.checks} />
          ) : (
            <p>
              No finalist code receipt. Inspect rejected candidates in the
              comparison.
            </p>
          )}
        </Panel>
        <Panel
          title="Independent ML receipt"
          subtitle={
            report.final_ml_receipt?.holdout_opened
              ? "Final holdout was opened for the frozen finalist"
              : "No final holdout receipt"
          }
        >
          {report.final_ml_receipt ? (
            <Checks checks={report.final_ml_receipt.checks} />
          ) : (
            <p>
              No final label-based evaluation was accepted. Screening checks are
              shown per candidate.
            </p>
          )}
        </Panel>
      </div>
      <Panel title="Provenance & reproduction">
        <div className="r-detail-list">
          <div>
            <span>Evidence type</span>
            <Badge>
              {recorded ? "Recorded genuine execution" : "Observed execution"}
            </Badge>
          </div>
          <div>
            <span>Original provider</span>
            <strong>{report.provider || "Not reported"}</strong>
          </div>
          <div>
            <span>Execution runner</span>
            <strong>{report.runner}</strong>
          </div>
          <div>
            <span>Contract digest</span>
            <code>{report.contract_digest}</code>
          </div>
          {report.source_sha256 && (
            <div>
              <span>Original recording SHA-256</span>
              <code>{report.source_sha256}</code>
            </div>
          )}
        </div>
        <div className="r-actions" style={{ margin: "20px 0" }}>
          <Copy
            value={
              "git apply autopilot-repair.patch\npython -m pytest -q tests"
            }
            title="Copy reproduction commands"
          />
        </div>
        <pre tabIndex={0} className="r-terminal">
          git apply autopilot-repair.patch{`\n`}python -m pytest -q tests
        </pre>
        <p style={{ marginTop: 14 }}>
          Run from the pinned repository with its dependencies installed.
          Reproducing independent ML checks also requires the original
          evaluation protocol and authorized evaluation data.
        </p>
        {report.runner === "process-unisolated" && (
          <div style={{ marginTop: 16 }}>
            <Notice title="Historical development runner" tone="warning">
              This recording used an unisolated process runner. It demonstrates
              measured repair behavior, not production sandbox isolation.
            </Notice>
          </div>
        )}
      </Panel>
      <Budget report={report} />
      {share && (
        <Modal
          title="Publish a read-only replay?"
          onClose={() => setShare(false)}
        >
          <div className="r-stack">
            <p>
              This publishes the task description, source diff, reasoning
              excerpts, logs and receipts. Review the report for private code
              and secrets before sharing.
            </p>
            <Notice
              title="A link may be accessible to anyone who receives it"
              tone="warning"
            >
              Evaluation label files are not part of this report. Check all
              other content before you publish.
            </Notice>
            <div className="r-actions">
              <Button onClick={() => setShare(false)}>Keep private</Button>
              <Button
                kind="primary"
                onClick={async () => {
                  try {
                    const result = await api<{ replay_id: string }>(
                      `/tasks/${taskId}/publish`,
                      { method: "POST" },
                    );
                    setShared(
                      `${window.location.origin}/repair/replay/${encodeURIComponent(result.replay_id)}`,
                    );
                    setShare(false);
                  } catch (e) {
                    setError((e as Error).message);
                    setShare(false);
                  }
                }}
              >
                Publish replay
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </div>
  );
}

export function PRHandoff({
  report,
  taskId,
  recorded,
}: {
  report: Report;
  taskId?: string;
  recorded: boolean;
}) {
  const [token, setToken] = useState(""),
    [repo, setRepo] = useState(""),
    [base, setBase] = useState("main"),
    [title, setTitle] = useState(
      "Repair ML serving behavior with independent verification",
    ),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(""),
    [pr, setPr] = useState("");
  const body = `## Repair\n${report.target || "Restore ML serving behavior"}\n\n## Verification\nDecision: ${report.decision}\nContract: ${report.contract_digest}\nFinalist: ${report.finalist || "None"}\nRunner: ${report.runner}\n\nEstimated inference: ${money(totalCost(report))}\n\nReview source changes and independent code/ML receipts. This PR must not be merged automatically.`;
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) {
      setError("Enter a repository as owner/name.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const result = await api<{ html_url?: string; url?: string }>(
        `/tasks/${taskId}/pr`,
        {
          method: "POST",
          body: JSON.stringify({ token, repo, base_branch: base, title }),
        },
      );
      const url = result.html_url || result.url || "";
      if (!safeGithub(url))
        throw Error(
          "The service did not return a valid GitHub pull request URL.",
        );
      setPr(url);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setToken("");
      setBusy(false);
    }
  }
  return (
    <div className="r-grid">
      <Panel
        title="Draft pull request preview"
        subtitle="Nothing is merged or deployed automatically."
      >
        <div className="r-stack">
          <Field label="PR title">
            <input
              value={title}
              maxLength={200}
              onChange={(e) => setTitle(e.target.value)}
            />
          </Field>
          <pre tabIndex={0} className="r-contract-code">
            {body}
          </pre>
          <Copy value={body} title="Copy PR description" />
        </div>
      </Panel>
      <Panel title="Hand the repair to your team">
        <div className="r-stack">
          {recorded ? (
            <Notice title="This is a recorded run">
              External PR creation is unavailable for historical recordings. You
              can download the recorded patch and review its evidence.
            </Notice>
          ) : report.decision !== "VERIFIED" ? (
            <Notice
              title="Only a verified finalist can be delivered"
              tone="warning"
            >
              No draft PR will be created for a rejected or inconclusive repair.
            </Notice>
          ) : (
            <form onSubmit={submit} className="r-stack">
              <Field label="GitHub repository">
                <input
                  value={repo}
                  onChange={(e) => setRepo(e.target.value)}
                  placeholder="owner/repository"
                  required
                />
              </Field>
              <Field label="Base branch">
                <input
                  value={base}
                  onChange={(e) => setBase(e.target.value)}
                  required
                />
              </Field>
              <Field
                label="GitHub access token"
                hint="Used once by the delivery service; not saved in this browser or placed in a URL. Scope it to the intended repository."
              >
                <input
                  type="password"
                  autoComplete="off"
                  value={token}
                  minLength={10}
                  maxLength={300}
                  onChange={(e) => setToken(e.target.value)}
                  required
                />
              </Field>
              <Button type="submit" kind="primary" disabled={busy}>
                {busy ? "Creating draft…" : "Create draft PR"}
              </Button>
            </form>
          )}
          {error && (
            <Notice title="Draft PR was not created" tone="error">
              {error}
            </Notice>
          )}
          {pr && (
            <Notice title="Draft pull request created">
              <a
                href={pr}
                target="_blank"
                rel="noreferrer"
                className="r-text-link"
              >
                Open draft on GitHub
              </a>
            </Notice>
          )}
          <p>
            You retain authority over code review, merge and any model release.
          </p>
          <Link className="r-text-link" href="/repair/docs?section=handoff">
            Read the delivery guide
          </Link>
        </div>
      </Panel>
    </div>
  );
}
