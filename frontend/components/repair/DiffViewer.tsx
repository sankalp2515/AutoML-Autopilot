"use client";
import { useMemo, useState } from "react";
import { Badge, Button, Copy, Empty, Icon } from "./ui";

type Line = { type: string; text: string; old?: number; next?: number };
type File = { name: string; kind: string; lines: Line[] };
function parse(diff: string): File[] {
  const files: File[] = [];
  let file: File | undefined,
    old = 0,
    next = 0,
    previous = "";
  for (const line of diff.split("\n")) {
    if (line.startsWith("--- ")) {
      previous = line.slice(4).replace(/^a\//, "");
      continue;
    }
    if (line.startsWith("+++ ")) {
      const path = line.slice(4).replace(/^b\//, "");
      file = {
        name: path === "/dev/null" ? previous : path,
        kind:
          previous === "/dev/null"
            ? "New file"
            : path === "/dev/null"
              ? "Deleted file"
              : "Modified",
        lines: [],
      };
      files.push(file);
      continue;
    }
    if (!file) continue;
    if (line.startsWith("@@")) {
      const match = line.match(/@@ -(\d+)(?:,\d+)? \+(\d+)/);
      if (match) {
        old = +match[1];
        next = +match[2];
      }
      file.lines.push({ type: "hunk", text: line });
    } else if (line.startsWith("-"))
      file.lines.push({ type: "removed", text: line.slice(1), old: old++ });
    else if (line.startsWith("+"))
      file.lines.push({ type: "added", text: line.slice(1), next: next++ });
    else if (line.startsWith(" "))
      file.lines.push({
        type: "context",
        text: line.slice(1),
        old: old++,
        next: next++,
      });
  }
  return files;
}
function code(text: string, other?: string) {
  if (other != null && other !== text) {
    let start = 0,
      end = 0;
    while (
      start < Math.min(text.length, other.length) &&
      text[start] === other[start]
    )
      start++;
    while (
      end < Math.min(text.length, other.length) - start &&
      text[text.length - 1 - end] === other[other.length - 1 - end]
    )
      end++;
    return (
      <>
        {text.slice(0, start)}
        <mark>{text.slice(start, end ? -end : undefined)}</mark>
        {end ? text.slice(-end) : ""}
      </>
    );
  }
  return text
    .split(
      /(\b(?:def|return|from|import|if|else|class|assert|const|let|True|False|None)\b|"[^"]*"|'[^']*'|#.*)/g,
    )
    .map((token, i) => (
      <span
        key={i}
        className={
          /^(def|return|from|import|if|else|class|assert|const|let|True|False|None)$/.test(
            token,
          )
            ? "r-syntax-key"
            : /^['"]/.test(token)
              ? "r-syntax-string"
              : token.startsWith("#")
                ? "r-syntax-comment"
                : ""
        }
      >
        {token}
      </span>
    ));
}
export default function DiffViewer({
  diff,
  title = "Source changes",
}: {
  diff: string;
  title?: string;
}) {
  const [split, setSplit] = useState(false);
  const files = useMemo(() => parse(diff), [diff]);
  if (!diff.trim())
    return (
      <Empty title="No source changes yet">
        The diff appears after the agent edits a file.
      </Empty>
    );
  return (
    <section className="r-panel r-diff-panel" aria-label={title}>
      <div className="r-panel-head">
        <div>
          <h2>{title}</h2>
          <p>
            {files.length} file{files.length === 1 ? "" : "s"} changed
          </p>
        </div>
        <div className="r-actions">
          <Button aria-pressed={split} onClick={() => setSplit(!split)}>
            {split ? "Unified view" : "Split view"}
          </Button>
          <Copy value={diff} title="Copy diff" />
        </div>
      </div>
      {files.length ? (
        files.map((file, i) => (
          <details key={i} open className="r-diff-file">
            <summary>
              <Icon name="code" size={15} />
              <code>{file.name}</code>
              <Badge>{file.kind}</Badge>
            </summary>
            <div
              className="r-diff-scroll"
              tabIndex={0}
              aria-label={`${file.name} code changes; scroll horizontally if needed`}
            >
              <table className={`r-diff ${split ? "r-diff-split" : ""}`}>
                <caption className="r-sr-only">
                  {file.name}:{" "}
                  {split ? "Old and new versions" : "Unified changes"}
                </caption>
                <thead className="r-sr-only">
                  <tr>
                    {split ? (
                      <>
                        <th>Old line</th>
                        <th>Old source</th>
                        <th>New line</th>
                        <th>New source</th>
                      </>
                    ) : (
                      <>
                        <th>Old line</th>
                        <th>New line</th>
                        <th>Source</th>
                      </>
                    )}
                  </tr>
                </thead>
                <tbody>
                  {file.lines.map((line, n) => {
                    const pair =
                      line.type === "removed" &&
                      file.lines[n + 1]?.type === "added"
                        ? file.lines[n + 1].text
                        : line.type === "added" &&
                            file.lines[n - 1]?.type === "removed"
                          ? file.lines[n - 1].text
                          : undefined;
                    if (split)
                      return (
                        <tr key={n} className={`r-diff-${line.type}`}>
                          <td className="r-line-number">{line.old}</td>
                          <td
                            className={
                              line.type === "removed" ? "r-diff-removed" : ""
                            }
                          >
                            <code>
                              {line.type !== "added"
                                ? code(line.text, pair)
                                : ""}
                            </code>
                          </td>
                          <td className="r-line-number">{line.next}</td>
                          <td
                            className={
                              line.type === "added" ? "r-diff-added" : ""
                            }
                          >
                            <code>
                              {line.type !== "removed"
                                ? code(line.text, pair)
                                : ""}
                            </code>
                          </td>
                        </tr>
                      );
                    return (
                      <tr key={n} className={`r-diff-${line.type}`}>
                        <td className="r-line-number">{line.old}</td>
                        <td className="r-line-number">{line.next}</td>
                        <td>
                          <span aria-hidden="true" className="r-diff-sign">
                            {line.type === "added"
                              ? "+"
                              : line.type === "removed"
                                ? "−"
                                : " "}
                          </span>
                          <code>{code(line.text, pair)}</code>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </details>
        ))
      ) : (
        <pre tabIndex={0} className="r-terminal">
          {diff}
        </pre>
      )}
    </section>
  );
}
