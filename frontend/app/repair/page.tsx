"use client";
import Link from "next/link";
import evidence from "@/data/repair/evidence.json";
import {study} from "@/components/repair/BenchmarkEvidence";
import { REPLAYS, VERIFIED_REPLAY, REJECTED_REPLAY } from "@/lib/repair";
import { Action, Badge, Icon } from "@/components/repair/ui";
const wrong = REPLAYS.churn_live_prompt_v1.candidates[0];
const parity = wrong.screen_receipt?.checks.find(
  (c) => c.name === "train_serve_parity",
);
const excerpt = wrong.candidate.diff
  .split("\n")
  .filter((line) => line.startsWith("+") && !line.startsWith("+++"))
  .slice(0, 4);
export default function RepairHome() {
  return (
    <>
      <section className="r-hero">
        <div>
          <h1>
            Green tests.
            <br />
            Proven repairs.
          </h1>
          <p>
            Your ML pipeline deserves more than a patch that passes tests.
            Autopilot investigates the failure, writes a repair, and checks that
            the model still behaves correctly.
          </p>
          <div className="r-actions">
            <Action href={VERIFIED_REPLAY} primary icon="play">
              Watch a verified repair
            </Action>
            <Action href="/repair/new?source=sample" icon="branch">
              Try a sample repo
            </Action>
          </div>
          <p className="r-hero-note">
            Start with a genuine recording. No account, API key or inference
            spend.
          </p>
        </div>
        <div className="r-evidence-stage">
          <div className="r-evidence-title">
            <span>churn-service / serving repair</span>
            <Badge>Recorded evidence</Badge>
          </div>
          <div className="r-code-mini">
            {excerpt.map((line, i) => (
              <div className="r-code-plus" key={i}>
                {line}
              </div>
            ))}
          </div>
          <div className="r-connector">
            <Icon name="arrow" />
          </div>
          <div className="r-proof-row">
            <strong>Software checks</strong>
            <Badge value={wrong.code_receipt?.decision}>Tests passed</Badge>
          </div>
          <div className="r-proof-row">
            <strong>ML behavior</strong>
            <Badge value={wrong.screen_receipt?.decision}>
              Repair rejected
            </Badge>
          </div>
          <div className="r-parity-cells" aria-hidden="true">
            {Array.from({ length: 40 }, (_, i) => (
              <i key={i} />
            ))}
          </div>
          <p style={{ fontSize: 12, marginTop: 14 }}>
            Online and batch scores differed on{" "}
            {String(parity?.evidence.mismatched_rows ?? "the checked")} rows. A
            green test suite missed it.
          </p>
          <div className="r-stage-foot">
            <span>Historical unisolated test runner</span>
            <Link className="r-text-link" href={REJECTED_REPLAY}>
              Inspect the evidence
            </Link>
          </div>
        </div>
      </section>
      {study && <div className="r-section"><p><strong>{study.arms[0].false_accepts} / {study.arms[0].accepted} accepted generic-agent patches</strong> were rejected by independent verification in the measured comparison. {study.arms[0].unresolved_accepts} accepted outcomes remain unresolved. <Link className="r-text-link" href="/repair/benchmark">Inspect the study, denominators and uncertainty</Link></p></div>}
      <div className="r-section">
        <p>
          <strong>
            {
              evidence.checker_proofs.filter(
                (p) => p.wrong_code === "VERIFIED" && p.wrong_ml === "REJECTED",
              ).length
            }{" "}
            / {evidence.checker_proofs.length} authored wrong patches
          </strong>{" "}
          passed software checks and were rejected by ML checks across the added
          fixtures.{" "}
          <Link className="r-text-link" href="/repair/benchmark">
            Inspect checker proofs and public-task evidence
          </Link>
        </p>
        <p className="r-muted">
          Checker validation, not a generic-agent false-accept rate.
        </p>
      </div>
      <section className="r-entry-grid" aria-label="Choose how to start">
        <div className="r-entry">
          <Icon name="play" size={24} />
          <h3>See a repair unfold</h3>
          <p>
            Follow the actual hypotheses, edits and measured results. Pause at
            any decision.
          </p>
          <Action href={VERIFIED_REPLAY}>Watch a verified repair</Action>
        </div>
        <div className="r-entry">
          <Icon name="branch" size={24} />
          <h3>Explore a sample repository</h3>
          <p>
            A small, realistic ML service. You choose the checks; the server
            enforces live-run access and caps.
          </p>
          <Action href="/repair/new?source=sample">Try a sample repo</Action>
        </div>
        <div className="r-entry">
          <Icon name="code" size={24} />
          <h3>Bring the problem you have</h3>
          <p>
            Connect a public repository or upload a ZIP. Review the repair
            boundaries before execution.
          </p>
          <Action href="/repair/new?source=url">Fix your repo</Action>
        </div>
      </section>
      <section className="r-evidence-band">
        <div>
          <strong>2 patches. Green tests. Both rejected.</strong>
          <p>
            Observed in one recorded churn-service investigation. Both failed
            train/serve parity; this is not a generic-agent benchmark.
          </p>
        </div>
        <Action href="/repair/benchmark" icon="chart">
          Explore the results
        </Action>
      </section>
      <section className="r-section">
        <div className="r-section-title">
          <div>
            <h2>From a failing repository to a reviewable repair.</h2>
            <p>Every stage leaves something you can inspect.</p>
          </div>
          <Link href="/repair/how-it-works" className="r-text-link">
            How it works
          </Link>
        </div>
        <div className="r-how-strip">
          {[
            [
              "Understand",
              "Pin the code, identify the failure and freeze the repair boundaries.",
            ],
            [
              "Investigate",
              "Compare explanations against measured diagnostic evidence.",
            ],
            [
              "Repair",
              "Generate source edits and regression tests in candidate branches.",
            ],
            [
              "Verify",
              "Check software behavior and applicable ML requirements independently.",
            ],
            [
              "Review",
              "Inspect the diff and receipts. Decide whether to open a draft PR.",
            ],
          ].map(([title, description], i) => (
            <div key={title} className="r-how-step">
              <span>{i + 1}</span>
              <h3>{title}</h3>
              <p>{description}</p>
            </div>
          ))}
        </div>
      </section>
      <div className="r-trust-strip">
        <span>
          <Icon name="shield" />
          Isolation is disclosed on every run
        </span>
        <span>
          <Icon name="code" />
          Sealed labels stay with the verifier
        </span>
        <span>
          <Icon name="branch" />
          Draft PRs. Never automatic merges.
        </span>
      </div>
    </>
  );
}
