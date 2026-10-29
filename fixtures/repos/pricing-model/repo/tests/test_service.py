from pathlib import Path
import subprocess
import sys
import joblib
import pandas as pd
import pytest
from pricing.model import fit, scores
from pricing.policy import decisions
from pricing.serve import score_request

@pytest.fixture(scope="module")
def frame():
    return pd.read_csv("data/train.csv")

def test_schema(frame):
    assert frame.row_id.is_unique and set(frame.outcome) == {0, 1}

def test_service(frame):
    model = fit(frame)
    assert 0 <= score_request(dict(frame.iloc[0], list_price=0), model) <= 1
