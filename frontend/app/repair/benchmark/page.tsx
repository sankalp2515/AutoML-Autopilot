"use client";
import { useState } from "react";
import Link from "next/link";
import { money, REPLAYS, totalCost } from "@/lib/repair";
import { Action, Badge, Notice, PageHead, Panel } from "@/components/repair/ui";
export default function Benchmark() {
  const [tab, setTab] = useState("repair");
  const recordings = Object.values(REPLAYS),
    rejected = REPLAYS.churn_live_prompt_v1;
  const green = rejected.candidates.filter(
      (c) => c.code_receipt?.decision === "VERIFIED",
    ),
    invalid = green.filter((c) =>
      c.screen_receipt?.checks.some((check) => check.status === "FAIL"),
    );
  return (
    <>
      <PageHead
        title="Results you can trace back to a run."
        intro="Measured evidence, clear denominators and visible limitations. No leaderboard claims without a comparable evaluation."
      />
      <div className="r-tabs" role="tablist" aria-label="Benchmark mode">
        <button
          role="tab"
          aria-selected={tab === "repair"}
          onClick={() => setTab("repair")}
        >
          Repository repair
        </button>
        <button
          role="tab"
          aria-selected={tab === "incident"}
          onClick={() => setTab("incident")}
        >
          Data incident benchmark
        </button>
      </div>
      {tab === "incident" ? (
        <Panel title="The earlier data-investigation evaluation">
          <p>
            V4 compares incident diagnostics and corrective decisions. It does
            not measure repository patch resolution, and its cost units are not
            inference dollars.
          </p>
          <div className="r-actions" style={{ marginTop: 20 }}>
            <Action href="/autopilot/benchmark">
              Open data incident results
            </Action>
          </div>
        </Panel>
      ) : (
        <div className="r-stack">
          <Panel
            title="When passing tests was not enough"
            subtitle="One recorded churn-service investigation; two candidate patches."
          >
            <div className="r-metric-row">
              <div>
                <strong>
                  {invalid.length} / {green.length}
                </strong>
                <span>Test-green candidates rejected by ML screening</span>
              </div>
              <div>
                <strong>200</strong>
                <span>Rows checked for online / batch consistency</span>
              </div>
            </div>
            <div className="r-outcome-bars">
              <div>
                <span>Software checks</span>
                <meter
                  min={0}
                  max={green.length}
                  value={green.length}
                  aria-label={`${green.length} candidates passed software checks`}
                />
                <strong>{green.length} passed</strong>
              </div>
              <div>
                <span>ML screening</span>
                <meter
                  min={0}
                  max={green.length}
                  value={invalid.length}
                  aria-label={`${invalid.length} candidates failed ML screening`}
                />
                <strong>{invalid.length} rejected</strong>
              </div>
            </div>
            <p>
              This measures a code-only false-positive risk within this
              recording. It is not a measured false-accept rate for a generic
              coding-agent baseline.
            </p>
            <div className="r-actions" style={{ marginTop: 18 }}>
              <Action href="/repair/replay/churn_live_prompt_v1">
                Inspect every rejected patch
              </Action>
            </div>
          </Panel>
          <Notice
            title="The matched-budget comparison is not published yet"
            tone="warning"
          >
            We have genuine full-system recordings, but no completed A / D /
            no-ML / no-Tavily study. Unmeasured results stay unmeasured.
          </Notice>
          <Panel
            title="Evaluation arms"
            subtitle="Each arm must be scored by the same independent checkers."
          >
            <div className="r-table-wrap" tabIndex={0}>
              <table className="r-table">
                <thead>
                  <tr>
                    <th>Arm</th>
                    <th>Changed capability</th>
                    <th>Verified resolution</th>
                    <th>False acceptance</th>
                    <th>Cost / verified fix</th>
                  </tr>
                </thead>
                <tbody>
                  {[
                    ["A · Generic coding agent", "Visible-tests acceptance"],
                    [
                      "D · Full Autopilot",
                      "ML-aware investigation and verification",
                    ],
                    ["D minus ML checks", "Internal ML acceptance disabled"],
                    ["D minus Tavily", "External research disabled"],
                  ].map(([arm, scope]) => (
                    <tr key={arm}>
                      <td>{arm}</td>
                      <td>{scope}</td>
                      <td>Not measured</td>
                      <td>Not measured</td>
                      <td>Not measured</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
          <Panel
            title="Available recorded evidence"
            subtitle="Different prompt versions on one seeded fault, not independent benchmark families."
          >
            <div className="r-table-wrap" tabIndex={0}>
              <table className="r-table">
                <thead>
                  <tr>
                    <th>Recording</th>
                    <th>Fixture / fault class</th>
                    <th>Decision</th>
                    <th>Inference estimate</th>
                    <th>Inspect</th>
                  </tr>
                </thead>
                <tbody>
                  {recordings.map((r) => (
                    <tr key={r.task_id}>
                      <td>{r.replay_id}</td>
                      <td>churn-service / train–serve mismatch</td>
                      <td>
                        <Badge value={r.decision} />
                      </td>
                      <td>{money(totalCost(r))}</td>
                      <td>
                        <Link
                          className="r-text-link"
                          href={`/repair/replay/${r.replay_id}`}
                        >
                          Open evidence
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
          <div className="r-grid">
            <Panel title="Failures stay in the report">
              <ul>
                <li>
                  Both first-version candidates passed software checks and
                  failed parity.
                </li>
                <li>
                  The verified recording used a historical unisolated
                  development runner.
                </li>
                <li>
                  Hypotheses can be wrong; the code and measurements must
                  decide.
                </li>
                <li>
                  Repeated trials and held-out repository families are still
                  needed.
                </li>
              </ul>
            </Panel>
            <Panel title="Reading the cost numbers">
              <p>
                The verified recording reports{" "}
                {money(totalCost(REPLAYS.churn_live_prompt_v2))} in estimated
                inference cost. It excludes local compute and storage, and is
                not a billed-dollar or population-wide cost-per-fix claim.
              </p>
              <div className="r-actions" style={{ marginTop: 18 }}>
                <Action href="/repair/docs?section=evaluation">
                  Evaluation format & methodology
                </Action>
              </div>
            </Panel>
          </div>
        </div>
      )}
    </>
  );
}
