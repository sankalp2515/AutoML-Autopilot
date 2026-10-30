from pathlib import Path
import subprocess
import sys
import joblib
import pandas as pd
import pytest
from refunds.model import fit, scores
from refunds.policy import decisions
from refunds.serve import score_request

@pytest.fixture(scope="module")
def frame():
    return pd.read_csv("data/train.csv")

def test_schema(frame):
    assert frame.row_id.is_unique and set(frame.outcome) == {0, 1}

def test_service(frame):
    model = fit(frame)
    assert 0 <= score_request(frame.iloc[0].to_dict(), model) <= 1

def test_quality(frame):
    from sklearn.metrics import recall_score
    model = fit(frame)
    assert recall_score(frame.outcome, decisions(frame, scores(frame, model))) >= 0.7
