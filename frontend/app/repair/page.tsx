"use client";
import { useState } from "react";
import Link from "next/link";
import evidence from "@/data/repair/evidence.json";
import {
  REPLAYS,
  VERIFIED_REPLAY,
  REJECTED_REPLAY,
  Status,
  useResource,
} from "@/lib/repair";
import { Action, Badge, Icon } from "@/components/repair/ui";

const source = "https://github.com/sankalp2515/AutoML-Autopilot";
const wrong = REPLAYS.churn_live_prompt_v1.candidates[0];
const mismatch = wrong.screen_receipt?.checks.find(
  (c) => c.name === "train_serve_parity",
)?.evidence.mismatched_rows;
const cases = [
  {
    name: "Churn serving parity",
    category: "A green test suite missed it",
    text: "A plausible patch passed software checks while online and batch scores disagreed. The ML checker rejected it.",
    proof: "Historical genuine recording · unisolated test runner",
    href: REJECTED_REPLAY,
    cta: "Inspect the rejected patch",
    icon: "branch",
  },
  {
    name: "Refund label leakage",
    category: "A tempting shortcut is refused",
    text: "An authored wrong patch gains access to information unavailable at prediction time. The fixture proves the leakage gate rejects it.",
    proof: "Authored checker proof · not an autonomous repair",
    href: "/repair/benchmark",
    cta: "Inspect the leakage proof",
    icon: "shield",
  },
  {
    name: "Credit group recall",
    category: "One average can hide a regression",
    text: "A higher overall score does not excuse weaker recall for a protected group. Declared segment checks evaluate that trade-off.",
    proof: "Authored checker proof · declared groups and limits",
    href: "/repair/benchmark",
    cta: "Inspect the group-check proof",
    icon: "chart",
  },
];
const faqs = [
  [
    "How is this different from a coding assistant?",
    "Autopilot proposes code changes, then an independent verifier evaluates software checks and the ML requirements declared in the repository contract. A patch can pass tests and still be rejected for leakage, serving mismatch or a failed quality guardrail.",
  ],
  [
    "Can I see it work without signing up?",
    "Yes. The recordings include actual model activity, candidate diffs and receipts. They are clearly labelled recorded, and viewing them starts no inference. Live judging access uses the code supplied with the submission.",
  ],
  [
    "Does VERIFIED mean the model is safe to deploy?",
    "It means the declared checks passed for that snapshot, patch and evaluation data. It is not a guarantee about every future input. Missing labels, unsupported checks and infrastructure failures must remain visible; your team still reviews and ships the change.",
  ],
  [
    "Will it change my repository or merge a PR?",
    "Repairs happen in isolated candidate workspaces. You can download a patch or explicitly request a draft PR. Autopilot never merges automatically.",
  ],
  [
    "Which repositories work today?",
    "The supported path is Python repositories with explicit commands and an autopilot.yaml contract. Six sample services cover serving parity, preprocessing, leakage, protected-group recall, serialization and numeric boundaries. Arbitrary frameworks and repository layouts are not universally supported.",
  ],
  [
    "What happens when a provider fails or a limit is reached?",
    "The interface reports the failure or admission limit without inventing a successful verdict. Existing evidence remains readable. The demo reserves finite inference credits for judging; a brief wait or an owner-assisted live session may be needed.",
  ],
  [
    "What can I upload?",
    "Use public code and synthetic or non-sensitive data for the hackathon demonstration. Labels are supplied separately to the verifier. Nebius Sandboxes beta prohibits personal or sensitive files. The hosted demo executes repairs in Nebius Sandboxes.",
  ],
];

export default function RepairHome() {
  const [gate, setGate] = useState<"software" | "ml">("ml");
  const status = useResource<Status>("/status", 30000);
  const proofs = evidence.checker_proofs.filter(
    (p) => p.wrong_code === "VERIFIED" && p.wrong_ml === "REJECTED",
  ).length;
  return (
    <div className="r-landing">
      <section className="lp-hero lp-container" aria-labelledby="landing-title">
        <div className="lp-hero-copy">
          <p className="lp-audience">
            <span className="lp-signal" /> Repository repair for ML engineers
          </p>
          <h1 id="landing-title">
            The ML coding agent that checks beyond green tests.
          </h1>
          <p className="lp-lead">
            Turn a failing ML repository into a reviewable patch, with
            independent evidence that the model still behaves correctly.
          </p>
          <p className="lp-trust-line">
            <Icon name="shield" size={17} /> Inspect the code. Inspect the
            checks. You control the merge.
          </p>
          <div className="r-actions">
            <Action href={VERIFIED_REPLAY} primary icon="play">
              Watch a verified repair
            </Action>
            <Action href="/repair/new?source=sample" icon="branch">
              Try a sample repo
            </Action>
          </div>
          <p className="lp-caption">
            Genuine recording. No account or API key needed to watch.
          </p>
        </div>
        <div
          className="lp-instrument"
          aria-label="Interactive explanation of independent verification"
        >
          <div className="lp-instrument-top">
            <span>
              <Icon name="branch" size={16} /> churn-service
            </span>
            <Badge>Recorded evidence</Badge>
          </div>
          <div className="lp-file">
            <span>candidate.patch</span>
            <code>
              {wrong.candidate.diff
                .split("\n")
                .filter((l) => l.startsWith("+") && !l.startsWith("+++"))
                .slice(0, 3)
                .join("\n")}
            </code>
          </div>
          <div
            className="lp-gate-switch"
            role="group"
            aria-label="Choose a verification layer"
          >
            <button
              aria-pressed={gate === "software"}
              onClick={() => setGate("software")}
            >
              <Icon name="check" size={17} /> Software tests
            </button>
            <button aria-pressed={gate === "ml"} onClick={() => setGate("ml")}>
              <Icon name="shield" size={17} /> ML behavior
            </button>
          </div>
          <div
            className={`lp-gate-result ${gate === "ml" ? "lp-result-reject" : "lp-result-pass"}`}
            aria-live="polite"
          >
            <span className="lp-result-symbol">
              <Icon name={gate === "ml" ? "close" : "check"} size={29} />
            </span>
            <div>
              <h2>
                {gate === "ml"
                  ? "Green tests. Wrong behavior."
                  : "The software checks passed."}
              </h2>
              <p>
                {gate === "ml"
                  ? `Online and batch scores disagreed on ${String(mismatch ?? "the checked")} rows. This patch was rejected.`
                  : "That is useful evidence. It does not establish that the model behaves the same when served."}
              </p>
            </div>
          </div>
          <div className="lp-observations" aria-hidden="true">
            {Array.from({ length: 32 }, (_, i) => (
              <span
                key={i}
                className={gate === "ml" ? "lp-observation-fail" : ""}
              />
            ))}
          </div>
          <div className="lp-instrument-foot">
            <span>Historical unisolated test runner</span>
            <Link href={REJECTED_REPLAY}>Read the actual receipt</Link>
          </div>
        </div>
      </section>
      <div className="lp-built-with lp-container">
        <span>Built around your engineering workflow</span>
        <div>
          <span>Python repositories</span>
          <span>Nemotron reasoning</span>
          <span>Independent verification</span>
          <span>Reviewable Git diffs</span>
        </div>
        <p>
          Genuine hosted repairs use Nemotron through Nebius Token Factory,
          with isolated execution in Nebius Sandboxes. Historical recordings
          retain their original provider labels.
        </p>
      </div>
      <section
        className="lp-section lp-container lp-problem"
        aria-labelledby="problem-title"
      >
        <div>
          <p className="lp-kicker">The gap after “tests passed”</p>
          <h2 id="problem-title">
            A working pipeline can still make the wrong predictions.
          </h2>
        </div>
        <div>
          <p>
            Serving code can build features differently. A repair can learn from
            leaked labels. An improved average can conceal a weaker result for
            one group.
          </p>
          <p>
            Autopilot investigates the code, tries bounded repairs and asks a
            separate verifier to check the evidence before presenting a
            finalist.
          </p>
          <Link className="r-text-link" href="/repair/how-it-works">
            See the trust boundaries
          </Link>
        </div>
      </section>
      <section
        className="lp-evidence-section"
        id="evidence"
        aria-labelledby="evidence-title"
      >
        <div className="lp-container">
          <div className="lp-section-head">
            <div>
              <p className="lp-kicker">Evidence you can open</p>
              <h2 id="evidence-title">
                A claim is only as good as its receipt.
              </h2>
            </div>
            <Link href="/repair/benchmark" className="r-text-link">
              View the evidence ledger
            </Link>
          </div>
          <div className="lp-proof-facts">
            <div>
              <strong>6</strong>
              <span>sample repositories</span>
              <small>Six distinct ML failure stories</small>
            </div>
            <div>
              <strong>
                {proofs}/{evidence.checker_proofs.length}
              </strong>
              <span>wrong-green patches rejected</span>
              <small>Authored checker proofs, not an agent success rate</small>
            </div>
            <div>
              <strong>Open</strong>
              <span>source, diffs and receipts</span>
              <small>GPL-3.0 · inspect the implementation</small>
            </div>
          </div>
          <p className="lp-honesty">
            An early product with inspectable evidence. We do not claim customer
            adoption, production case studies or a completed comparative
            benchmark.
          </p>
          <div className="lp-case-grid">
            {cases.map((c) => (
              <article key={c.name} className="lp-case">
                <Icon name={c.icon} size={25} />
                <p className="lp-case-category">{c.category}</p>
                <h3>{c.name}</h3>
                <p>{c.text}</p>
                <div>
                  <small>{c.proof}</small>
                  <Link href={c.href} className="r-text-link">
                    {c.cta}
                  </Link>
                </div>
              </article>
            ))}
          </div>
        </div>
      </section>
      <section
        className="lp-section lp-container"
        id="workflow"
        aria-labelledby="workflow-title"
      >
        <div className="lp-section-head">
          <div>
            <p className="lp-kicker">From failure to a decision</p>
            <h2 id="workflow-title">
              The agent does the investigation.
              <br />
              You get the evidence.
            </h2>
          </div>
          <p>Every stage produces something concrete to review.</p>
        </div>
        <ol className="lp-workflow">
          {[
            [
              "Set the boundaries",
              "Pin a repository snapshot and choose the failing test, permitted edits and ML requirements.",
            ],
            [
              "Investigate the cause",
              "Nemotron compares hypotheses against repository evidence. Relevant official documentation can inform the next edit.",
            ],
            [
              "Try isolated repairs",
              "Candidate branches produce actual source edits and regression tests under bounded tool and token budgets.",
            ],
            [
              "Check code and model",
              "A separate verifier checks software behavior, declared ML invariants and the available evaluation data.",
            ],
            [
              "Review and decide",
              "Read the diff and both receipts. Download the patch or explicitly open a draft PR. Nothing merges automatically.",
            ],
          ].map(([title, text], i) => (
            <li key={title}>
              <span aria-hidden="true">{i + 1}</span>
              <div>
                <h3>{title}</h3>
                <p>{text}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>
      <section className="lp-control-section">
        <div className="lp-container lp-control-grid">
          <div>
            <p className="lp-kicker">Built for the review you actually do</p>
            <h2>
              Less guessing.
              <br />A clearer handoff.
            </h2>
            <p>
              Designed for the ML engineer fixing a serving incident, the
              maintainer reviewing a generated patch and the reviewer asking
              what a green check really proves.
            </p>
            <Action href="/repair/new?source=url" icon="code">
              Fix your repo
            </Action>
          </div>
          <div className="lp-benefits">
            {[
              [
                "A diff, not just an answer",
                "See the exact source changes and regression test. Review the patch using the workflow you already know.",
                "code",
              ],
              [
                "A reason for every decision",
                "Inspect which checks passed, failed or could not run. Missing labels do not become invented quality claims.",
                "book",
              ],
              [
                "A boundary around each attempt",
                "The declared contract constrains edits and commands. Candidate execution is isolated, with runtime provenance recorded.",
                "shield",
              ],
              [
                "An approval that stays yours",
                "A verified result is a proposal. Your team decides whether to create a draft PR and whether to release.",
                "branch",
              ],
            ].map(([title, text, icon]) => (
              <article key={title}>
                <Icon name={icon} size={22} />
                <div>
                  <h3>{title}</h3>
                  <p>{text}</p>
                </div>
              </article>
            ))}
          </div>
        </div>
      </section>
      <section
        className="lp-section lp-container"
        id="access"
        aria-labelledby="access-title"
      >
        <div className="lp-section-head">
          <div>
            <p className="lp-kicker">Access, without surprises</p>
            <h2 id="access-title">
              Explore freely. Run live with an invitation.
            </h2>
          </div>
          <p>No paid plans or checkout in this hackathon build.</p>
        </div>
        <div className="lp-access-grid">
          <article>
            <Badge>Start here</Badge>
            <h3>Recorded walkthrough</h3>
            <p className="lp-access-price">
              $0 <span>to explore</span>
            </p>
            <p>
              Open genuine recordings, follow the candidate edits and inspect
              the receipts. No account, key or new inference.
            </p>
            <Action href={VERIFIED_REPLAY} primary icon="play">
              Watch a verified repair
            </Action>
          </article>
          <article>
            <Badge>Hackathon evaluation</Badge>
            <h3>Live judge workspace</h3>
            <p className="lp-access-price">
              $0 <span>for invited judges</span>
            </p>
            <p>
              A new repair on the supported workflow, funded by the project’s
              limited inference credits. Use the access code in the judging
              instructions.
            </p>
            <Action href="/repair/new?source=sample">Try a sample repo</Action>
          </article>
        </div>
        <div className="lp-access-note">
          <Icon name="clock" size={21} />
          <div>
            <strong>Why live access has limits</strong>
            <p>
              {status.data?.access_policy?.reason ||
                "Finite hackathon inference credits are reserved for evaluation. Limits help keep live access available throughout judging. Recordings, diffs and receipts remain readable without starting another model call."}
            </p>
            {status.data?.access_policy?.judge_only && (
              <p>
                Current policy:{" "}
                {status.data.access_policy.daily_runs_per_workspace} new runs
                per workspace in 24 hours;{" "}
                {status.data.access_policy.cooldown_seconds} seconds between
                starts;{" "}
                {status.data.access_policy.max_active_tasks || "bounded"} shared
                live slots. Existing runs can be reopened without restarting.
              </p>
            )}
            <p>
              If you need an extended evaluation, contact the project owner
              using the submission testing instructions.
            </p>
          </div>
        </div>
      </section>
      <section
        className="lp-section lp-container lp-faq-section"
        aria-labelledby="faq-title"
      >
        <div>
          <p className="lp-kicker">Before you try it</p>
          <h2 id="faq-title">
            Good questions.
            <br />
            Straight answers.
          </h2>
          <Link href="/repair/docs" className="r-text-link">
            Read the product docs
          </Link>
        </div>
        <div className="lp-faqs">
          {faqs.map(([q, a]) => (
            <details key={q}>
              <summary>
                {q}
                <Icon name="plus" size={18} />
              </summary>
              <p>{a}</p>
            </details>
          ))}
        </div>
      </section>
      <section className="lp-final lp-container">
        <div>
          <p className="lp-kicker">The next step is an actual repair</p>
          <h2>
            See what the patch changed.
            <br />
            Then see what proved it.
          </h2>
          <p>
            Start with the recorded result. Explore the live workspace when you
            are ready.
          </p>
        </div>
        <div>
          <Action href={VERIFIED_REPLAY} primary icon="play">
            Watch a verified repair
          </Action>
          <a href={source} className="r-text-link">
            Inspect the open-source project
          </a>
        </div>
      </section>
    </div>
  );
}
