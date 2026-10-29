"""Repair agent (card K4): reproduce → competing hypotheses → candidate branches → oracles.

    1. Reproduce: the oracle measures the base commit's test outcomes; the declared failing
       tests are re-run to capture a compact, path-free failure excerpt.
    2. Hypothesise: one Ultra call proposes 2–3 competing root causes with a cheap check each.
    3. Engineer: one sandbox branch per hypothesis (up to ECC.limits.max_candidates). The coder
       tier calls policy-gated tools (Codex C3), one JSON action per turn, under token and
       tool-call budgets. Old turns are folded into a one-line summary to keep prompts small.
    4. Select without the holdout: each candidate gets the code oracle and the label-free ML
       screen. The agent never sees oracle results (no grading feedback loop).
    5. Freeze the smallest eligible diff and open the single-use holdout for it alone.

`mode="generic"` is the comparison arm A: same model, tools and budget, but no ML guidance,
no hypotheses and acceptance by visible tests only. Its final patch is still graded by the
same oracles, so arms differ only in behaviour, never in grading.
"""
from __future__ import annotations

import difflib
import re
import time
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field, ValidationError

from app.repair import contracts as c
from app.repair.agent import prompts
from app.repair.agent.llm import BudgetExceeded, ChatClient, parse_json
from app.repair.oracle import code as code_oracle
from app.repair.oracle import common as cm
from app.repair.oracle import ml as ml_oracle
from app.repair.sandbox.base import SCRATCH, SandboxRunner
from app.repair.tools import ToolExecutor

KEEP_TURNS = 6                  # recent tool exchanges kept verbatim in the prompt
RESULT_CHARS = 3000             # cap per tool result sent to the model
_SKIP_DIRS = {"__pycache__", ".pytest_cache", SCRATCH, ".git"}
_TEXT_SUFFIX = {".py", ".txt", ".md", ".cfg", ".toml", ".yaml", ".yml", ".json", ".ini", ".csv", ".sql", ".sh"}


# ── records ───────────────────────────────────────────────────────────────────
class Hypothesis(BaseModel):
    id: str = Field(pattern=r"^H\d{1,2}$")
    cause: str = Field(max_length=400)
    evidence: str = Field(default="", max_length=600)
    files: list[str] = Field(default_factory=list, max_length=10)
    check: str = Field(default="", max_length=400)
    proposed_by: str = "nemotron"


class Step(BaseModel):
    n: int
    thought: str = ""
    action: str
    target: str = ""
    ok: bool
    denied: str | None = None
    exit_code: int | None = None
    excerpt: str = ""


class CandidateRun(BaseModel):
    candidate: c.Candidate
    steps: list[Step] = Field(default_factory=list)
    stop_reason: str = ""
    diff_lines: int = 0
    code_receipt: c.CodeReceipt | None = None
    screen_receipt: c.MLReceipt | None = None
    eligible: bool = False


class RepairReport(BaseModel):
    task_id: str
    mode: str
    contract_digest: str
    prompt_version: str = prompts.PROMPT_VERSION
    prompt_hash: str = Field(default_factory=prompts.prompt_hash)
    provider: str | None = None
    runner: str
    failure_excerpt: str = ""
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    planner_error: str | None = None
    candidates: list[CandidateRun] = Field(default_factory=list)
    finalist: str | None = None
    final_ml_receipt: c.MLReceipt | None = None
    verdict: c.VerifiedChangeReceipt | None = None
    decision: c.Decision = c.Decision.INCONCLUSIVE
    reasons: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
    calls: list[dict[str, Any]] = Field(default_factory=list)
    events: list[dict[str, Any]] = Field(default_factory=list)
    seconds: float = 0.0


# ── helpers ───────────────────────────────────────────────────────────────────
def compact_failure(text: str, roots: list[str], limit: int = 2500) -> str:
    """Keep the lines that explain a pytest failure; drop absolute paths and noise."""
    text = re.sub(r"(?:[A-Za-z]:)?[\\/][^\s\"']*?[\\/](?=(?:" + "|".join(
        re.escape(r.strip("/")) for r in roots + ["tests"]) + r")[\\/])", "", text)
    keep = []
    for ln in text.splitlines():
        s = ln.strip()
        if (s.startswith(("E ", "E\t", ">", "FAILED", "ERROR")) or "Error" in s or "Exception" in s
                or re.match(r"^[\w./\\-]+\.py:\d+", s)):
            keep.append(ln.rstrip().replace("\\", "/"))
    out = "\n".join(dict.fromkeys(keep)) or text[-limit:]
    return out[-limit:]


def workdir_diff(base_dir: str, work_dir: str, roots: list[str]) -> str:
    """Unified diff (a/ b/ prefixes, git-apply compatible) of text files under the edit roots."""
    base, work = Path(base_dir), Path(work_dir)

    def files(root: Path) -> set[str]:
        out = set()
        for r in roots:
            top = root / r.rstrip("/")
            if top.is_file():
                out.add(r.rstrip("/"))
                continue
            if not top.is_dir():
                continue
            for p in top.rglob("*"):
                if p.is_file() and not (set(p.relative_to(root).parts) & _SKIP_DIRS) and p.suffix in _TEXT_SUFFIX:
                    out.add(p.relative_to(root).as_posix())
        return out

    chunks = []
    for rel in sorted(files(base) | files(work)):
        a, b = base / rel, work / rel
        old = a.read_text(encoding="utf-8").splitlines(keepends=True) if a.is_file() else []
        new = b.read_text(encoding="utf-8").splitlines(keepends=True) if b.is_file() else []
        if old == new:
            continue
        for seq in (old, new):
            if seq and not seq[-1].endswith("\n"):
                seq[-1] += "\n"
        chunks.append("".join(difflib.unified_diff(old, new, f"a/{rel}" if a.is_file() else "/dev/null",
                                                   f"b/{rel}" if b.is_file() else "/dev/null")))
    return "".join(chunks)


def _target(act: Any) -> str:
    for k in ("path", "selector", "kind", "pattern"):
        v = getattr(act, k, None)
        if v:
            return str(v)[:120]
    return ""


def _result_text(res: c.ToolResult, roots: list[str]) -> str:
    if res.denied:
        return f"DENIED: {res.denied}"
    body = res.output or ""
    if res.action == "run":
        head = f"exit_code={res.exit_code}"
        if res.exit_code not in (0, None):
            body = compact_failure(body, roots, RESULT_CHARS - 200) + "\n---\n" + body[-800:]
        else:
            body = body[-1200:]
        return (head + "\n" + body)[:RESULT_CHARS]
    return body[:RESULT_CHARS] + ("\n[truncated]" if res.truncated or len(body) > RESULT_CHARS else "")


# ── the agent ─────────────────────────────────────────────────────────────────
class RepairAgent:
    def __init__(self, runner: SandboxRunner, llm: ChatClient, *, base_dir: str, contract: c.RepoContract,
                 ecc: c.ECC, sealed_dir: str, manifest: c.SealedManifest, ledger: ml_oracle.HoldoutLedger,
                 mode: str = "full", on_event: Callable[[dict], None] | None = None):
        if not hasattr(runner, "work_dir"):
            raise ValueError("the agent's file tools need a runner with a local work directory")
        self.runner, self.llm = runner, llm
        self.base_dir, self.contract, self.ecc = base_dir, contract, ecc
        self.sealed_dir, self.manifest, self.ledger = sealed_dir, manifest, ledger
        self.mode = mode
        self.on_event = on_event
        self.roots = list(ecc.allowed_edit_roots)
        self.ml_required = bool(ecc.success.ml_invariants)
        self.report = RepairReport(task_id=ecc.task_id, mode=mode, contract_digest=ecc.contract_digest,
                                   provider=llm.provider, runner=runner.name)

    def _event(self, type_: str, actor: str, **data) -> None:
        ev = {"seq": len(self.report.events) + 1, "type": type_, "actor": actor, "at": time.time(), **data}
        self.report.events.append(ev)
        if self.on_event:
            self.on_event(ev)

    # 1 reproduce
    def _reproduce(self, base_env: str) -> tuple[code_oracle.Baseline, str, str]:
        baseline = code_oracle.measure_baseline(self.runner, self.base_dir, self.contract)
        f2p = self.ecc.success.fail_to_pass
        r = self.runner.run(base_env, ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", *f2p],
                            timeout_s=self.contract.limits.test_seconds)
        excerpt = compact_failure((r.stdout or "") + "\n" + (r.stderr or ""), self.roots)
        files = ToolExecutor(self.runner.work_dir(base_env), contract=self.contract, ecc=self.ecc)._files(".")
        listing = "\n".join(f for f in files if not f.startswith(SCRATCH))[:4000]
        self.report.failure_excerpt = excerpt
        self._event("baseline.reproduced", "platform", failing=[t for t in f2p
                    if baseline.outcomes.get(t) in ("failed", "error")], collected=len(baseline.outcomes))
        return baseline, excerpt, listing

    def _brief(self, excerpt: str, listing: str) -> str:
        cmds = [k for k in ("test", "train", "predict") if getattr(self.contract.commands, k)]
        brief = (f"TASK: {self.ecc.target}\nFAILING TESTS: {', '.join(self.ecc.success.fail_to_pass)}\n"
                 f"FAILURE OUTPUT (compacted):\n{excerpt}\n\nFILES:\n{listing}\n\n"
                 f"RUNNABLE COMMANDS: {', '.join(cmds)}. EDITABLE: {', '.join(self.roots)}.")
        if self.mode == "full":
            d = self.contract.data
            inv = prompts.invariant_brief(self.ecc.success.ml_invariants, serve=self.contract.entrypoints.get("serve"),
                                          metric=self.ecc.success.metric,
                                          unavailable=d.unavailable_at_prediction if d else [],
                                          segment=d.segment_column if d else None)
            brief += ("\n\n" + inv) if inv else ""
        return brief

    def _finish_gate(self, tools: ToolExecutor, last_edit: int, last_green_suite: int) -> str | None:
        """Full mode only: the agent may not stop before it has added a test and run the whole
        suite green after its last edit. Uses only the agent's own work, never oracle results."""
        if self.mode != "full":
            return None
        new_tests = {p.casefold() for p in tools._files(".") if Path(p).name.startswith("test_")} - tools.existing_tests
        if not new_tests:
            return "no new test file was added."
        if last_green_suite <= last_edit:
            return "the full test suite has not passed since your last edit."
        return None

    # 2 hypothesise
    def _hypotheses(self, brief: str) -> list[Hypothesis]:
        try:
            text = self.llm.complete("ultra", [{"role": "system", "content": prompts.HYPOTHESES_SYSTEM},
                                               {"role": "user", "content": brief}],
                                     max_tokens=1500, purpose="hypotheses")
            hyps = [Hypothesis(**h) for h in parse_json(text)["hypotheses"][:3]]
            if len(hyps) < 1:
                raise ValueError("no hypotheses")
        except (BudgetExceeded, RuntimeError, ValueError, KeyError, TypeError, ValidationError) as e:
            self.report.planner_error = f"{type(e).__name__}: {str(e)[:200]}"
            hyps = [Hypothesis(id="H1", cause="The declared failing test exposes a defect in the code it calls.",
                               evidence="fallback: hypothesis planner unavailable", proposed_by="platform")]
        for h in hyps:
            self._event("hypothesis.proposed", "agent", hypothesis_id=h.id, cause=h.cause, provenance="INFERRED")
        return hyps

    # 3 engineer one candidate
    def _engineer(self, base_env: str, cid: str, brief: str, hyp: Hypothesis | None,
                  others: list[Hypothesis], max_calls: int) -> CandidateRun:
        env = self.runner.fork(base_env)
        work = self.runner.work_dir(env)
        run = CandidateRun(candidate=c.Candidate(candidate_id=cid, task_id=self.ecc.task_id,
                                                 hypothesis_id=hyp.id if hyp else None))
        self._event("candidate.created", "platform", candidate_id=cid, hypothesis_id=run.candidate.hypothesis_id)
        try:
            tools = ToolExecutor(work, contract=self.contract, ecc=self.ecc, runner=self.runner, env_id=env)
            system = prompts.coder_system(self.roots, self.ecc.success.ml_invariants, mode=self.mode)
            task = brief
            if hyp:
                task += (f"\n\nYOUR HYPOTHESIS {hyp.id}: {hyp.cause}\nCheck: {hyp.check}\n"
                         + "".join(f"COMPETING {o.id}: {o.cause}\n" for o in others)
                         + "Check your hypothesis cheaply first. If the evidence refutes it, say so and fix the real cause.")
            turns: list[tuple[str, str]] = []
            invalid = refused = 0
            last_edit = last_green_suite = 0
            for n in range(1, max_calls + 1):
                msgs = [{"role": "system", "content": system}, {"role": "user", "content": task}]
                if len(turns) > KEEP_TURNS:
                    old = "; ".join(f"{i + 1}. {s.action} {s.target} -> {'ok' if s.ok else 'failed'}"
                                    for i, s in enumerate(run.steps[:len(turns) - KEEP_TURNS]))
                    msgs.append({"role": "user", "content": f"EARLIER STEPS (summary): {old}"})
                for a, r in turns[-KEEP_TURNS:]:
                    msgs += [{"role": "assistant", "content": a}, {"role": "user", "content": r}]
                try:
                    text = self.llm.complete("super", msgs, max_tokens=2500, purpose=f"code:{cid}")
                except BudgetExceeded as e:
                    run.stop_reason = f"budget: {e}"
                    self._event("budget.exhausted", "platform", candidate_id=cid, detail=str(e))
                    break
                except RuntimeError as e:
                    run.stop_reason = f"provider: {e}"
                    break
                try:
                    step = c.AgentStep.model_validate(parse_json(text))
                except (ValueError, ValidationError) as e:
                    invalid += 1
                    turns.append((text[:1500], f"INVALID REPLY: {str(e)[:300]}. Reply with one valid JSON action."))
                    run.steps.append(Step(n=n, action="invalid", ok=False, denied=str(e)[:200]))
                    if invalid >= 3:
                        run.stop_reason = "three invalid replies"
                        break
                    continue
                act = step.act
                if act.action == "finish" and refused < 2:
                    why = self._finish_gate(tools, last_edit, last_green_suite)
                    if why:
                        refused += 1
                        msg = prompts.FINISH_REFUSED.format(why=why)
                        run.steps.append(Step(n=n, thought=step.thought[:300], action="finish", ok=False,
                                              denied=msg[:200]))
                        turns.append((step.model_dump_json(), msg))
                        continue
                res = tools.execute(step)
                if act.action == "apply_edit" and res.ok:
                    last_edit = n
                if act.action == "run" and act.kind == "test" and not act.selector and res.ok:
                    last_green_suite = n
                excerpt = _result_text(res, self.roots)
                run.steps.append(Step(n=n, thought=step.thought[:300], action=act.action, target=_target(act),
                                      ok=res.ok, denied=res.denied, exit_code=res.exit_code, excerpt=excerpt[:600]))
                if res.denied:
                    self._event("tool.denied", "platform", candidate_id=cid, action=act.action, rule=res.denied[:160])
                elif act.action == "apply_edit":
                    self._event("patch.applied", "agent", candidate_id=cid, path=act.path)
                elif act.action == "run" and act.kind == "test":
                    self._event("tests.passed" if res.ok else "tests.failed", "sandbox", candidate_id=cid,
                                exit_code=res.exit_code)
                turns.append((step.model_dump_json(), f"RESULT {act.action}: {excerpt}"))
                if act.action == "finish":
                    run.candidate.summary = act.summary
                    run.stop_reason = "finished"
                    break
            else:
                run.stop_reason = "tool-call budget"
            diff = workdir_diff(self.base_dir, work, self.roots)
            run.candidate.diff = diff
            run.candidate.diff_digest = cm.diff_digest(diff)
            changes = cm.parse_diff(diff)
            run.candidate.files_changed = [x.path for x in changes]
            run.diff_lines = sum(len(x.added_lines) + x.removed for x in changes)
            run.candidate.iterations = len(run.steps)
            run.candidate.status = "FROZEN" if diff else "ABANDONED"
        finally:
            self.runner.discard(env)
        return run

    # 4 screen without labels
    def _screen(self, run: CandidateRun, baseline: code_oracle.Baseline) -> None:
        cand = run.candidate
        if not cand.diff:
            return
        run.code_receipt = code_oracle.verify(
            self.runner, base_dir=self.base_dir, contract=self.contract, ecc=self.ecc, task_id=self.ecc.task_id,
            candidate_id=cand.candidate_id, diff=cand.diff, baseline=baseline, sealed_dir=self.sealed_dir,
            manifest=self.manifest)
        ok = run.code_receipt.decision is c.Decision.VERIFIED
        self._event("code_oracle.passed" if ok else "code_oracle.rejected", "code_oracle",
                    candidate_id=cand.candidate_id, reasons=run.code_receipt.reasons)
        if ok and self.ml_required:
            run.screen_receipt = ml_oracle.verify(
                self.runner, base_dir=self.base_dir, contract=self.contract, ecc=self.ecc,
                task_id=self.ecc.task_id, candidate_id=cand.candidate_id, diff=cand.diff,
                sealed_dir=self.sealed_dir, manifest=self.manifest, ledger=None, baseline=None, open_holdout=False)
            ok = not any(ch.status is c.CheckStatus.FAIL for ch in run.screen_receipt.checks) \
                and run.screen_receipt.decision is not c.Decision.SYSTEM_FAILURE
            self._event("ml_oracle.passed" if ok else "ml_oracle.rejected", "ml_oracle",
                        candidate_id=cand.candidate_id, stage="screen", reasons=run.screen_receipt.reasons)
        run.eligible = ok
        if not ok:
            cand.status = "REJECTED"
            self._event("candidate.rejected", "platform", candidate_id=cand.candidate_id)

    def run(self) -> RepairReport:
        t0 = time.time()
        rep = self.report
        self._event("task.created", "platform", contract_digest=self.ecc.contract_digest, mode=self.mode)
        base_env = self.runner.prepare(self.base_dir, python=self.contract.python, install=self.contract.install,
                                       timeout_s=900)
        try:
            baseline, excerpt, listing = self._reproduce(base_env)
            brief = self._brief(excerpt, listing)
            n = self.ecc.limits.max_candidates
            if self.mode == "full":
                hyps = self._hypotheses(brief)
                rep.hypotheses = hyps
                plan = [(h, [o for o in hyps if o.id != h.id]) for h in hyps[:n]]
            else:
                plan = [(None, [])]
            per = max(8, min(30, self.ecc.limits.max_tool_calls // len(plan)))
            for i, (h, others) in enumerate(plan, 1):
                rep.candidates.append(self._engineer(base_env, f"c{i}", brief, h, others, per))
        finally:
            self.runner.discard(base_env)

        for run in rep.candidates:
            self._screen(run, baseline)
        eligible = sorted((r for r in rep.candidates if r.eligible), key=lambda r: r.diff_lines)
        if not eligible:
            rep.decision = c.Decision.REJECTED if any(r.candidate.diff for r in rep.candidates) else c.Decision.INCONCLUSIVE
            rep.reasons = ["E_NO_ELIGIBLE_CANDIDATE" if rep.decision is c.Decision.REJECTED else "E_NO_PATCH"]
        else:
            fin = eligible[0]
            rep.finalist = fin.candidate.candidate_id
            self._event("candidate.frozen", "platform", candidate_id=rep.finalist, diff_digest=fin.candidate.diff_digest)
            final_ml = None
            if self.ml_required:
                base_preds = ml_oracle.baseline_predictions(self.runner, self.base_dir, self.contract)
                final_ml = ml_oracle.verify(
                    self.runner, base_dir=self.base_dir, contract=self.contract, ecc=self.ecc,
                    task_id=self.ecc.task_id, candidate_id=rep.finalist, diff=fin.candidate.diff,
                    sealed_dir=self.sealed_dir, manifest=self.manifest, ledger=self.ledger, baseline=base_preds)
                rep.final_ml_receipt = final_ml
            rep.verdict = c.VerifiedChangeReceipt.combine(fin.code_receipt, final_ml, ml_required=self.ml_required)
            rep.decision, rep.reasons = rep.verdict.decision, rep.verdict.reasons
            fin.candidate.status = "VERIFIED" if rep.decision is c.Decision.VERIFIED else "REJECTED"
            self._event("candidate.verified" if rep.decision is c.Decision.VERIFIED else "candidate.rejected",
                        "ml_oracle", candidate_id=rep.finalist, decision=rep.decision.value)
        rep.usage = self.llm.usage()
        rep.calls = [x.model_dump() for x in self.llm.calls]
        rep.seconds = round(time.time() - t0, 1)
        return rep
