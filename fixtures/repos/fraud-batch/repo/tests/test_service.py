from pathlib import Path
import subprocess
import sys
import joblib
import pandas as pd
import pytest
from fraud.model import fit, scores
from fraud.policy import decisions
from fraud.serve import score_request

@pytest.fixture(scope="module")
def frame():
    return pd.read_csv("data/train.csv")

def test_schema(frame):
    assert frame.row_id.is_unique and set(frame.outcome) == {0, 1}

def test_service(frame):
    model = fit(frame)
    assert 0 <= score_request(frame.iloc[0].to_dict(), model) <= 1

def test_artifact_roundtrip(tmp_path):
    model, out = tmp_path / "model.joblib", tmp_path / "predictions.csv"
    a = subprocess.run([sys.executable, "-m", "fraud.train", "--data", "data/train.csv", "--out", str(model)], capture_output=True)
    assert a.returncode == 0, a.stderr.decode()
    b = subprocess.run([sys.executable, "-m", "fraud.predict", "--model", str(model), "--input", "data/holdout_features.csv", "--out", str(out)], capture_output=True)
    assert b.returncode == 0, b.stderr.decode()
    assert len(pd.read_csv(out)) == 600
