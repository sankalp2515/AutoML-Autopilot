"use client";
import evidence from "@/data/repair/evidence.json";
import { Badge, Notice, Panel } from "./ui";
export type ArmResult = {
  arm: string;
  attempted: number;
  accepted: number;
  verified: number;
  false_accepts: number;
  unresolved_accepts: number;
  verified_rate: number | null;
  false_accept_rate: number | null;
  cost_per_verified: number | null;
  verified_ci95: number[] | null;
};
export type MeasuredStudy = {
  complete: boolean;
  arms: ArmResult[];
  rows: {
    fixture: string;
    arm: string;
    seed: number;
    accepted: boolean;
    external_decision: string;
    error?: string;
  }[];
  limitations: string[];
};
export const study = evidence.comparison as MeasuredStudy | null;
export default function BenchmarkEvidence() {
  return (
    <>
      <Panel
        title="Five independent checker proofs"
        subtitle="Authored wrong/reference patches on synthetic repositories. These are not agent success rates."
      >
        <div
          className="r-table-wrap"
          tabIndex={0}
          role="region"
          aria-label="Fixture checker matrix"
        >
          <table className="r-table">
            <thead>
              <tr>
                <th>Repository</th>
                <th>Wrong patch: software</th>
                <th>Wrong patch: ML</th>
                <th>Reference</th>
                <th>Provenance</th>
              </tr>
            </thead>
            <tbody>
              {evidence.checker_proofs.map((p) => (
                <tr key={p.fixture}>
                  <th scope="row">{p.fixture}</th>
                  <td>
                    <Badge value={p.wrong_code} />
                  </td>
                  <td>
                    <Badge value={p.wrong_ml} />
                  </td>
                  <td>
                    <Badge value={p.reference_ml} />
                  </td>
                  <td>
                    <code>{p.sha256.slice(0, 12)}</code>
                    <p>{p.source}</p>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p>
          All original versions reproduce their declared failure. These proofs
          use the explicit unisolated test runner; they establish checker
          behavior, not hostile-code isolation.
        </p>
      </Panel>
      {evidence.public_subset && (
        <Panel
          title="Published MLOps repository patterns"
          subtitle="Two pinned MLOps-Bench tasks, evaluated with their unchanged public F2P/P2P tests."
        >
          <div
            className="r-table-wrap"
            tabIndex={0}
            role="region"
            aria-label="Public task feasibility"
          >
            <table className="r-table">
              <thead>
                <tr>
                  <th>Task</th>
                  <th>Original new-behavior tests</th>
                  <th>Preserved tests</th>
                  <th>Reference implementation</th>
                </tr>
              </thead>
              <tbody>
                {evidence.public_subset.tasks.map((t) => (
                  <tr key={t.task}>
                    <th scope="row">{t.task}</th>
                    <td>
                      {t.baseline.f2p.passed} passed / {t.baseline.f2p.failed}{" "}
                      failed
                    </td>
                    <td>{t.baseline.p2p.passed} passed</td>
                    <td>
                      {t.reference.f2p.passed + t.reference.p2p.passed} passed
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p>
            Source commit: <code>{evidence.public_subset.source_commit}</code>.{" "}
            <a
              className="r-text-link"
              href={`https://github.com/oracle-samples/mlops-bench/tree/${evidence.public_subset.source_commit}`}
              target="_blank"
              rel="noreferrer"
            >
              Inspect upstream corpus
            </a>
          </p>
          <Notice title="Subset feasibility, not a leaderboard result">
            The tasks are repository-grounded benchmark artifacts, not full
            production repositories. No autonomous repair was measured on these
            tasks. No full-suite or hidden-test result is claimed.
          </Notice>
        </Panel>
      )}
      {study && <Panel title="Measured acceptance and resolution rates" subtitle="Wilson 95% intervals and denominators describe this task set, not all ML software."><div className="r-stack">{study.arms.map(a=><div key={a.arm}><strong>{a.arm}: {a.verified} / {a.attempted} independently verified</strong><meter min={0} max={1} value={a.verified_rate || 0} aria-label={`${a.arm} verified resolution rate`} /><p>{a.verified_ci95 ? `95% interval ${(a.verified_ci95[0]*100).toFixed(1)}–${(a.verified_ci95[1]*100).toFixed(1)}%` : "No trials"}. False acceptance: {a.false_accepts} / {a.accepted} accepted; {a.unresolved_accepts} unresolved.</p></div>)}</div></Panel>}
      {study && (
        <Panel title="Measured matched-budget study">
          <div className="r-table-wrap" tabIndex={0}>
            <table className="r-table">
              <thead>
                <tr>
                  <th>Fixture / seed</th>
                  <th>Arm</th>
                  <th>Accepted</th>
                  <th>Independent result</th>
                </tr>
              </thead>
              <tbody>
                {study.rows.map((r) => (
                  <tr key={`${r.fixture}-${r.seed}-${r.arm}`}>
                    <td>
                      {r.fixture} / {r.seed}
                    </td>
                    <td>{r.arm}</td>
                    <td>{r.accepted ? "Yes" : "No"}</td>
                    <td>{r.error || r.external_decision}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <ul>
            {study.limitations.map((l) => (
              <li key={l}>{l}</li>
            ))}
          </ul>
        </Panel>
      )}
    </>
  );
}
