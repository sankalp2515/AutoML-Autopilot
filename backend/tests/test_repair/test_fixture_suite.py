"""Actual checker proofs on five additional synthetic repositories; no LLM calls."""
import json
from pathlib import Path

import pandas as pd
import pytest

from app.repair import contracts as c, snapshots
from app.repair.oracle import code, ml
from app.repair.sandbox.process import ProcessRunner
from app.config import settings

ROOT = Path(__file__).resolve().parents[3] / "fixtures/repos"
NAMES = ["loan-scorer", "refund-risk", "credit-approval", "fraud-batch", "pricing-model"]


@pytest.mark.parametrize("name", NAMES)
def test_catalog_and_label_boundary(name, monkeypatch):
    monkeypatch.setattr(snapshots, "fixtures_root", lambda: ROOT)
    entry = next(x for x in snapshots.samples() if x["name"] == name)
    assert entry["task"] and entry["failing_tests"] and entry["invariants"]
    rc = c.RepoContract.from_yaml((ROOT / name / "repo/autopilot.yaml").read_text())
    holdout = pd.read_csv(ROOT / name / "repo" / rc.data.holdout_features)
    labels = pd.read_csv(ROOT / name / "sealed/holdout_labels.csv")
    train = pd.read_csv(ROOT / name / "repo" / rc.data.train)
    assert rc.data.target not in holdout
    assert holdout.row_id.is_unique and labels.row_id.is_unique
    assert set(holdout.row_id) == set(labels.row_id)
    assert not set(train.row_id) & set(holdout.row_id)
    if name == "credit-approval":
        assert labels.groupby("region").outcome.sum().min() >= 30


@pytest.mark.slow
@pytest.mark.parametrize("name", NAMES)
def test_unpatched_wrong_and_reference(name, tmp_path, monkeypatch):
    fix, repo = ROOT / name, ROOT / name / "repo"
    monkeypatch.setattr(snapshots, "fixtures_root", lambda: ROOT)
    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    staged = snapshots.stage_sample("fixture-proof", name)
    admitted = snapshots.freeze(staged["id"], "fixture-proof")
    repo, rc = Path(admitted["snapshot_dir"]), admitted["contract"]
    manifest = c.SealedManifest.from_yaml((fix / "sealed/manifest.yaml").read_text())
    ecc = c.ECC.build(task_id=f"proof-{name}", base_commit_sha=admitted["sha"],
                      repo_snapshot_digest=admitted["digest"],
                      contract=rc, target=(fix / "TASK.txt").read_text())
    runner = ProcessRunner(str(tmp_path / "envs"), allow_unisolated=True)
    baseline = code.measure_baseline(runner, str(repo), rc)
    assert baseline.parsed
    assert all(baseline.outcomes.get(t) in ("failed", "error") for t in rc.task.fail_to_pass), baseline
    base_predictions = ml.baseline_predictions(runner, str(repo), rc)
    expected = json.loads((fix / "sealed/expectations.json").read_text())
    receipts = {}
    for kind in ("wrong_fix", "reference_fix"):
        diff = (fix / f"sealed/{kind}.patch").read_text()
        cr = code.verify(runner, base_dir=str(repo), contract=rc, ecc=ecc, task_id=ecc.task_id,
                         candidate_id=kind, diff=diff, baseline=baseline)
        assert cr.decision == c.Decision.VERIFIED, cr.model_dump()
        mr = ml.verify(runner, base_dir=str(repo), contract=rc, ecc=ecc, task_id=ecc.task_id,
                       candidate_id=kind, diff=diff, sealed_dir=str(fix / "sealed"), manifest=manifest,
                       ledger=ml.FileHoldoutLedger(str(tmp_path / kind)), baseline=base_predictions)
        receipts[kind] = {"code": cr.model_dump(mode="json"), "ml": mr.model_dump(mode="json")}
        if kind == "wrong_fix":
            assert mr.decision == c.Decision.REJECTED, mr.model_dump()
            check = next(x for x in mr.checks if x.name == expected["rejection_check"])
            assert check.status == c.CheckStatus.FAIL, mr.model_dump()
            if name == "credit-approval":
                assert mr.metrics["delta"]["delta"] > 0
                assert mr.metrics["segments"]["by_group"]["rural"]["delta"] < -rc.data.segment_max_drop
            if name == "refund-risk":
                assert not mr.holdout_opened
        else:
            assert mr.decision == c.Decision.VERIFIED, mr.model_dump()
            assert mr.holdout_opened
    # Optional local/CI artifact; clearly a deterministic checker proof, not a replay.
    import os
    if out := os.environ.get("REPAIR_PROOF_OUTPUT"):
        p = Path(out); p.mkdir(parents=True, exist_ok=True)
        (p / f"{name}.json").write_text(json.dumps({"fixture": name, "kind": "checker-proof", "runner": runner.name, "autonomous_agent": False, "base_commit_sha": admitted["sha"], "snapshot_digest": admitted["digest"], "receipts": receipts}, indent=2), encoding="utf-8")
