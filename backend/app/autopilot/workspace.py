"""On-disk workspace of one investigation: immutable inputs + produced artifacts.

Layout (DATA_DIR/autopilot/<run_id>/):
  inputs/reference.csv, inputs/current.csv, inputs/pack.json, inputs/protocol.json
  models/incumbent.joblib, models/challenger.joblib, models/<experiment_id>.joblib
  outputs/<experiment_id>.json

Every file written here is recorded with its sha256 so the receipt can prove the
verifier scored exactly the artifacts the runner produced.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from app.autopilot import mlcore
from app.autopilot.scenarios import IncidentPack

# Experiment workers set this; only the verifier process may load final-test rows.
_FINAL_TEST_LOCKED = False


def lock_final_test(locked: bool = True) -> bool:
    """Returns the previous value so in-process callers can restore it."""
    global _FINAL_TEST_LOCKED
    prev, _FINAL_TEST_LOCKED = _FINAL_TEST_LOCKED, locked
    return prev


class Workspace:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    # ── paths ────────────────────────────────────────────────────────────────
    @property
    def inputs(self) -> Path:
        return self.root / "inputs"

    @property
    def models(self) -> Path:
        return self.root / "models"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    def model_path(self, name: str) -> str:
        return str(self.models / f"{name}.joblib")

    # ── write once ───────────────────────────────────────────────────────────
    def materialize(self, pack: IncidentPack, protocol: dict[str, Any]) -> dict[str, str]:
        for d in (self.inputs, self.models, self.outputs):
            d.mkdir(parents=True, exist_ok=True)
        pack.reference.to_csv(self.inputs / "reference.csv", index=False)
        pack.current.to_csv(self.inputs / "current.csv", index=False)
        meta = {k: v for k, v in asdict(pack).items() if k not in ("reference", "current")}
        (self.inputs / "pack.json").write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
        (self.inputs / "protocol.json").write_text(json.dumps(protocol), encoding="utf-8")
        return {name: mlcore.sha256_file(self.inputs / name)
                for name in ("reference.csv", "current.csv", "pack.json", "protocol.json")}

    # ── read ─────────────────────────────────────────────────────────────────
    def pack(self) -> dict[str, Any]:
        return json.loads((self.inputs / "pack.json").read_text(encoding="utf-8"))

    def protocol(self) -> dict[str, Any]:
        return json.loads((self.inputs / "protocol.json").read_text(encoding="utf-8"))

    def reference(self) -> pd.DataFrame:
        return pd.read_csv(self.inputs / "reference.csv")

    def current(self) -> pd.DataFrame:
        return pd.read_csv(self.inputs / "current.csv")

    def rows(self, role: str) -> pd.DataFrame:
        if role == "final_test" and _FINAL_TEST_LOCKED:
            raise PermissionError("final-test rows are readable only by the verifier process")
        roles = self.protocol()["roles"]
        src = self.reference() if role in ("train", "ref_validation") else self.current()
        return src.iloc[roles[role]].reset_index(drop=True)

    def incumbent(self) -> mlcore.Candidate:
        pack = self.pack()
        thr = float((pack.get("serving") or {}).get("threshold", pack["pipeline"]["threshold"]))
        return mlcore.Candidate(self.model_path("incumbent"), thr, label="incumbent",
                                score_column=pack.get("score_column"))

    def write_output(self, name: str, payload: dict[str, Any]) -> str:
        p = self.outputs / f"{name}.json"
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        return mlcore.sha256_file(p)
