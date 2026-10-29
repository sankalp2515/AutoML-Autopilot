"use client";
import { useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  api,
  Discovery,
  Sample,
  StagedRepo,
  Status,
  Task,
  useResource,
  VERIFIED_REPLAY,
} from "@/lib/repair";
import {
  Action,
  Badge,
  Button,
  Field,
  Icon,
  Loading,
  Notice,
  PageHead,
  Panel,
} from "./ui";

const invariants = [
  ["train_serve_parity", "Online and batch predictions match"],
  ["holdout_non_inferior", "Quality is preserved on held-out labels"],
  ["holdout_improves", "Quality improves on held-out labels"],
  ["segment_guardrail", "Critical group quality is preserved"],
  ["no_target_leakage", "Post-outcome features do not drive predictions"],
];
type Builder = {
  name: string;
  python: string;
  test: string;
  train: string;
  predict: string;
  serve: string;
  trainData: string;
  features: string;
  id: string;
  target: string;
  roots: string;
  mode: string;
  checks: string[];
};
const initial: Builder = {
  name: "my-ml-service",
  python: "3.12",
  test: "pytest -q tests",
  train:
    "python -m churn.train --data data/train.csv --out {artifacts}/model.joblib",
  predict:
    "python -m churn.predict --model {artifacts}/model.joblib --input {holdout_features} --out {predictions}",
  serve: "churn.serve:score_request",
  trainData: "data/train.csv",
  features: "data/holdout_features.csv",
  id: "customer_id",
  target: "churned",
  roots: "churn/, tests/",
  mode: "restore",
  checks: ["train_serve_parity", "holdout_non_inferior"],
};
const quote = (s: string) => JSON.stringify(s);
function yaml(b: Builder) {
  return `version: 1\nname: ${quote(b.name)}\npython: ${quote(b.python)}\ninstall: "pip install -r requirements.txt"\ncommands:\n  test: ${quote(b.test)}\n${b.train ? `  train: ${quote(b.train)}\n` : ""}${b.predict ? `  predict: ${quote(b.predict)}\n` : ""}${b.serve ? `entrypoints:\n  serve: ${quote(b.serve)}\n` : ""}${b.checks.length ? `data:\n  train: ${quote(b.trainData)}\n  holdout_features: ${quote(b.features)}\n  id_column: ${quote(b.id)}\n  target: ${quote(b.target)}\n  positive_label: 1\n` : ""}task:\n  type: ${b.mode}\n  metric: roc_auc\n  fail_to_pass: []\n  ml_invariants: ${JSON.stringify(b.checks)}\npolicy:\n  allowed_edit_roots: ${JSON.stringify(
    b.roots
      .split(",")
      .map((v) => v.trim())
      .filter(Boolean),
  )}\n  immutable: ["data/", "requirements.txt"]\n  immutable_existing_tests: true\nlimits:\n  test_seconds: 120\n  train_seconds: 120\n`;
}
const validPath = (p: string) =>
  !!p && !p.startsWith("/") && !/[\\:]/.test(p) && !p.split("/").includes("..");

export default function NewRepair() {
  const search = useSearchParams(),
    router = useRouter();
  const [step, setStep] = useState(0),
    [source, setSource] = useState(
      search.get("source") === "url" ? "url" : "sample",
    );
  const [selected, setSelected] = useState("churn-service"),
    [url, setUrl] = useState(""),
    [ref, setRef] = useState("main"),
    [zip, setZip] = useState<File | null>(null);
  const [repo, setRepo] = useState<StagedRepo | null>(null),
    [raw, setRaw] = useState(""),
    [builder, setBuilder] = useState(initial),
    [guided, setGuided] = useState(false);
  const [error, setError] = useState<string | null>(null),
    [busy, setBusy] = useState(false),
    [discovery, setDiscovery] = useState<Discovery | null>(null),
    [chosen, setChosen] = useState<string[]>([]);
  const [target, setTarget] = useState(
      "Restore the failing service without changing valid model behavior.",
    ),
    [labels, setLabels] = useState<File | null>(null),
    [segment, setSegment] = useState(""),
    [positiveLabel, setPositiveLabel] = useState("1"),
    [mode, setMode] = useState("full"),
    [pack, setPack] = useState<string | null>(null),
    [packRows, setPackRows] = useState<number | null>(null),
    [invite, setInvite] = useState("");
  const samples = useResource<{ samples: Sample[] }>("/samples"),
    status = useResource<Status>("/status");
  useEffect(() => {
    if (
      samples.data?.samples.length &&
      !samples.data.samples.some((s) => s.name === selected)
    )
      setSelected(samples.data.samples[0].name);
  }, [samples.data, selected]);
  const contract = guided ? yaml(builder) : raw;
  const sample = samples.data?.samples.find((s) => s.name === selected);
  const errors: string[] = [];
  if (guided) {
    if (!builder.name.match(/^[\w.-]+$/))
      errors.push(
        "Use a repository name with letters, numbers, periods or hyphens.",
      );
    if (!builder.test.trim()) errors.push("Add a test command.");
    if (!builder.roots.split(",").every((p) => validPath(p.trim())))
      errors.push("Editable folders must be relative paths without ..");
    if (
      builder.checks.length &&
      (!builder.train ||
        !builder.predict ||
        !validPath(builder.trainData) ||
        !validPath(builder.features) ||
        !builder.id ||
        !builder.target)
    )
      errors.push(
        "ML checks need training and prediction commands, safe data paths, an ID column and a target.",
      );
    if (builder.checks.includes("train_serve_parity") && !builder.serve)
      errors.push("Prediction parity needs a serving entrypoint.");
  }
  async function stage() {
    setError(null);
    if (source === "url") {
      try {
        const value = new URL(url);
        if (
          value.protocol !== "https:" ||
          value.username ||
          value.password ||
          !["github.com", "gitlab.com"].includes(value.hostname)
        )
          throw Error();
      } catch {
        setError(
          "Use a public GitHub or GitLab HTTPS URL without credentials.",
        );
        return;
      }
    }
    if (
      source === "zip" &&
      (!zip ||
        zip.size > 20 * 1024 * 1024 ||
        !zip.name.toLowerCase().endsWith(".zip"))
    ) {
      setError("Choose a ZIP archive no larger than 20 MB.");
      return;
    }
    setBusy(true);
    try {
      let result: StagedRepo;
      if (source === "zip") {
        const data = new FormData();
        data.append("file", zip!);
        result = await api("/repos/upload", { method: "POST", body: data });
      } else
        result = await api("/repos", {
          method: "POST",
          body: JSON.stringify(
            source === "sample" ? { sample: selected } : { url, ref },
          ),
        });
      setRepo(result);
      setRaw(result.contract_yaml);
      setBuilder({ ...initial, name: result.sample || "my-ml-service" });
      if (sample && source === "sample") setTarget(sample.task);
      setDiscovery(result.tests || null);
      setChosen(result.tests?.failing || sample?.failing_tests || []);
      setStep(1);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    if (errors.length) return;
    setBusy(true);
    setError(null);
    try {
      const result = await api<StagedRepo>(`/repos/${repo!.id}/contract`, {
        method: "PUT",
        body: JSON.stringify({ yaml: contract }),
      });
      setRepo(result);
      setRaw(result.contract_yaml);
      setStep(2);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function discover() {
    setBusy(true);
    setError(null);
    try {
      const d = await api<Discovery>(`/repos/${repo!.id}/discover`, {
        method: "POST",
      });
      setDiscovery(d);
      setChosen(d.failing);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function uploadPack() {
    if (!labels) return;
    if (
      labels.size > 20 * 1024 * 1024 ||
      !labels.name.toLowerCase().endsWith(".csv")
    ) {
      setError("Choose a labels CSV no larger than 20 MB.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const data = new FormData();
      data.append("file", labels);
      data.append("id_column", builder.id);
      data.append("target", builder.target);
      data.append("positive_label", positiveLabel);
      if (segment) data.append("segment_column", segment);
      const result = await api<{ id: string; rows: number }>("/packs", {
        method: "POST",
        body: data,
      });
      setPack(result.id);
      setPackRows(result.rows);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function start() {
    if (target.trim().length < 3) {
      setError("Describe the repair objective in at least 3 characters.");
      return;
    }
    if (labels && !pack) {
      setError(
        "Upload or remove the selected evaluation pack before starting.",
      );
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const task = await api<Task>("/tasks", {
        method: "POST",
        body: JSON.stringify({
          repo_id: repo!.id,
          target,
          fail_to_pass: chosen,
          pack_id: pack,
          mode,
          invite_code: invite || undefined,
        }),
      });
      router.push(`/repair/tasks/${task.id}`);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
      setInvite("");
    }
  }
  const bf = (key: keyof Builder, text: string, hint?: string) => (
    <Field label={text} hint={hint}>
      <input
        value={String(builder[key])}
        onChange={(e) => setBuilder({ ...builder, [key]: e.target.value })}
      />
    </Field>
  );
  return (
    <>
      <PageHead
        title="Start with the problem. Set the boundaries."
        intro="Connect a repository, review what the agent may change, then choose what a successful repair must preserve."
      />
      <ol className="r-stepper">
        {["Source", "Contract", "Task"].map((name, i) => (
          <li key={name} aria-current={step === i ? "step" : undefined}>
            <span>{i < step ? <Icon name="check" size={13} /> : i + 1}</span>
            {name}
          </li>
        ))}
      </ol>
      {error && (
        <div style={{ marginBottom: 20 }}>
          <Notice
            title="We couldn’t complete that step"
            tone="error"
            action={
              <Action href={VERIFIED_REPLAY}>Watch a recorded repair</Action>
            }
          >
            {error}
          </Notice>
        </div>
      )}
      {step === 0 && (
        <div className="r-stack">
          <Panel
            title="Where is your code?"
            subtitle="The repository is pinned to a commit before a repair starts."
          >
            <div
              className="r-tabs"
              role="tablist"
              aria-label="Repository source"
            >
              {[
                ["sample", "Sample repositories"],
                ["url", "Git repository"],
                ["zip", "ZIP upload"],
              ].map(([id, text]) => (
                <button
                  key={id}
                  role="tab"
                  aria-selected={source === id}
                  onClick={() => {
                    setSource(id);
                    setError(null);
                  }}
                >
                  {text}
                </button>
              ))}
            </div>
            {source === "sample" ? (
              samples.loading ? (
                <Loading />
              ) : samples.error ? (
                <Notice
                  title="Sample catalog unavailable"
                  action={
                    <Button onClick={samples.reload}>Retry catalog</Button>
                  }
                >
                  {samples.error}
                </Notice>
              ) : (
                <div className="r-grid-three">
                  {samples.data?.samples.map((s) => (
                    <button
                      key={s.name}
                      className="r-sample"
                      aria-pressed={selected === s.name}
                      onClick={() => setSelected(s.name)}
                    >
                      <Icon name="branch" />
                      <strong>{s.title}</strong>
                      <code>{s.name}</code>
                      <p>{s.task}</p>
                      <span>
                        <Badge>{s.invariants.length} ML checks</Badge>
                      </span>
                    </button>
                  ))}
                </div>
              )
            ) : source === "url" ? (
              <div className="r-form-grid">
                <Field
                  label="Repository HTTPS URL"
                  hint="Public GitHub or GitLab repositories. Never include a password or access token in the URL."
                >
                  <input
                    type="url"
                    placeholder="https://github.com/your-team/ml-service"
                    value={url}
                    onChange={(e) => setUrl(e.target.value)}
                  />
                </Field>
                <Field
                  label="Branch or commit SHA"
                  hint="A branch is resolved to an exact commit during intake."
                >
                  <input value={ref} onChange={(e) => setRef(e.target.value)} />
                </Field>
              </div>
            ) : (
              <div
                className="r-upload"
                onDragOver={(e) => e.preventDefault()}
                onDrop={(e) => {
                  e.preventDefault();
                  setZip(e.dataTransfer.files[0] || null);
                }}
              >
                <Icon name="upload" size={30} />
                <Field
                  label="Repository ZIP"
                  hint="Up to 20 MB in this client; the server may set a lower limit. Do not include secrets or environment files."
                >
                  <input
                    type="file"
                    accept=".zip"
                    onChange={(e) => setZip(e.target.files?.[0] || null)}
                  />
                </Field>
                {zip && (
                  <p>
                    {zip.name} · {(zip.size / 1024 / 1024).toFixed(2)} MB
                  </p>
                )}
              </div>
            )}
            <div className="r-form-footer">
              <small>
                Source code is sent to the configured repair service.{" "}
                <Link
                  className="r-text-link"
                  href="/repair/docs?section=privacy"
                >
                  Read the privacy model
                </Link>
                .
              </small>
              <Button
                kind="primary"
                onClick={stage}
                disabled={busy || (source === "sample" && !sample)}
                icon="arrow"
              >
                {busy ? "Inspecting repository…" : "Continue to contract"}
              </Button>
            </div>
          </Panel>
          <Notice
            title="No failing test, but your model’s metric dropped?"
            action={
              <Action href="/autopilot/new">Investigate a data incident</Action>
            }
          >
            Use the data investigator to compare reference and current data
            windows.
          </Notice>
        </div>
      )}
      {step === 1 && repo && (
        <div className="r-stack">
          <Notice
            title={`${repo.file_count} files inspected · ${repo.sample || repo.source.url || "Uploaded repository"}`}
          >
            <code>Commit {repo.sha.slice(0, 12)}</code> ·{" "}
            {repo.contract_source === "proposed"
              ? "Review the proposed contract carefully."
              : "A repository contract was detected."}
          </Notice>
          <div className="r-grid">
            <Panel
              title="Repair boundaries"
              subtitle="Existing tests and test configuration stay protected."
            >
              <div
                className="r-tabs"
                role="tablist"
                aria-label="Contract editor"
              >
                <button
                  role="tab"
                  aria-selected={!guided}
                  onClick={() => setGuided(false)}
                >
                  Detected YAML
                </button>
                <button
                  role="tab"
                  aria-selected={guided}
                  onClick={() => setGuided(true)}
                >
                  Guided builder
                </button>
              </div>
              {guided ? (
                <div className="r-stack">
                  <Notice title="Builder starts from an example">
                    The fields below are editable example values. Review every
                    command and path for your repository; the detected YAML
                    remains available in the other tab.
                  </Notice>
                  <div className="r-form-grid">
                    {bf("name", "Repository name")}
                    {bf("python", "Python version")}
                    {bf("test", "Test command")}
                    {bf(
                      "roots",
                      "Editable folders",
                      "Comma-separated repository-relative folders.",
                    )}
                    {bf("train", "Training command")}
                    {bf("predict", "Prediction command")}
                    {bf(
                      "serve",
                      "Serving entrypoint",
                      "Example: churn.serve:score_request",
                    )}
                    {bf("trainData", "Training CSV")}
                    {bf(
                      "features",
                      "Holdout features CSV",
                      "No target labels in this file.",
                    )}
                    {bf("id", "Row ID column")}
                    {bf("target", "Target column")}
                    <Field label="Repair objective">
                      <select
                        value={builder.mode}
                        onChange={(e) =>
                          setBuilder({ ...builder, mode: e.target.value })
                        }
                      >
                        <option value="restore">
                          Restore expected behavior
                        </option>
                        <option value="preserve">Preserve behavior</option>
                        <option value="improve">
                          Improve predictive quality
                        </option>
                      </select>
                    </Field>
                  </div>
                  <fieldset>
                    <legend>ML requirements</legend>
                    {invariants.map(([id, text]) => (
                      <label key={id} className="r-checkbox-row">
                        <input
                          type="checkbox"
                          checked={builder.checks.includes(id)}
                          onChange={(e) =>
                            setBuilder({
                              ...builder,
                              checks: e.target.checked
                                ? [...builder.checks, id]
                                : builder.checks.filter((c) => c !== id),
                            })
                          }
                        />
                        {text}
                      </label>
                    ))}
                  </fieldset>
                  {errors.map((e) => (
                    <p key={e} className="r-error" role="alert">
                      {e}
                    </p>
                  ))}
                </div>
              ) : (
                <Field
                  label="autopilot.yaml"
                  hint="Saving validates the contract against the repository. It does not execute a repair."
                >
                  <textarea
                    className="r-yaml"
                    value={raw}
                    onChange={(e) => setRaw(e.target.value)}
                    spellCheck={false}
                  />
                </Field>
              )}
            </Panel>
            <Panel
              title={guided ? "Live YAML preview" : "Repository review"}
              subtitle="Your changes must be validated before you continue."
            >
              {guided ? (
                <pre tabIndex={0} className="r-contract-code">
                  {contract}
                </pre>
              ) : (
                <div className="r-stack">
                  {repo.contract_errors?.length ? (
                    <Notice title="Needs review" tone="warning">
                      {repo.contract_errors.map((e) => (
                        <p key={e}>{e}</p>
                      ))}
                    </Notice>
                  ) : (
                    <Notice title="Detected contract is structurally valid">
                      Review commands, data paths and edit permissions before
                      saving.
                    </Notice>
                  )}
                  <details>
                    <summary>Inspect repository files</summary>
                    <pre tabIndex={0} className="r-contract-code">
                      {repo.files.join("\n")}
                    </pre>
                  </details>
                  {repo.needs_input?.map((n) => (
                    <p key={n}>{n}</p>
                  ))}
                </div>
              )}
            </Panel>
          </div>
          <div className="r-form-footer">
            <Button onClick={() => setStep(0)}>Back to source</Button>
            <Button
              kind="primary"
              disabled={busy || !!errors.length || !contract.trim()}
              onClick={save}
            >
              {busy ? "Validating contract…" : "Validate and continue"}
            </Button>
          </div>
        </div>
      )}
      {step === 2 && repo && (
        <div className="r-stack">
          <div className="r-grid">
            <Panel title="What should the repair achieve?">
              <div className="r-stack">
                <Field label="Task description">
                  <textarea
                    value={target}
                    maxLength={500}
                    onChange={(e) => setTarget(e.target.value)}
                  />
                </Field>
                <div className="r-section-title">
                  <h3>Failing tests</h3>
                  <Button onClick={discover} disabled={busy} icon="search">
                    {busy ? "Working…" : "Discover tests"}
                  </Button>
                </div>
                {discovery ? (
                  <fieldset>
                    <legend>Tests that must pass after repair</legend>
                    {Object.entries(discovery.outcomes).map(([id, outcome]) => (
                      <label key={id} className="r-checkbox-row">
                        <input
                          type="checkbox"
                          checked={chosen.includes(id)}
                          onChange={(e) =>
                            setChosen(
                              e.target.checked
                                ? [...chosen, id]
                                : chosen.filter((v) => v !== id),
                            )
                          }
                        />
                        <span>
                          <code>{id}</code>{" "}
                          <Badge value={outcome === "passed" ? "PASS" : "FAIL"}>
                            {outcome}
                          </Badge>
                        </span>
                      </label>
                    ))}
                  </fieldset>
                ) : (
                  <Notice title="Run test discovery">
                    The service executes the visible test suite in its
                    configured sandbox, then lists test IDs here.{" "}
                    {chosen.length > 0 &&
                      `${chosen.length} failing test ID is already declared in the repository contract.`}
                  </Notice>
                )}
                <Field label="Execution mode">
                  <select
                    value={mode}
                    onChange={(e) => setMode(e.target.value)}
                  >
                    <option value="full">
                      Full Autopilot · independent ML checks
                    </option>
                    <option value="generic">
                      Generic coding agent · comparison baseline
                    </option>
                  </select>
                </Field>
                {mode === "generic" && (
                  <Notice title="Comparison mode" tone="warning">
                    The generic agent does not use the full ML-aware
                    investigation workflow. Independent final checks still
                    determine acceptance.
                  </Notice>
                )}
                <details>
                  <summary>Advanced: execution and spend</summary>
                  <div className="r-stack" style={{ marginTop: 16 }}>
                    <Field
                      label="Maximum candidates"
                      hint="Managed by this server; per-task budget editing is not currently exposed."
                    >
                      <input value="Server policy" readOnly />
                    </Field>
                    <Field label="Token ceiling">
                      <input value="Server policy" readOnly />
                    </Field>
                    <p className="r-muted">
                      {status.data?.daily_cap_usd != null
                        ? `Server-wide daily spend cap: $${status.data.daily_cap_usd}.`
                        : "A cost estimate has not been provided by the service."}{" "}
                      Measured usage appears when the worker publishes it.
                    </p>
                  </div>
                </details>
              </div>
            </Panel>
            <Panel
              title="Independent evaluation"
              subtitle="Labels are uploaded separately from the repository."
            >
              <div className="r-stack">
                <Notice
                  title={
                    repo.sample
                      ? "Sample evaluation pack included"
                      : "Predictive quality needs outcome labels"
                  }
                >
                  {repo.sample
                    ? "This sample has an evaluator-side holdout pack. Upload an alternative only if you intend to replace it."
                    : "Without an evaluation pack, label-free checks can still run. The verdict must say which quality checks could not be evaluated."}
                </Notice>
                <Field
                  label="Holdout labels CSV (optional)"
                  hint="Up to 20 MB. Include row ID and target columns; add a segment column for group checks."
                >
                  <input
                    key={pack || "no-pack"}
                    type="file"
                    accept=".csv"
                    onChange={(e) => {
                      setLabels(e.target.files?.[0] || null);
                    }}
                    disabled={!!pack}
                  />
                </Field>
                <div className="r-form-grid">
                  {bf("id", "Row ID column")}
                  {bf("target", "Target column")}
                  <Field label="Positive label">
                    <input
                      value={positiveLabel}
                      onChange={(e) => setPositiveLabel(e.target.value)}
                    />
                  </Field>
                  <Field label="Segment column (optional)">
                    <input
                      value={segment}
                      onChange={(e) => setSegment(e.target.value)}
                    />
                  </Field>
                </div>
                {pack ? (
                  <Notice
                    title={`Evaluation pack uploaded · ${packRows} rows`}
                    action={
                      <Button
                        onClick={async () => {
                          try {
                            await api(`/packs/${pack}`, { method: "DELETE" });
                            setPack(null);
                            setLabels(null);
                            setPackRows(null);
                          } catch (e) {
                            setError((e as Error).message);
                          }
                        }}
                      >
                        Remove pack
                      </Button>
                    }
                  >
                    Labels remain on the verifier side.
                  </Notice>
                ) : (
                  labels && (
                    <Button onClick={uploadPack} disabled={busy}>
                      Upload evaluation pack
                    </Button>
                  )
                )}
                <Field label="Live-run invite code (if required)">
                  <input
                    type="password"
                    autoComplete="off"
                    value={invite}
                    onChange={(e) => setInvite(e.target.value)}
                  />
                </Field>
              </div>
            </Panel>
          </div>
          {!status.data?.live_runs && (
            <Notice
              title={
                status.error
                  ? "Live service unavailable"
                  : "Live repairs are currently switched off"
              }
              action={
                <Action href={VERIFIED_REPLAY}>
                  Watch a verified recording
                </Action>
              }
            >
              You can inspect the workflow without spending inference credits.
              Enablement and spending caps are controlled by your server.
            </Notice>
          )}
          <div className="r-form-footer">
            <Button onClick={() => setStep(1)}>Back to contract</Button>
            <Button
              kind="primary"
              icon="play"
              disabled={busy || !status.data?.live_runs}
              onClick={start}
            >
              {busy ? "Queuing repair…" : "Start repair"}
            </Button>
          </div>
        </div>
      )}
    </>
  );
}
