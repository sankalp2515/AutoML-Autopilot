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
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from app.config import settings
from app.repair import contracts as c
from app.repair.oracle import code as code_oracle
from app.repair.oracle import ml as ml_oracle


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


def snapshots_root() -> Path:
    return Path(settings.DATA_DIR, "repair", "snapshots").resolve()


def packs_root() -> Path:
    return Path(settings.REPAIR_PACKS_DIR or Path(settings.DATA_DIR, "repair", "packs")).resolve()


def fixtures_root() -> Path:
    return Path(settings.REPAIR_FIXTURES_DIR or Path(__file__).resolve().parents[4] / "fixtures" / "repos").resolve()


class Grader:
    def __init__(self, runner):
        self.runner = runner
        self.ledger = ml_oracle.FileHoldoutLedger(str(_root("ledger")))

    # ── resolution ────────────────────────────────────────────────────────────
    def _load(self, ref: GradeRef):
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
        self._admit(ecc)
        sealed_dir, manifest = self._sealed(ref.sealed_ref)
        return ecc, contract, str(base), sealed_dir, manifest

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
            return code_oracle.Baseline(d["outcomes"], d["parsed"])
        b = code_oracle.measure_baseline(self.runner, base, contract)
        p.write_text(json.dumps({"outcomes": b.outcomes, "parsed": b.parsed}), encoding="utf-8")
        return b

    def _base_predictions(self, ref: GradeRef, base: str, contract) -> pd.DataFrame | None:
        p = _root("cache") / f"{ref.task_id}.preds.csv"
        if p.is_file():
            txt = p.read_text(encoding="utf-8")
            return None if txt == "NONE" else pd.read_csv(io.StringIO(txt), dtype={"id": str}).set_index("id")
        df = ml_oracle.baseline_predictions(self.runner, base, contract)
        p.write_text("NONE" if df is None else df.reset_index().to_csv(index=False), encoding="utf-8")
        return df

    # ── verdicts ──────────────────────────────────────────────────────────────
    def code(self, ref: GradeRef, candidate_id: str, diff: str) -> c.CodeReceipt:
        ecc, contract, base, sealed_dir, manifest = self._load(ref)
        return code_oracle.verify(self.runner, base_dir=base, contract=contract, ecc=ecc, task_id=ref.task_id,
                                  candidate_id=candidate_id, diff=diff, baseline=self.baseline(ref),
                                  sealed_dir=sealed_dir, manifest=manifest)

    def ml(self, ref: GradeRef, candidate_id: str, diff: str, *, final: bool) -> c.MLReceipt:
        ecc, contract, base, sealed_dir, manifest = self._load(ref)
        base_preds = self._base_predictions(ref, base, contract) if final and manifest else None
        return ml_oracle.verify(self.runner, base_dir=base, contract=contract, ecc=ecc, task_id=ref.task_id,
                                candidate_id=candidate_id, diff=diff, sealed_dir=sealed_dir, manifest=manifest,
                                ledger=self.ledger if final else None, baseline=base_preds, open_holdout=final)
