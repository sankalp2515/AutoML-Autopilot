"""Reproduce evaluator-only outcomes without publishing their draw seed."""

from pathlib import Path
import runpy

import numpy as np
import pandas as pd


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    generator = runpy.run_path(str(root.parent / "repo/data/generate.py"))
    rows = pd.read_csv(root.parent / "repo/data/holdout_features.csv")
    rng = np.random.default_rng(28711)
    labels = (rng.random(len(rows)) < generator["churn_probabilities"](rows)).astype(int)
    pd.DataFrame({"customer_id": rows.customer_id, "churned": labels}).to_csv(
        root / "holdout_labels.csv", index=False, lineterminator="\n")
