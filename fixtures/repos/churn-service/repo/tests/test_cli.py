import subprocess
import sys

import joblib
import pandas as pd

from tests.helpers import model


def test_train_cli(tmp_path):
    path = tmp_path / "trained.joblib"
    subprocess.run([sys.executable, "-m", "churn.train", "--data", "data/train.csv",
                    "--out", str(path)], check=True, timeout=60)
    assert path.is_file()


def test_predict_cli(tmp_path):
    path = tmp_path / "model.joblib"
    out = tmp_path / "scores.csv"
    joblib.dump(model(), path)
    subprocess.run([sys.executable, "-m", "churn.predict", "--model", str(path),
                    "--input", "data/holdout_features.csv", "--out", str(out)],
                   check=True, timeout=60)
    result = pd.read_csv(out)
    assert len(result) == 1500
    assert list(result.columns) == ["customer_id", "score"]
