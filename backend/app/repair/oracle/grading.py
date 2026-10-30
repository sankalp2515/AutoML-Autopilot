"""Grader: the verifier-side owner of sealed material, admission records and the holdout ledger.

Used in-process during development and behind the verifier service in production
(oracle/server.py). The worker sends only references (task, contract, snapshot, sealed
reference, candidate diff); labels and sealed tests never leave this side.

    sealed_ref = "fixture:<name>"  bundled sample repository's sealed/ folder
               = "pack:<id>"       evaluation pack uploaded by the user (labels CSV)
               = None              no labels: label-free ML checks only
"""
from __future__ import annotations

import io
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from app.config import settings
from app.repair import contracts as c
from app.repair.oracle import code as code_oracle
from app.repair.oracle import ml as ml_oracle
from app.repair.oracle.ledger import DatabaseLedger, BoundLedger
from app.repair.sandbox.pinned import pinned


class GradingError(ValueError):
    pass


@dataclass(frozen=True)
class GradeRef:
    task_id: str
    ecc: dict
    snapshot_dir: str
    sealed_ref: str | None = None


def _root(*parts: str) -> Path:
    p = Path(settings.REPAIR_VERIFIER_STATE_DIR or Path(settings.DATA_DIR, "verifier"), *parts)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _publish_cache(path: Path, content: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".cache-", dir=path.parent)
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary,path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def cleanup_cache(*, now=None) -> int:
    # Cache is reproducible; durable admissions and holdout permits NEVER expire.
    cutoff=(time.time() if now is None else now)-max(settings.REPAIR_RETENTION_HOURS*3600,
                                                   settings.REPAIR_QUEUE_TTL_S+2400)
    removed=0
    for path in _root("cache").iterdir():
        if path.is_symlink() or not path.is_file():continue
        if path.stat().st_mtime>=cutoff:continue
        if not re.fullmatch(r"[0-9a-f-]{8,64}\.(tests\.json|preds\.csv)",path.name):continue
        path.unlink(missing_ok=True);removed+=1
    return removed


def snapshots_root() -> Path:
    return Path(settings.DATA_DIR, "repair", "snapshots").resolve()


def packs_root() -> Path:
    return Path(settings.REPAIR_PACKS_DIR or Path(settings.DATA_DIR, "repair", "packs")).resolve()


def fixtures_root() -> Path:
    return Path(settings.REPAIR_FIXTURES_DIR or Path(__file__).resolve().parents[4] / "fixtures" / "repos").resolve()


class Grader:
    def __init__(self, runner):
        self.runner = runner
        if settings.REPAIR_LEDGER_DATABASE_URL:
            if not settings.REPAIR_LEDGER_DATABASE_URL.startswith("postgresql+asyncpg://") and not os.environ.get("PYTEST_CURRENT_TEST"):
                raise GradingError("production verifier ledger requires PostgreSQL")
            self.ledger=DatabaseLedger(settings.REPAIR_LEDGER_DATABASE_URL)
            self.ledger.initialize()
        elif runner.isolated and not os.environ.get("PYTEST_CURRENT_TEST"):
            raise GradingError("configure the verifier-only PostgreSQL admission/holdout ledger")
        else:
            self.ledger = ml_oracle.FileHoldoutLedger(str(_root("ledger")))

    # ── resolution ────────────────────────────────────────────────────────────
    def _load(self, ref: GradeRef, *, register=False):
        if not re.fullmatch(r"[0-9a-f-]{8,64}", ref.task_id):
            raise GradingError("bad task id")
        ecc = c.ECC.model_validate(ref.ecc)
        if not ecc.intact() or ecc.task_id != ref.task_id:
            raise GradingError("contract digest or task mismatch")
        base = Path(ref.snapshot_dir).resolve()
        allowed = (snapshots_root(), fixtures_root())
        if not any(base == a or a in base.parents for a in allowed) or not (base / "autopilot.yaml").is_file():
            raise GradingError("snapshot is outside the snapshot store")
        contract = c.RepoContract.from_yaml((base / "autopilot.yaml").read_text(encoding="utf-8"))
        if contract.digest() != ecc.repo_contract_digest:
            raise GradingError("repository contract changed since admission")
        if self.runner.isolated:
            from app.repair.intake import verify_snapshot
            try:
                verify_snapshot(base,ecc.base_commit_sha,ecc.repo_snapshot_digest)
            except ValueError as exc:
                raise GradingError("snapshot differs from admission") from exc
        sealed_dir, manifest = self._sealed(ref.sealed_ref)
        binding=c.canonical_digest({"contract":ecc.contract_digest,"sealed_ref":ref.sealed_ref,
                                    "sealed":self._sealed_digest(sealed_dir)})
        if isinstance(self.ledger,DatabaseLedger):
            from app.repair.sandbox.pinned import immutable_image
            if self.runner.isolated and not immutable_image(ecc.environment.get("image")):
                raise GradingError("admission lacks the prepared dependency image digest")
            try:
                (self.ledger.admit if register else self.ledger.check)(ecc.task_id,binding)
            except PermissionError as exc:
                raise GradingError(str(exc)) from exc
        else:
            self._admit(ecc)
        return ecc, contract, str(base), sealed_dir, manifest

    def register(self,ref):
        self._load(ref,register=True)

    @staticmethod
    def _sealed_digest(directory):
        import hashlib
        if directory is None:return None
        root=Path(directory)
        return c.canonical_digest({p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts})

    def _admit(self, ecc: c.ECC) -> None:
        """First sighting records the contract digest; later requests must match it (W05)."""
        p = _root("admissions") / f"{ecc.task_id}.json"
        try:
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"contract_digest": ecc.contract_digest}, f)
        except FileExistsError:
            if json.loads(p.read_text(encoding="utf-8"))["contract_digest"] != ecc.contract_digest:
                raise GradingError("contract differs from the admission record") from None

    def _sealed(self, sealed_ref: str | None):
        if not sealed_ref:
            return None, None
        kind, _, name = sealed_ref.partition(":")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name):
            raise GradingError("bad sealed reference")
        d = (fixtures_root() / name / "sealed") if kind == "fixture" else (packs_root() / name) if kind == "pack" else None
        if d is None or not (d / "manifest.yaml").is_file():
            raise GradingError("sealed bundle not found")
        return str(d), c.SealedManifest.from_yaml((d / "manifest.yaml").read_text(encoding="utf-8"))

    # ── cached baselines ──────────────────────────────────────────────────────
    def baseline(self, ref: GradeRef) -> code_oracle.Baseline:
        ecc, contract, base, *_ = self._load(ref)
        p = _root("cache") / f"{ref.task_id}.tests.json"
        if p.is_file():
            d = json.loads(p.read_text(encoding="utf-8"))
            p.touch()
            return code_oracle.Baseline(d["outcomes"], d["parsed"])
        b = code_oracle.measure_baseline(pinned(self.runner,ecc), base, contract)
        _publish_cache(p,json.dumps({"outcomes": b.outcomes, "parsed": b.parsed}))
        return b

    def _base_predictions(self, ref: GradeRef, base: str, contract, runner=None) -> pd.DataFrame | None:
        p = _root("cache") / f"{ref.task_id}.preds.csv"
        if p.is_file():
            txt = p.read_text(encoding="utf-8")
            p.touch()
            return None if txt == "NONE" else pd.read_csv(io.StringIO(txt), dtype={"id": str}).set_index("id")
        df = ml_oracle.baseline_predictions(runner or self.runner, base, contract)
        _publish_cache(p,"NONE" if df is None else df.reset_index().to_csv(index=False))
        return df

    # ── verdicts ──────────────────────────────────────────────────────────────
    def code(self, ref: GradeRef, candidate_id: str, diff: str) -> c.CodeReceipt:
        ecc, contract, base, sealed_dir, manifest = self._load(ref)
        return code_oracle.verify(pinned(self.runner,ecc), base_dir=base, contract=contract, ecc=ecc, task_id=ref.task_id,
                                  candidate_id=candidate_id, diff=diff, baseline=self.baseline(ref),
                                  sealed_dir=sealed_dir, manifest=manifest)

    def ml(self, ref: GradeRef, candidate_id: str, diff: str, *, final: bool) -> c.MLReceipt:
        ecc, contract, base, sealed_dir, manifest = self._load(ref)
        runner=pinned(self.runner,ecc)
        base_preds = self._base_predictions(ref, base, contract,runner) if final and manifest else None
        return ml_oracle.verify(runner, base_dir=base, contract=contract, ecc=ecc, task_id=ref.task_id,
                                candidate_id=candidate_id, diff=diff, sealed_dir=sealed_dir, manifest=manifest,
                                ledger=BoundLedger(self.ledger,c.canonical_digest(diff)) if final else None,
                                baseline=base_preds, open_holdout=final)
