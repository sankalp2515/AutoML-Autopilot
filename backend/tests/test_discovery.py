"""Discovery Agent v1 — proposal parsing, vetting, ask-mode gate, marker."""

import json

import pytest

from app.core import discovery


@pytest.fixture(autouse=True)
def _tmp_data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(discovery.settings, "DATA_DIR", str(tmp_path))
    yield tmp_path


def test_vet_tier_a_pure_pip_ok():
    v = discovery.vet("catboost")
    assert v["ok"] and v["pinned"].startswith("catboost==")


def test_vet_rejects_system_deps_on_this_host():
    v = discovery.vet("lightgbm")
    assert not v["ok"] and "libgomp1" in v["reason"]


def test_vet_rejects_unknown_package():
    v = discovery.vet("evil-package")
    assert not v["ok"] and v["tier"] == "C"


def test_record_proposal_parses_scout_note():
    note = "CatBoost (pip: catboost) is recommended for categorical-heavy data."
    p = discovery.record_proposal(note, "run1", "binary_classification")
    assert p["package"] == "catboost" and p["status"] == "pending"
    assert discovery.list_proposals()[0]["package"] == "catboost"


def test_record_proposal_no_note_no_pkg():
    assert discovery.record_proposal("", "run1") is None
    assert discovery.record_proposal("use a better model", "run1") is None


def test_blocked_package_cannot_be_approved():
    discovery.record_proposal("LightGBM (pip: lightgbm) is fast.", "run1")
    with pytest.raises(ValueError):
        discovery.decide("lightgbm", approve=True)


def test_approve_stages_pinned_requirement(tmp_path):
    discovery.record_proposal("CatBoost (pip: catboost) rocks.", "run1")
    p = discovery.decide("catboost", approve=True)
    assert p["status"] == "approved"
    staged = tmp_path / "discovery" / "approved-requirements.txt"
    assert staged.exists()
    assert "catboost==" in staged.read_text()


def test_rejected_proposal_not_renagged():
    discovery.record_proposal("CatBoost (pip: catboost) rocks.", "run1")
    discovery.decide("catboost", approve=False)
    p = discovery.record_proposal("CatBoost (pip: catboost) again!", "run2")
    assert p["status"] == "rejected"          # decision sticks


def test_installed_marker_flips_registry(tmp_path, monkeypatch):
    from app.core import model_registry as reg
    monkeypatch.setattr(reg.settings, "DATA_DIR", str(tmp_path), raising=False)
    spec = next(s for s in reg.MODEL_CATALOG if s.get("requires_pkg") == "catboost") \
        if hasattr(reg, "MODEL_CATALOG") else None
    # Marker absent → not installed
    cat = {m["name"] for m in reg.catalog_for("binary_classification", installed_only=True)}
    assert "CatBoost" not in cat
    # Write the verified marker → trainable
    (tmp_path / "discovery").mkdir()
    (tmp_path / "discovery" / "installed.json").write_text(json.dumps(["catboost"]))
    cat2 = {m["name"] for m in reg.catalog_for("binary_classification", installed_only=True)}
    assert "CatBoost" in cat2
