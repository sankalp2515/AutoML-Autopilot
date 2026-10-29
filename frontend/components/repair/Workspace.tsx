"use client";
import { useIdentity } from "@/lib/repair-identity";
import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  api,
  ApiError,
  Candidate,
  details,
  duration,
  Hypothesis,
  label,
  RepairEvent,
  Report,
  REPLAYS,
  REJECTED_REPLAY,
  Step,
  Task,
  terminal,
  VERIFIED_REPLAY,
} from "@/lib/repair";
import {
  Action,
  Badge,
  Button,
  Empty,
  Icon,
  Loading,
  Modal,
  Notice,
  PageHead,
  Panel,
} from "./ui";
import DiffViewer from "./DiffViewer";
import { Budget, Comparison, PRHandoff, ReceiptView } from "./Evidence";

type Frame = { id: string; event?: RepairEvent; step?: Step; cid?: string };
function frames(report: Report): Frame[] {
  const result: Frame[] = [];
  for (const event of report.events) {
    const d = details(event),
      cid = typeof d.candidate_id === "string" ? d.candidate_id : undefined;
    if (
      ["patch.applied", "tests.passed", "tests.failed", "tool.denied"].includes(
        event.type,
      )
    )
      continue;
    result.push({ id: `event-${event.seq}`, event, cid });
    if (event.type === "candidate.created")
      for (const step of report.candidates.find(
        (c) => c.candidate.candidate_id === cid,
      )?.steps || [])
        result.push({ id: `${cid}-step-${step.n}`, step, cid });
  }
  return result;
}
const stageNames = ["Understand", "Investigate", "Repair", "Verify", "Review"];
function stageOf(type: string) {
  return /REVIEW_READY|PR_DRAFTED|SELECTING/.test(type)
    ? 4
    : /candidate.verified|candidate.rejected|candidate.frozen|oracle|VERIFYING/.test(
          type,
        )
      ? 3
      : /candidate.created|PATCHING|tool|patch/.test(type)
        ? 2
        : /hypothesis|DIAGNOSING/.test(type)
          ? 1
          : 0;
}
function normalize(
  raw: Candidate | (Candidate["candidate"] & Partial<Candidate>),
): Candidate {
  if ("candidate" in raw && raw.candidate) return raw as Candidate;
  return {
    candidate: raw as Candidate["candidate"],
    steps: raw.steps || [],
    stop_reason: raw.stop_reason || "",
    diff_lines: raw.diff_lines || 0,
    eligible: !!raw.eligible,
    screen_receipt: raw.screen_receipt,
    skipped_files: raw.skipped_files,
  };
}

export default function Workspace({
  replayId,
  taskId,
}: {
  replayId?: string;
  taskId?: string;
}) {
  const identity = useIdentity();
  const recorded = !!replayId;
  const [report, setReport] = useState<Report | null>(
      replayId && REPLAYS[replayId] ? REPLAYS[replayId] : null,
    ),
    [task, setTask] = useState<Task | null>(null),
    [events, setEvents] = useState<RepairEvent[]>([]),
    [liveCandidates, setLiveCandidates] = useState<Candidate[]>([]);
  const [loading, setLoading] = useState(!report),
    [error, setError] = useState(""),
    [revision, setRevision] = useState(0),
    [playing, setPlaying] = useState(false),
    [cursor, setCursor] = useState(0),
    [speed, setSpeed] = useState("1"),
    [tab, setTab] = useState("activity"),
    [selected, setSelected] = useState("c1"),
    [tour, setTour] = useState<number | null>(null),
    [cancel, setCancel] = useState(false),
    [cancelling, setCancelling] = useState(false);
  useEffect(() => {
    if (!replayId || REPLAYS[replayId]) return;
    let active = true;
    api<Report>(`/replays/${encodeURIComponent(replayId)}`)
      .then((r) => {
        if (active) {
          setReport({ ...r, recorded: true });
          setError("");
        }
      })
      .catch((e) => active && setError((e as Error).message))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [replayId, revision]);
  useEffect(() => {
    if (!taskId) return;
    let active = true,
      busy = false,
      last = 0;
    const poll = async () => {
      if (busy) return;
      busy = true;
      try {
        const t = await api<Task>(`/tasks/${encodeURIComponent(taskId)}`);
        const fresh = await api<RepairEvent[]>(
          `/tasks/${encodeURIComponent(taskId)}/events?after=${last}`,
        );
        const candidates = await api<
          (Candidate | (Candidate["candidate"] & Partial<Candidate>))[]
        >(`/tasks/${encodeURIComponent(taskId)}/candidates`);
        if (!active) return;
        setTask(t);
        if (fresh.length) {
          last = fresh.at(-1)!.seq;
          setEvents((previous) => {
            const ids = new Set(previous.map((e) => e.seq));
            return [...previous, ...fresh.filter((e) => !ids.has(e.seq))];
          });
        }
        setLiveCandidates(candidates.map(normalize));
        setError("");
        if (terminal(t.status)) {
          try {
            const r = await api<Report>(
              `/tasks/${encodeURIComponent(taskId)}/report`,
            );
            if (active) setReport(r);
          } catch (e) {
            if (!(e instanceof ApiError && e.status === 409)) throw e;
          }
        }
      } catch (e) {
        if (active) setError((e as Error).message);
      } finally {
        busy = false;
        if (active) setLoading(false);
      }
    };
    void poll();
    const timer = setInterval(poll, 2500);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [taskId, revision]);
  const allFrames = useMemo(() => (report ? frames(report) : []), [report]);
  const lastFrame = Math.max(0, allFrames.length - 1),
    atEnd = !recorded || cursor >= lastFrame;
  useEffect(() => {
    if (!recorded || !playing) return;
    const timer = setInterval(
      () =>
        setCursor((c) => {
          if (c >= lastFrame) {
            setPlaying(false);
            return c;
          }
          return c + 1;
        }),
      1200 / Number(speed),
    );
    return () => clearInterval(timer);
  }, [recorded, playing, lastFrame, speed]);
  const visible = recorded
    ? allFrames.slice(0, cursor + 1)
    : events.map(
        (e) =>
          ({
            id: `event-${e.seq}`,
            event: e,
            cid:
              typeof details(e).candidate_id === "string"
                ? String(details(e).candidate_id)
                : undefined,
          }) as Frame,
      );
  const seenEvents = visible.flatMap((f) => (f.event ? [f.event] : []));
  const proposedHypotheses: Hypothesis[] = recorded
    ? (report?.hypotheses || []).filter((h) =>
        seenEvents.some(
          (e) =>
            e.type === "hypothesis.proposed" &&
            details(e).hypothesis_id === h.id,
        ),
      )
    : report?.hypotheses ||
      events
        .filter((e) => e.type === "hypothesis.proposed")
        .map((e, i) => {
          const d = details(e);
          return {
            id: String(d.hypothesis_id || `H${i + 1}`),
            cause: String(d.cause || "Hypothesis recorded"),
            evidence: String(d.evidence || ""),
            check: String(d.check || ""),
            files: [],
          };
        });
  const hypotheses = proposedHypotheses.map((h) => ({
    ...h,
    status: recorded && !atEnd ? undefined : h.status,
  }));
  for (const event of seenEvents) {
    const d = details(event);
    if (event.type === "diagnostic.executed" && d.kind === "probe") {
      const h = hypotheses.find((h) => h.id === d.hypothesis_id);
      if (h) h.status = String(d.outcome) as Hypothesis["status"];
    }
    if (event.type === "hypothesis.revised" && Array.isArray(d.order)) {
      const order = d.order as string[];
      hypotheses.sort((a, b) => order.indexOf(a.id) - order.indexOf(b.id));
    }
  }
  const driftEvent = [...seenEvents]
    .reverse()
    .find(
      (e) =>
        e.type === "diagnostic.executed" && details(e).kind === "data_drift",
    );
  const drift = !recorded || atEnd ? report?.data_profile : null;
  const candidates: Candidate[] = recorded
    ? (report?.candidates || [])
        .filter((c) =>
          visible.some(
            (f) =>
              f.event?.type === "candidate.created" &&
              f.cid === c.candidate.candidate_id,
          ),
        )
        .map((c) => {
          const id = c.candidate.candidate_id,
            steps = visible.flatMap((f) =>
              f.cid === id && f.step ? [f.step] : [],
            ),
            engineered = steps.length >= c.steps.length;
          const code = seenEvents.some(
              (e) =>
                e.type.startsWith("code_oracle.") &&
                details(e).candidate_id === id,
            ),
            screen = seenEvents.some(
              (e) =>
                e.type.startsWith("ml_oracle.") &&
                details(e).candidate_id === id,
            );
          return {
            ...c,
            candidate: {
              ...c.candidate,
              diff: engineered ? c.candidate.diff : "",
              summary: engineered ? c.candidate.summary : "",
            },
            steps,
            code_receipt: code ? c.code_receipt : null,
            screen_receipt: screen ? c.screen_receipt : null,
            eligible: screen && c.eligible,
          };
        })
    : report?.candidates || liveCandidates;
  const activeCandidate =
    candidates.find((c) => c.candidate.candidate_id === selected) ||
    candidates[0];
  const points = useMemo(() => {
    const find = (test: (f: Frame) => boolean) => allFrames.findIndex(test);
    return [
      {
        title: "Competing explanations",
        text: "These are model-proposed hypotheses, not verified causes.",
        index: find((f) => f.event?.type === "hypothesis.proposed"),
      },
      {
        title: "An actual tool call",
        text: "Inspect the recorded reasoning excerpt, requested tool and measured result.",
        index: find((f) => !!f.step),
      },
      {
        title: "Software tests passed",
        text: "Green tests establish the checked software behavior. They do not establish ML correctness.",
        index: find((f) => f.event?.type === "code_oracle.passed"),
      },
      {
        title: "Independent ML check",
        text: "The ML checker can reject a test-green candidate. This recording retains its actual outcome.",
        index: find((f) => f.event?.type.startsWith("ml_oracle.") || false),
      },
      {
        title: "The final decision",
        text: "Only the selected finalist receives final label-based grading when applicable.",
        index: lastFrame,
      },
    ].filter((p) => p.index >= 0);
  }, [allFrames, lastFrame]);
  function moveTour(n: number) {
    if (!points[n]) return;
    setPlaying(false);
    setTour(n);
    setCursor(points[n].index);
    setTab(n >= 2 ? "candidates" : "activity");
  }
  const stage = recorded
    ? atEnd
      ? 4
      : Math.max(
          0,
          ...visible.map((f) => (f.step ? 2 : stageOf(f.event?.type || ""))),
        )
    : stageOf(task?.status || "CREATED");
  if (loading)
    return (
      <Loading
        text={
          recorded ? "Opening recorded evidence…" : "Connecting to your repair…"
        }
      />
    );
  if (!report && !task)
    return (
      <div className="r-stack">
        <PageHead title="Repair unavailable" />
        <Notice
          title="We couldn’t open this repair"
          tone="error"
          action={
            <>
              <Button onClick={() => setRevision((v) => v + 1)}>Retry</Button>
              <Action href={VERIFIED_REPLAY}>Watch a verified recording</Action>
            </>
          }
        >
          {error || "The task or recording has not been published."}
        </Notice>
      </div>
    );
  return (
    <>
      <PageHead
        title={
          recorded
            ? "A repair you can inspect."
            : task?.target || "Repair workspace"
        }
        intro={
          recorded
            ? `${report?.sample || "ML repository"} · Recorded execution · ${duration(report?.seconds)}`
            : "Live events are persisted by the worker. Verification remains independent."
        }
        action={
          <>
            {recorded ? (
              <Badge>Recorded run · no inference spend</Badge>
            ) : (
              <Badge value={task?.status} />
            )}
            <Action href="/repair/runs">All runs</Action>
            {!recorded &&
              identity.canWrite &&
              !terminal(task?.status || "") && (
                <Button kind="danger" onClick={() => setCancel(true)}>
                  Cancel repair
                </Button>
              )}
          </>
        }
      />
      {error && (
        <div style={{ marginBottom: 20 }}>
          <Notice
            title={
              task
                ? "Reconnecting to the repair service"
                : "Could not load evidence"
            }
            tone="warning"
            action={
              <Button onClick={() => setRevision((v) => v + 1)}>
                Retry connection
              </Button>
            }
          >
            {error} Existing evidence remains visible.
          </Notice>
        </div>
      )}
      {recorded && report && (
        <div className="r-stack" style={{ marginBottom: 20 }}>
          <Notice title="Genuine historical recording">
            Provider: {report.provider || "not reported"}. Runner:{" "}
            {report.runner}.{" "}
            {report.runner === "process-unisolated"
              ? "This was a development run without container isolation."
              : ""}{" "}
            Playback does not execute code or call a model.
          </Notice>
          <div className="r-replay-bar">
            <Button
              icon={playing ? "pause" : "play"}
              kind="primary"
              onClick={() => {
                if (cursor >= lastFrame) setCursor(0);
                setPlaying(!playing);
                setTour(null);
              }}
            >
              {playing ? "Pause" : "Play recording"}
            </Button>
            <input
              type="range"
              min={0}
              max={lastFrame}
              value={cursor}
              aria-label="Replay timeline"
              onChange={(e) => {
                setPlaying(false);
                setCursor(+e.target.value);
                setTour(null);
              }}
            />
            <small>
              Step {cursor + 1} / {allFrames.length}
            </small>
            <label className="r-checkbox-row">
              Speed
              <select
                aria-label="Playback speed"
                value={speed}
                onChange={(e) => setSpeed(e.target.value)}
              >
                {["0.5", "1", "2", "4"].map((v) => (
                  <option key={v} value={v}>
                    {v}×
                  </option>
                ))}
              </select>
            </label>
            <Button onClick={() => moveTour(0)}>Guided tour</Button>
          </div>
          {tour !== null && (
            <div className="r-tour">
              <div>
                <strong>
                  {tour + 1} of {points.length}: {points[tour].title}
                </strong>
                <p>{points[tour].text}</p>
              </div>
              <div className="r-actions">
                {tour > 0 && (
                  <Button onClick={() => moveTour(tour - 1)}>
                    Previous stop
                  </Button>
                )}
                {tour < points.length - 1 ? (
                  <Button kind="primary" onClick={() => moveTour(tour + 1)}>
                    Next stop
                  </Button>
                ) : (
                  <Button
                    kind="primary"
                    onClick={() => {
                      setCursor(lastFrame);
                      setTab("verdict");
                      setTour(null);
                    }}
                  >
                    Open receipt
                  </Button>
                )}
                <Button
                  kind="quiet"
                  onClick={() => setTour(null)}
                  aria-label="Close tour"
                  icon="close"
                />
              </div>
            </div>
          )}
        </div>
      )}
      {!recorded &&
        ["FAILED", "CANCELLED", "BLOCKED"].includes(task?.status || "") && (
          <div style={{ marginBottom: 20 }}>
            <Notice
              title={
                task?.status === "FAILED"
                  ? "The repair stopped without a verified outcome"
                  : task?.status === "CANCELLED"
                    ? "Repair cancelled"
                    : "The repair is blocked"
              }
              tone="warning"
              action={<Action href="/repair/new">Start another repair</Action>}
            >
              {task?.error || "No approval to merge or release is implied."}
            </Notice>
          </div>
        )}
      <div className="r-stage-line" tabIndex={0} aria-label="Repair stages">
        {stageNames.map((name, i) => (
          <div
            key={name}
            className={stage >= i ? "done" : ""}
            aria-current={stage === i ? "step" : undefined}
          >
            <span>{stage > i ? <Icon name="check" size={12} /> : i + 1}</span>
            {name}
          </div>
        ))}
      </div>
      <div
        className="r-tabs"
        role="tablist"
        aria-label="Repair workspace views"
      >
        {[
          ["activity", "Activity"],
          ["candidates", "Candidates"],
          ["verdict", "Verdict & receipt"],
          ["pr", "PR handoff"],
        ].map(([id, title]) => (
          <button
            role="tab"
            key={id}
            aria-selected={tab === id}
            onClick={() => {
              setTab(id);
              if (recorded && ["verdict", "pr"].includes(id)) {
                setPlaying(false);
                setCursor(lastFrame);
              }
            }}
          >
            {title}
          </button>
        ))}
      </div>
      {tab === "activity" && (
        <div className="r-stack">
          <div className="r-work-grid">
            <Panel
              title="Agent activity"
              subtitle={
                recorded
                  ? "Recorded events and tool-step order; playback pauses are presentation timing."
                  : "Waiting for the worker’s persisted events; reasoning excerpts appear only when published."
              }
              action={<Badge>{visible.length} items</Badge>}
            >
              <div className="r-activity">
                {visible.map((f) =>
                  f.step ? (
                    <div className="r-event" key={f.id}>
                      <span>
                        {f.cid}/{f.step.n}
                      </span>
                      <div>
                        <strong>{label(f.step.action)}</strong>{" "}
                        <Badge
                          value={
                            f.step.denied
                              ? "warning"
                              : f.step.ok
                                ? "PASS"
                                : "FAIL"
                          }
                        >
                          {f.step.denied
                            ? "Denied"
                            : f.step.ok
                              ? "Completed"
                              : "Not successful"}
                        </Badge>
                        {f.step.thought && (
                          <details>
                            <summary>Reasoning excerpt · inferred</summary>
                            <p>{f.step.thought}</p>
                          </details>
                        )}
                        <p>
                          <code>{f.step.target}</code>
                        </p>
                        {f.step.denied && (
                          <Notice
                            title="Policy denied this action"
                            tone="warning"
                          >
                            {f.step.denied}
                          </Notice>
                        )}
                        <details>
                          <summary>
                            Tool result
                            {f.step.exit_code != null
                              ? ` · exit ${f.step.exit_code}`
                              : ""}
                          </summary>
                          <pre tabIndex={0}>
                            {f.step.excerpt || "No result excerpt recorded."}
                          </pre>
                        </details>
                      </div>
                    </div>
                  ) : (
                    <div className="r-event" key={f.id}>
                      <span>{f.event?.seq}</span>
                      <div>
                        <strong>
                          {label(f.event?.type.replaceAll(".", " "))}
                        </strong>{" "}
                        <Badge>{f.event?.actor.replaceAll("_", " ")}</Badge>
                        <p>
                          {String(
                            details(f.event!).cause ||
                              details(f.event!).detail ||
                              details(f.event!).reason ||
                              (f.cid
                                ? `Candidate ${f.cid}`
                                : "Persisted platform event"),
                          )}
                        </p>
                        <details>
                          <summary>Inspect event record</summary>
                          <pre tabIndex={0}>
                            {JSON.stringify(f.event, null, 2)}
                          </pre>
                        </details>
                      </div>
                    </div>
                  ),
                )}
              </div>
              {!visible.length && (
                <p>No events yet. The worker will publish progress here.</p>
              )}
            </Panel>
            <div className="r-stack">
              <Panel
                title="Competing hypotheses"
                subtitle="A proposal becomes supported only through evidence."
              >
                {hypotheses.length ? (
                  hypotheses.map((h) => (
                    <div className="r-hypothesis" key={h.id}>
                      <div>
                        <strong>{h.id}</strong>
                        <Badge
                          value={
                            h.status === "confirmed"
                              ? "PASS"
                              : h.status === "refuted"
                                ? "FAIL"
                                : undefined
                          }
                        >
                          {h.status ? `Probe ${h.status}` : "Inferred"}
                        </Badge>
                      </div>
                      <p>{h.cause}</p>
                      {h.evidence && (
                        <p>
                          <strong>Basis:</strong> {h.evidence}
                        </p>
                      )}
                      {h.check && (
                        <p>
                          <strong>Proposed check:</strong> {h.check}
                        </p>
                      )}
                    </div>
                  ))
                ) : (
                  <p>The investigator has not published a hypothesis yet.</p>
                )}
              </Panel>
              {(drift?.flagged?.length || driftEvent) && (
                <Panel title="Data drift findings">
                  <p>
                    Distribution changes guide investigation; they do not
                    establish the cause.
                  </p>
                  {drift?.flagged?.length ? (
                    drift.flagged.map((f) => (
                      <p key={f.column}>
                        <strong>{f.column}</strong> · PSI{" "}
                        {f.psi?.toFixed(3) ?? "not reported"} · missing-value
                        shift {f.missing_shift?.toFixed(3) ?? "not reported"}
                      </p>
                    ))
                  ) : (
                    <p>{JSON.stringify(details(driftEvent!).flagged)}</p>
                  )}
                </Panel>
              )}
              <Budget report={atEnd ? report : null} />
            </div>
          </div>
          <Panel
            title="Candidate source & terminal"
            subtitle="Final source diffs appear only after that candidate’s recorded engineering steps."
          >
            <div
              className="r-tabs"
              role="tablist"
              aria-label="Candidate source"
            >
              {candidates.map((c) => (
                <button
                  role="tab"
                  key={c.candidate.candidate_id}
                  aria-selected={
                    activeCandidate?.candidate.candidate_id ===
                    c.candidate.candidate_id
                  }
                  onClick={() => setSelected(c.candidate.candidate_id)}
                >
                  {c.candidate.candidate_id}
                </button>
              ))}
            </div>
            {activeCandidate ? (
              <div className="r-stack">
                <DiffViewer diff={activeCandidate.candidate.diff} />
                <h3>Last published tool result</h3>
                <pre tabIndex={0} className="r-terminal">
                  {activeCandidate.steps.at(-1)?.excerpt ||
                    "No terminal output has been published yet."}
                </pre>
              </div>
            ) : (
              <p>No candidate has been created yet.</p>
            )}
          </Panel>
        </div>
      )}
      {tab === "candidates" &&
        (candidates.length ? (
          <div className="r-stack">
            <Comparison candidates={candidates} />
            {recorded && replayId !== "churn_live_prompt_v1" && (
              <Notice
                title="Want to inspect a green-but-wrong patch?"
                action={
                  <Action href={REJECTED_REPLAY}>
                    Open the earlier rejected recording
                  </Action>
                }
              >
                The rejection is in a different genuine recording. These
                outcomes are not combined into a fabricated run.
              </Notice>
            )}
          </div>
        ) : (
          <Empty title="Candidates have not been published yet">
            The coding stage will create independent proposals here.
          </Empty>
        ))}
      {tab === "verdict" &&
        (report && atEnd ? (
          <ReceiptView report={report} taskId={taskId} recorded={recorded} />
        ) : (
          <Notice title="The final receipt is not available yet">
            A running or stopped task is not a verified repair. Continue
            watching the activity stream.
          </Notice>
        ))}
      {tab === "pr" &&
        (report && atEnd ? (
          <PRHandoff report={report} taskId={taskId} recorded={recorded} />
        ) : (
          <Notice title="No verified finalist to hand off yet">
            PR delivery becomes available after independent final grading.
          </Notice>
        ))}
      {cancel && (
        <Modal title="Cancel this repair?" onClose={() => setCancel(false)}>
          <div className="r-stack">
            <p>
              The service will revoke worker authority and stop accepting new
              results. This does not undo any external work you already
              approved.
            </p>
            <div className="r-actions">
              <Button onClick={() => setCancel(false)}>Keep running</Button>
              <Button
                kind="danger"
                disabled={cancelling}
                onClick={async () => {
                  setCancelling(true);
                  try {
                    const t = await api<Task>(`/tasks/${taskId}/cancel`, {
                      method: "POST",
                      body: JSON.stringify({ version: task?.version }),
                    });
                    setTask(t);
                    setCancel(false);
                    setRevision((v) => v + 1);
                  } catch (e) {
                    setError((e as Error).message);
                    setCancel(false);
                  } finally {
                    setCancelling(false);
                  }
                }}
              >
                {cancelling ? "Cancelling…" : "Confirm cancellation"}
              </Button>
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}
