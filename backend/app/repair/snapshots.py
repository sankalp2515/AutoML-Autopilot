"""Repository intake for real users (BE-03/04/05/06/17).

Flow:  stage (URL+ref | zip | bundled sample)  →  contract (detected, proposed, edited)
       →  discover tests in a sandbox  →  freeze a snapshot at task creation.

Staging is a private local Git repository so every source ends in an exact commit, and a
user-supplied `autopilot.yaml` becomes a local commit on top of the upstream one. The
upstream SHA is kept for delivery. Evaluation packs (holdout labels) are stored where only
the API and the verifier can read them; the worker never mounts them.
"""
from __future__ import annotations

import io
import json
import re
import shutil
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from app.config import settings
from app.repair import contracts as c
from app.repair.intake import IntakeError, git, intake_repository
from app.repair.oracle.grading import fixtures_root, packs_root, snapshots_root
from app.repair.security import check_repo_url, unpinned_requirements

_GIT_ID = ["-c", "user.name=autopilot", "-c", "user.email=autopilot@localhost", "-c", "commit.gpgsign=false"]
_ID = re.compile(r"^[0-9a-f]{12}$")


class StageError(ValueError):
    pass


def staging_root() -> Path:
    p = Path(settings.DATA_DIR, "repair", "staging")
    p.mkdir(parents=True, exist_ok=True)
    return p


def _meta_path(sid: str) -> Path:
    if not _ID.match(sid):
        raise StageError("unknown repository")
    return staging_root() / sid / "meta.json"


def meta(sid: str, tenant: str) -> dict[str, Any]:
    p = _meta_path(sid)
    if not p.is_file():
        raise StageError("unknown repository")
    m = json.loads(p.read_text(encoding="utf-8"))
    if m["tenant"] != tenant:
        raise StageError("unknown repository")
    return m


def _save(sid: str, m: dict) -> None:
    _meta_path(sid).write_text(json.dumps(m, indent=1), encoding="utf-8")


def _limits(repo: Path) -> None:
    files = [p for p in repo.rglob("*") if p.is_file() and ".git" not in p.relative_to(repo).parts]
    if len(files) > settings.REPAIR_MAX_FILES:
        raise StageError(f"repository has more than {settings.REPAIR_MAX_FILES} files")
    if sum(p.stat().st_size for p in files) > settings.REPAIR_MAX_REPO_MB * 1024 * 1024:
        raise StageError(f"repository is larger than {settings.REPAIR_MAX_REPO_MB} MB")
    if any(p.is_symlink() for p in repo.rglob("*")):
        raise StageError("symlinks are not supported")


def _commit_all(repo: Path, message: str) -> str:
    git(["add", "-A"], cwd=repo)
    git([*_GIT_ID, "commit", "-q", "--allow-empty", "-m", message], cwd=repo)
    return git(["rev-parse", "HEAD"], cwd=repo).decode().strip()


def _new(tenant: str, source: dict) -> tuple[str, Path]:
    sid = uuid.uuid4().hex[:12]
    d = staging_root() / sid
    (d / "repo").mkdir(parents=True)
    _save(sid, {"id": sid, "tenant": tenant, "source": source, "upstream_sha": None, "sha": None,
                "sample": source.get("sample")})
    return sid, d / "repo"


def stage_url(tenant: str, url: str, ref: str) -> dict:
    url = check_repo_url(url)
    if not re.fullmatch(r"[A-Za-z0-9._/-]{1,200}", ref) or ".." in ref:
        raise StageError("invalid branch, tag or commit")
    sid, repo = _new(tenant, {"kind": "url", "url": url, "ref": ref})
    try:
        git(["init", "-q"], cwd=repo)
        git(["fetch", "-q", "--depth", "1", "--no-tags", url, ref], cwd=repo, timeout_s=180)
        git(["checkout", "-q", "FETCH_HEAD"], cwd=repo)
        upstream = git(["rev-parse", "HEAD"], cwd=repo).decode().strip()
        _limits(repo)
    except (IntakeError, StageError):
        shutil.rmtree(repo.parent, ignore_errors=True)
        raise
    m = meta(sid, tenant)
    m.update(upstream_sha=upstream, sha=upstream)
    _save(sid, m)
    return describe(sid, tenant)


def stage_zip(tenant: str, data: bytes, name: str) -> dict:
    if len(data) > settings.REPAIR_MAX_REPO_MB * 1024 * 1024:
        raise StageError("archive too large")
    sid, repo = _new(tenant, {"kind": "zip", "name": name[:120]})
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = [n for n in z.namelist() if not n.endswith("/")]
            if len(names) > settings.REPAIR_MAX_FILES:
                raise StageError("archive has too many files")
            prefix = names[0].split("/", 1)[0] + "/" if names and all(n.startswith(names[0].split("/", 1)[0] + "/") for n in names) else ""
            for n in names:
                rel = n[len(prefix):]
                if not rel or rel.startswith("/") or ".." in Path(rel).parts or ":" in rel or "/.git/" in f"/{rel}":
                    continue
                dest = repo / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(z.read(n))
        _limits(repo)
        git(["init", "-q"], cwd=repo)
        sha = _commit_all(repo, "uploaded archive")
    except zipfile.BadZipFile as e:
        shutil.rmtree(repo.parent, ignore_errors=True)
        raise StageError("not a valid zip archive") from e
    except (IntakeError, StageError):
        shutil.rmtree(repo.parent, ignore_errors=True)
        raise
    m = meta(sid, tenant)
    m.update(upstream_sha=sha, sha=sha)
    _save(sid, m)
    return describe(sid, tenant)


def samples() -> list[dict]:
    out = []
    root = fixtures_root()
    for d in sorted(root.iterdir()) if root.is_dir() else []:
        if (d / "repo" / "autopilot.yaml").is_file() and (d / "sealed" / "manifest.yaml").is_file():
            rc = c.RepoContract.from_yaml((d / "repo" / "autopilot.yaml").read_text(encoding="utf-8"))
            readme = (d / "repo" / "README.md").read_text(encoding="utf-8").strip().splitlines() if (d / "repo" / "README.md").is_file() else []
            task = (d / "TASK.txt").read_text(encoding="utf-8").strip() if (d / "TASK.txt").is_file() else \
                "A recent change broke this repository. Make the failing tests pass without breaking the model."
            out.append({"name": d.name, "title": readme[0].lstrip("# ") if readme else d.name, "task": task,
                        "failing_tests": rc.task.fail_to_pass, "invariants": rc.task.ml_invariants})
    return out


def stage_sample(tenant: str, name: str) -> dict:
    src = fixtures_root() / name / "repo"
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name) or not src.is_dir():
        raise StageError("unknown sample")
    sid, repo = _new(tenant, {"kind": "sample", "sample": name})
    shutil.rmtree(repo)
    shutil.copytree(src, repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".git"))
    git(["init", "-q"], cwd=repo)
    sha = _commit_all(repo, f"sample {name}")
    m = meta(sid, tenant)
    m.update(upstream_sha=sha, sha=sha, sample=name)
    _save(sid, m)
    return describe(sid, tenant)


# ── contract ──────────────────────────────────────────────────────────────────
def propose_contract(repo: Path) -> tuple[str, list[str]]:
    """A starting autopilot.yaml from the repository layout, plus the fields a human must confirm."""
    needs = []
    pkgs = sorted(p.parent.name for p in repo.glob("*/__init__.py") if p.parent.name not in ("tests", "test"))
    pkg = pkgs[0] if pkgs else None
    tests = "tests" if (repo / "tests").is_dir() else "test" if (repo / "test").is_dir() else None
    if not pkg:
        needs.append("commands: no importable package found at the top level")
    if not tests:
        needs.append("commands.test: no tests/ folder found")

    def mod(name: str) -> str | None:
        hit = next((p for p in sorted(repo.rglob(f"{name}.py")) if ".git" not in p.parts and "tests" not in p.parts), None)
        return ".".join(hit.relative_to(repo).with_suffix("").parts) if hit else None

    serve = None
    for p in sorted(repo.rglob("*.py")):
        if "tests" in p.parts or ".git" in p.parts:
            continue
        m = re.search(r"^def (score_request|score|predict_one|serve)\(", p.read_text(encoding="utf-8", errors="ignore"), re.M)
        if m:
            serve = f"{'.'.join(p.relative_to(repo).with_suffix('').parts)}:{m.group(1)}"
            break
    csvs = sorted(str(p.relative_to(repo).as_posix()) for p in repo.rglob("*.csv") if ".git" not in p.parts)
    train_m, predict_m = mod("train"), mod("predict")
    doc: dict[str, Any] = {"version": 1, "name": re.sub(r"[^A-Za-z0-9_.-]", "-", repo.parent.name)[:80] or "repo",
                           "python": "3.12", "install": "pip install -r requirements.txt",
                           "commands": {"test": f"pytest -q {tests or 'tests'}"},
                           "task": {"type": "restore", "metric": "roc_auc", "fail_to_pass": [], "ml_invariants": []},
                           "policy": {"allowed_edit_roots": [f"{pkg or 'src'}/", f"{tests or 'tests'}/"],
                                      "immutable": ["data/", "requirements.txt"], "immutable_existing_tests": True}}
    if train_m and predict_m:
        doc["commands"]["train"] = f"python -m {train_m} --data <TRAIN_CSV> --out {{artifacts}}/model.joblib"
        doc["commands"]["predict"] = (f"python -m {predict_m} --model {{artifacts}}/model.joblib "
                                      "--input {holdout_features} --out {predictions}")
        doc["data"] = {"train": csvs[0] if csvs else "data/train.csv",
                       "holdout_features": next((x for x in csvs if "holdout" in x), "data/holdout_features.csv"),
                       "id_column": "<ID_COLUMN>", "target": "<TARGET_COLUMN>", "positive_label": 1,
                       "problem": "binary"}
        doc["task"]["ml_invariants"] = ["holdout_non_inferior"] + (["train_serve_parity"] if serve else [])
        needs += ["commands.train/predict: check the arguments", "data.id_column and data.target",
                  "data.problem (binary | multiclass | regression) and a task.metric that fits it",
                  "data.holdout_features: a CSV of unseen rows without labels"]
    if serve:
        doc["entrypoints"] = {"serve": serve}
    if not (repo / "requirements.txt").is_file():
        needs.append("requirements.txt: add pinned dependencies")
    needs.append("task.fail_to_pass: pick the failing tests after discovery")
    return yaml.safe_dump(doc, sort_keys=False), needs


def describe(sid: str, tenant: str) -> dict:
    m = meta(sid, tenant)
    repo = staging_root() / sid / "repo"
    files = sorted(p.relative_to(repo).as_posix() for p in repo.rglob("*") if p.is_file() and ".git" not in p.relative_to(repo).parts)
    out = {"id": sid, "source": m["source"], "upstream_sha": m["upstream_sha"], "sha": m["sha"],
           "files": files[:500], "file_count": len(files), "unpinned_requirements": unpinned_requirements(repo),
           "sample": m.get("sample")}
    y = repo / "autopilot.yaml"
    if y.is_file():
        text = y.read_text(encoding="utf-8")
        out["contract_yaml"] = text
        out["contract_errors"] = validate_contract(text, repo)
        out["contract_source"] = "repository" if m["sha"] == m["upstream_sha"] else "edited"
    else:
        out["contract_yaml"], out["needs_input"] = propose_contract(repo)
        out["contract_errors"] = ["no autopilot.yaml yet: review the proposal and save it"]
        out["contract_source"] = "proposed"
    out["tests"] = m.get("tests")
    return out


def validate_contract(text: str, repo: Path) -> list[str]:
    try:
        rc = c.RepoContract.from_yaml(text)
    except Exception as e:  # noqa: BLE001 - every problem is reported to the user
        return [str(e)[:800]]
    errs = []
    for k in ("train", "holdout_features"):
        if rc.data and not (repo / getattr(rc.data, k)).is_file():
            errs.append(f"data.{k}: {getattr(rc.data, k)} does not exist in the repository")
    if rc.entrypoints.get("serve"):
        mod = rc.entrypoints["serve"].split(":")[0].replace(".", "/")
        if not ((repo / f"{mod}.py").is_file() or (repo / mod / "__init__.py").is_file()):
            errs.append("entrypoints.serve: module not found")
    if rc.data and (repo / rc.data.holdout_features).is_file():
        cols = pd.read_csv(repo / rc.data.holdout_features, nrows=5).columns
        if rc.data.id_column not in cols:
            errs.append(f"data.id_column {rc.data.id_column!r} is not a column of the holdout features")
        if rc.data.target in cols:
            errs.append("holdout features must not contain the target column")
    bad = unpinned_requirements(repo)
    if bad:
        errs.append(f"requirements.txt must pin exact versions (name==version): {', '.join(bad[:5])}")
    return errs


def save_contract(sid: str, tenant: str, text: str, fail_to_pass: list[str] | None = None) -> dict:
    m = meta(sid, tenant)
    repo = staging_root() / sid / "repo"
    if fail_to_pass is not None:
        doc = yaml.safe_load(text)
        doc.setdefault("task", {})["fail_to_pass"] = fail_to_pass
        text = yaml.safe_dump(doc, sort_keys=False)
    errs = validate_contract(text, repo)
    if errs:
        raise StageError("; ".join(errs))
    y = repo / "autopilot.yaml"
    if not y.is_file() or y.read_text(encoding="utf-8") != text:
        y.write_text(text, encoding="utf-8")
        m["sha"] = _commit_all(repo, "autopilot.yaml (contract for AutoML Autopilot)")
        _save(sid, m)
    return describe(sid, tenant)


# ── test discovery (BE-04) ────────────────────────────────────────────────────
def discover(sid: str, tenant: str, runner) -> dict:
    """Collect tests and run the suite once in a sandbox; failing tests are what the user picks."""
    from app.repair.oracle.code import measure_baseline
    m = meta(sid, tenant)
    repo = staging_root() / sid / "repo"
    if not (repo / "autopilot.yaml").is_file():
        raise StageError("save a contract first")
    rc = c.RepoContract.from_yaml((repo / "autopilot.yaml").read_text(encoding="utf-8"))
    b = measure_baseline(runner, str(repo), rc)
    if not b.parsed:
        raise StageError("the test command did not produce a report; check commands.test and requirements")
    m["tests"] = {"outcomes": b.outcomes, "failing": sorted(k for k, v in b.outcomes.items() if v in ("failed", "error"))}
    _save(sid, m)
    return m["tests"]


# ── freeze at task creation ───────────────────────────────────────────────────
def freeze(sid: str, tenant: str) -> dict:
    """Snapshot the staged commit with the platform's intake (validated, digested)."""
    m = meta(sid, tenant)
    repo = staging_root() / sid / "repo"
    if not (repo / "autopilot.yaml").is_file():
        raise StageError("the repository has no contract yet")
    errs = validate_contract((repo / "autopilot.yaml").read_text(encoding="utf-8"), repo)
    if errs:
        raise StageError("; ".join(errs))
    dest = snapshots_root() / f"{sid}-{m['sha'][:12]}"
    digests = m.setdefault("snapshot_digests", {})
    if not dest.exists():
        snapshots_root().mkdir(parents=True, exist_ok=True)
        digests[m["sha"]] = intake_repository(str(repo), m["sha"], dest).digest
        _save(sid, m)
    digest = digests[m["sha"]]
    rc = c.RepoContract.from_yaml((dest / "autopilot.yaml").read_text(encoding="utf-8"))
    if not rc.task.fail_to_pass and not rc.task.ml_invariants:
        raise StageError("pick at least one failing test (task.fail_to_pass), or declare ML invariants "
                         "for a model-quality regression")
    return {"snapshot_dir": str(dest), "sha": m["sha"], "upstream_sha": m["upstream_sha"], "contract": rc,
            "digest": digest, "sample": m.get("sample"), "source": m["source"]}


# ── evaluation packs (BE-06) ──────────────────────────────────────────────────
def create_pack(tenant: str, data: bytes, *, id_column: str, target: str, positive_label: str,
                segment_column: str | None) -> dict:
    if len(data) > 20 * 1024 * 1024:
        raise StageError("labels file larger than 20 MB")
    try:
        df = pd.read_csv(io.BytesIO(data))
    except Exception as e:  # noqa: BLE001
        raise StageError("labels must be a CSV file") from e
    need = [id_column, target] + ([segment_column] if segment_column else [])
    missing = [x for x in need if x not in df.columns]
    if missing:
        raise StageError(f"labels CSV is missing columns: {', '.join(missing)}")
    if df[id_column].duplicated().any():
        raise StageError("duplicate ids in the labels file")
    if df[target].astype(str).nunique() != 2 or str(positive_label) not in set(df[target].astype(str)):
        raise StageError("the target must have exactly two values including the positive label")
    pid = uuid.uuid4().hex[:12]
    d = packs_root() / pid
    d.mkdir(parents=True)
    df[need].to_csv(d / "labels.csv", index=False)
    (d / "manifest.yaml").write_text(yaml.safe_dump({"version": 1, "labels": "labels.csv", "parity_rows": 200}),
                                     encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"tenant": tenant, "id_column": id_column, "target": target,
                                             "positive_label": str(positive_label), "segment_column": segment_column,
                                             "rows": int(len(df)), "positives": int((df[target].astype(str) == str(positive_label)).sum())}),
                                 encoding="utf-8")
    return {"id": pid, "rows": int(len(df))}


def pack_owner(pid: str) -> str | None:
    p = packs_root() / pid / "meta.json"
    return json.loads(p.read_text(encoding="utf-8"))["tenant"] if _ID.match(pid) and p.is_file() else None


def delete_pack(pid: str, tenant: str) -> None:
    if pack_owner(pid) != tenant:
        raise StageError("unknown evaluation pack")
    shutil.rmtree(packs_root() / pid, ignore_errors=True)
