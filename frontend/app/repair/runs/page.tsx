"use client";
import { useMemo, useState } from "react";
import Link from "next/link";
import {
  duration,
  label,
  money,
  REPLAYS,
  Task,
  totalCost,
  useResource,
} from "@/lib/repair";
import {
  Action,
  Badge,
  Button,
  Empty,
  Field,
  Loading,
  Notice,
  PageHead,
} from "@/components/repair/ui";
type Row = {
  id: string;
  repo: string;
  target: string;
  decision: string;
  cost?: number | null;
  seconds?: number | null;
  recorded: boolean;
};
type PublishedReplay = {
  id: string;
  task_id?: string;
  decision: string;
  cost_usd?: number;
  seconds?: number;
};
export default function Runs() {
  const [query, setQuery] = useState(""),
    [filter, setFilter] = useState("all"),
    [kind, setKind] = useState("all"),
    [limit, setLimit] = useState(25);
  const tasks = useResource<Task[]>(`/tasks?limit=${limit}&offset=0`, 10000);
  const published = useResource<{ replays: PublishedReplay[] }>("/replays");
  const rows = useMemo(
    () =>
      [
        ...(tasks.data || []).map((t) => ({
          id: t.id,
          repo: t.repo_name || t.sample || "Your repository",
          target: t.target,
          decision: t.decision || t.status,
          cost: t.cost_usd,
          seconds: t.seconds,
          recorded: false,
        })),
        ...Object.values(REPLAYS).map((r) => ({
          id: r.replay_id!,
          repo: r.sample || "churn-service",
          target:
            r.decision === "VERIFIED"
              ? "Restore the serving path without changing ML behavior"
              : "Investigate a serving repair that passes tests",
          decision: r.decision,
          cost: totalCost(r),
          seconds: r.seconds,
          recorded: true,
        })),
        ...(published.data?.replays || [])
          .filter(
            (r) =>
              !Object.values(REPLAYS).some(
                (b) => b.replay_id === r.id || b.task_id === r.task_id,
              ),
          )
          .map((r) => ({
            id: r.id,
            repo: "Published recording",
            target: r.task_id || r.id,
            decision: r.decision,
            cost: r.cost_usd,
            seconds: r.seconds,
            recorded: true,
          })),
      ].filter(
        (r) =>
          (kind === "all" || (kind === "replays") === r.recorded) &&
          (filter === "all" || r.decision === filter) &&
          `${r.repo} ${r.target}`.toLowerCase().includes(query.toLowerCase()),
      ),
    [tasks.data, published.data, query, kind, filter],
  );
  return (
    <>
      <PageHead
        title="Every repair. Its evidence intact."
        intro="Return to your live work or inspect a genuine recorded run. Replays never start inference."
        action={
          <Action href="/repair/new" primary icon="plus">
            New repair
          </Action>
        }
      />
      <div className="r-filters">
        <Field label="Find a run">
          <input
            placeholder="Search repository or task"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </Field>
        <Field label="Decision">
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            {[
              "all",
              "VERIFIED",
              "REJECTED",
              "INCONCLUSIVE",
              "FAILED",
              "CANCELLED",
              "CREATED",
            ].map((v) => (
              <option key={v} value={v}>
                {v === "all" ? "All decisions" : label(v)}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Run type">
          <select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="all">Live and recorded</option>
            <option value="live">Your live runs</option>
            <option value="replays">Recorded runs</option>
          </select>
        </Field>
      </div>
      {tasks.error && (
        <div style={{ marginBottom: 20 }}>
          <Notice
            title="Your live history is temporarily unavailable"
            action={<Button onClick={tasks.reload}>Retry history</Button>}
          >
            The bundled genuine recordings remain available below. {tasks.error}
          </Notice>
        </div>
      )}
      {tasks.loading && kind === "live" ? (
        <Loading />
      ) : rows.length ? (
        <div className="r-panel r-table-wrap">
          <table className="r-table">
            <thead>
              <tr>
                <th scope="col">Repository / task</th>
                <th scope="col">Decision</th>
                <th scope="col">Inference estimate</th>
                <th scope="col">Duration</th>
                <th scope="col">Evidence</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r: Row) => (
                <tr key={`${r.recorded}-${r.id}`}>
                  <td>
                    <strong>{r.repo}</strong>
                    <p>{r.target}</p>
                  </td>
                  <td>
                    <Badge value={r.decision} />
                  </td>
                  <td>{money(r.cost)}</td>
                  <td>{duration(r.seconds)}</td>
                  <td>
                    <Link
                      className="r-text-link"
                      href={`/repair/${r.recorded ? "replay" : "tasks"}/${r.id}`}
                    >
                      {r.recorded ? "Open recorded run" : "Open workspace"}
                    </Link>
                    {r.recorded && <p>Recorded execution</p>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <Empty
          title={
            query || filter !== "all"
              ? "No matching repairs"
              : "Your first repair starts here"
          }
          action={
            <Action href="/repair/new" primary>
              Start a repair
            </Action>
          }
        >
          {query || filter !== "all"
            ? "Try a different search or decision filter."
            : "Run a sample repository or connect the ML service you are working on."}
        </Empty>
      )}
      {tasks.data && tasks.data.length >= limit && limit < 100 && (
        <div className="r-actions" style={{ marginTop: 20 }}>
          <Button onClick={() => setLimit(Math.min(100, limit + 25))}>
            Load more runs
          </Button>
        </div>
      )}
    </>
  );
}
