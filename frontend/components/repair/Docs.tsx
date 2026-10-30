"use client";
import { useSearchParams } from "next/navigation";
import { useState } from "react";
import { Action, Copy, Notice, PageHead, Panel } from "./ui";

const reference = `version: 1
name: churn-service
python: "3.12"
install: "pip install -r requirements.txt"
commands:
  test: "pytest -q tests"
  train: "python -m churn.train --data data/train.csv --out {artifacts}/model.joblib"
  predict: "python -m churn.predict --model {artifacts}/model.joblib --input {holdout_features} --out {predictions}"
entrypoints:
  serve: "churn.serve:score_request"
data:
  train: data/train.csv
  holdout_features: data/holdout_features.csv
  id_column: customer_id
  target: churned
  problem: binary
  positive_label: 1
task:
  type: restore
  metric: roc_auc
  fail_to_pass: ["tests/test_serve.py::test_score_request_returns_probability"]
  ml_invariants: ["train_serve_parity", "holdout_non_inferior"]
policy:
  allowed_edit_roots: ["churn/", "tests/"]
  immutable: ["autopilot.yaml", "data/", "requirements.txt"]
  immutable_existing_tests: true
limits:
  test_seconds: 120
  train_seconds: 120`;
const sections = [
  ["quickstart", "Getting started"],
  ["contract", "Repository contract"],
  ["support", "Supported repositories"],
  ["evaluation", "Evaluation packs"],
  ["handoff", "PR handoff"],
  ["privacy", "Privacy & retention"],
  ["faq", "FAQ"],
];
export default function Docs() {
  const search = useSearchParams(),
    [selected, setSelected] = useState(search.get("section") || "quickstart");
  const section = sections.some(([s]) => s === selected)
    ? selected
    : "quickstart";
  return (
    <>
      <PageHead
        title="A clear contract. An inspectable repair."
        intro="Everything you need to connect a repository, set its boundaries and read the evidence."
      />
      <div className="r-doc-layout">
        <nav className="r-doc-nav" aria-label="Documentation sections">
          {sections.map(([id, title]) => (
            <button
              key={id}
              aria-pressed={section === id}
              onClick={() => setSelected(id)}
            >
              {title}
            </button>
          ))}
        </nav>
        <article className="r-prose">
          {section === "quickstart" && (
            <>
              <h2>Your first repair</h2>
              <p>
                Start with a recorded run to understand the workflow without any
                model calls. A recording retains actual source changes, measured
                checks and the original execution context.
              </p>
              <ol>
                <li>Open a verified recording and use the guided tour.</li>
                <li>
                  Compare it with the earlier rejected recording. Inspect why
                  software tests were insufficient.
                </li>
                <li>
                  Choose a sample repository or connect your own public Git
                  repository / ZIP.
                </li>
                <li>
                  Validate the repair contract, discover failing tests and
                  optionally upload an evaluation pack.
                </li>
                <li>
                  Start a live repair only when your server has enabled access
                  and set spending caps.
                </li>
              </ol>
              <div className="r-actions">
                <Action href="/repair/replay/churn_live_prompt_v2" primary>
                  Open the verified recording
                </Action>
                <Action href="/repair/new">Start a repair</Action>
              </div>
              <h3>Have a data incident instead?</h3>
              <p>
                If your model’s metric dropped and there is no failing software
                test, the existing data investigator compares reference and
                current data windows.
              </p>
              <Action href="/autopilot/new">Investigate a data incident</Action>
            </>
          )}
          {section === "contract" && (
            <>
              <h2>The autopilot.yaml contract</h2>
              <p>
                This versioned file describes how your repository is tested,
                trained and scored. Review detected commands before saving. The
                platform freezes the admitted contract; the agent cannot grant
                itself broader permissions.
              </p>
              <Copy value={reference} title="Copy example YAML" />
              <pre
                tabIndex={0}
                className="r-contract-code"
                style={{ marginTop: 16, maxHeight: "none" }}
              >
                {reference}
              </pre>
              <h3>Fields that matter</h3>
              <ul>
                <li>
                  <code>commands</code>: single argv-style commands; shell
                  chaining and redirects are not allowed.
                </li>
                <li>
                  <code>entrypoints.serve</code>: a function accepting one
                  record and a loaded model, returning a probability.
                </li>
                <li>
                  <code>data</code>: training data and unlabeled holdout
                  features with stable row IDs.
                </li>
                <li>
                  <code>task.type</code>: restore, preserve or improve. Quality
                  improvement is not required for every software repair.
                </li>
                <li>
                  <code>policy</code>: editable roots and immutable files.
                  Existing tests and test configuration remain protected.
                </li>
                <li>
                  <code>limits</code>: bounded test and training execution time.
                </li>
              </ul>
              <h3>Supported placeholders</h3>
              <p>
                <code>{"{artifacts}"}</code>,{" "}
                <code>{"{holdout_features}"}</code>,{" "}
                <code>{"{predictions}"}</code>, <code>{"{rows}"}</code> and{" "}
                <code>{"{out}"}</code> are filled by the platform. They are not
                user-selected shell commands.
              </p>
            </>
          )}
          {section === "support" && (
            <>
              <h2>Supported repositories and limits</h2>
              <p>
                The initial workflow targets small Python ML repositories with
                declared test, training, prediction and serving entrypoints. The
                available recordings use a pandas / scikit-learn churn service.
              </p>
              <ul>
                <li>
                  Use a public GitHub, GitLab or Bitbucket HTTPS URL, a branch /
                  exact commit, or a repository ZIP.
                </li>
                <li>
                  This client prechecks ZIPs at 20 MB. The server may apply
                  stricter file, size and admission policies.
                </li>
                <li>
                  Pin dependencies. Installs occur during preparation, not
                  arbitrary model-directed runtime commands.
                </li>
                <li>
                  Use repository-relative paths. Symlinks, path escapes and
                  protected configuration edits are refused.
                </li>
                <li>
                  The ML checker supports binary, multiclass and regression
                  contracts. Declare data.problem and a compatible task.metric.
                  A custom entrypoints.load_model loader can load other
                  frameworks; dependencies and compute support still need
                  validation.
                </li>
              </ul>
              <Notice title="Packaging needs review" tone="warning">
                The initial local runner is validated for the sample’s
                flat-package workflow. Editable installs and some src-layout
                packaging need additional integration; inspect the runtime’s
                supported installation contract before relying on them.
              </Notice>
            </>
          )}
          {section === "evaluation" && (
            <>
              <h2>Keep features and outcome labels separate.</h2>
              <p>
                Your repository’s prediction command receives an unlabeled
                features CSV and emits stable row IDs with positive-class
                probabilities. Upload outcome labels separately through the
                evaluation-pack step.
              </p>
              <h3>Prediction output</h3>
              <pre tabIndex={0} className="r-contract-code">
                customer_id,score{`\n`}C004001,0.37{`\n`}C004002,0.82
              </pre>
              <h3>Evaluation pack</h3>
              <pre tabIndex={0} className="r-contract-code">
                customer_id,churned,region{`\n`}C004001,0,north{`\n`}
                C004002,1,south
              </pre>
              <p>
                The ID column must align one-to-one. The target describes the
                outcome; an optional segment column supports group checks. Tell
                the form the positive label and column names. Missing,
                duplicated or misaligned evidence must not become a successful
                quality claim.
              </p>
              <Notice title="No labels means no measured predictive-quality claim">
                Label-free behavior checks can still be useful. A parity check
                does not establish that ROC AUC improved. A paired confidence
                interval for a metric change is not an interval for the absolute
                metric.
              </Notice>
              <h3>Reading benchmark claims</h3>
              <p>
                All comparison arms must use the same independent evaluator and
                disclosed budgets. The currently bundled recordings are not a
                completed generic-agent comparison, and their provider costs are
                estimates.
              </p>
            </>
          )}
          {section === "handoff" && (
            <>
              <h2>A draft is a handoff, not a release.</h2>
              <p>
                Review the final diff, added regression tests, code checks, ML
                checks and evaluation limitations. A VERIFIED outcome permits a
                reviewable handoff; it does not authorize merging or deploying.
              </p>
              <ol>
                <li>Download the recorded or live patch and receipt JSON.</li>
                <li>
                  Reproduce the software checks from the correct repository
                  revision.
                </li>
                <li>
                  For a verified live run added by URL, provide the destination
                  GitHub repository and base branch.
                </li>
                <li>
                  Use a narrowly scoped token. It stays in transient form state
                  and is cleared after the request.
                </li>
                <li>
                  Open the returned draft PR and complete your normal review
                  process.
                </li>
              </ol>
              <Notice title="Recorded runs cannot create external PRs">
                Historical recordings support inspection and patch downloads.
                They do not hold your repository permissions or trigger live
                delivery.
              </Notice>
              <h3>Sharing</h3>
              <p>
                Publishing a read-only replay can expose the task text, source
                diff, reasoning excerpts and logs. Review for private
                information before confirming publication.
              </p>
            </>
          )}
          {section === "privacy" && (
            <>
              <h2>Your code is sensitive input.</h2>
              <p>
                The hosted hackathon demo uses Nebius Sandboxes beta. Use only
                public code and synthetic or non-sensitive data. Do not upload
                personal data, private datasets or secrets. Sandbox filesystem
                snapshots follow the provider&apos;s retention policy; removing a
                local task does not guarantee immediate provider-side deletion.
              </p>
              <p>
                The repair service receives admitted source code, commands and
                task context. Configured inference providers may receive
                selected code excerpts and diagnostic context. Research services
                may receive relevant, minimized technical queries.
              </p>
              <ul>
                <li>
                  Do not include secrets or environment files in a repository
                  upload.
                </li>
                <li>
                  Outcome labels are uploaded through a separate evaluation-pack
                  path; they are not ordinary candidate inputs.
                </li>
                <li>
                  GitHub delivery tokens are used transiently and are not saved
                  by this UI.
                </li>
                <li>
                  Recordings reveal their original runner and provider. A
                  historical unisolated runner is not represented as an isolated
                  production environment.
                </li>
                <li>
                  Retention periods, deletion rights, provider terms and storage
                  location depend on the operator. This UI does not invent a
                  retention guarantee the server has not reported.
                </li>
              </ul>
              <Notice
                title="Check the deployment before uploading customer data"
                tone="warning"
              >
                Confirm the operator’s isolation, access controls, deletion
                process and retention configuration. Current browser-session
                scoping is not a substitute for organization-grade account
                management.
              </Notice>
              <p>
                Browser storage contains presentation preferences and a session
                identifier. Tokens, evaluation-label contents and repository
                ZIPs are not placed in local storage by this frontend.
              </p>
            </>
          )}
          {section === "faq" && (
            <>
              <h2>Frequently asked questions</h2>
              {[
                [
                  "Why can a green patch be rejected?",
                  "A unit test can miss wrong feature order, leakage, training/serving mismatch or a critical-group regression. Independent ML checks test the declared behavior.",
                ],
                [
                  "Does VERIFIED mean the system is formally proven safe?",
                  "No. It means the configured checks passed for the bound inputs and protocol. Untested situations and missing evidence remain limitations.",
                ],
                [
                  "Are replays simulated repairs?",
                  "The bundled replays come from genuine recorded executions. Playback timing is presentation-only; it never calls a model or executes code.",
                ],
                [
                  "Why does a recording say unisolated?",
                  "The initial experiments used a development process runner. We preserve that provenance instead of assigning them the current target deployment’s guarantees.",
                ],
                [
                  "Can I run without a model key?",
                  "You can inspect bundled recordings without a key. Live runs require an enabled service, an available configured provider and admission under server caps.",
                ],
                [
                  "Can Autopilot merge or deploy for me?",
                  "The normal handoff is a draft PR. Merge and model promotion require separate human authority.",
                ],
                [
                  "Where are teams and organization roles?",
                  "Open Workspace settings to sign in with GitHub, select a workspace, and manage members or CI repository links. Viewers read, members run repairs, admins manage access and links, and owners manage ownership. Teammates must sign in once before they can be added.",
                ],
              ].map(([q, a]) => (
                <details key={q}>
                  <summary>{q}</summary>
                  <p>{a}</p>
                </details>
              ))}
            </>
          )}
        </article>
      </div>
    </>
  );
}
