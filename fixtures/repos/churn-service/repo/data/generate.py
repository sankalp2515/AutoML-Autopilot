"""Generate repeatable synthetic account records for local development."""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 21042


def generate_accounts(n_rows=5500):
    rng = np.random.default_rng(SEED)
    df = pd.DataFrame({
        "customer_id": [f"C{i:06d}" for i in range(n_rows)],
        "tenure_months": rng.integers(1, 85, n_rows),
        "monthly_charge": rng.uniform(20, 140, n_rows).round(2),
        "support_calls": rng.poisson(1.4, n_rows),
        "late_payments": rng.poisson(0.6, n_rows),
        "contract_type": rng.choice(["monthly", "annual"], n_rows, p=[0.6, 0.4]),
        "auto_pay": rng.integers(0, 2, n_rows),
        "internet_service": rng.choice(["dsl", "fiber"], n_rows),
        "paperless": rng.integers(0, 2, n_rows),
        "usage_gb": rng.uniform(5, 250, n_rows).round(2),
    })
    return df


def churn_probabilities(df):
    logit = (
        -1.8 - 0.055 * df.tenure_months + 0.035 * (df.monthly_charge - 60)
        + 0.95 * df.support_calls + 0.8 * df.late_payments
        + 1.2 * (df.contract_type == "monthly") - 1.0 * df.auto_pay
        + 0.65 * (df.internet_service == "fiber") + 0.3 * df.paperless
        + 0.006 * (df.usage_gb - 100)
    )
    return 1 / (1 + np.exp(-logit))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path(__file__).parent)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    accounts = generate_accounts()
    train = accounts.iloc[:4000].copy()
    rng = np.random.default_rng(SEED + 1)
    train["churned"] = (rng.random(len(train)) < churn_probabilities(train)).astype(int)
    train.to_csv(args.out / "train.csv", index=False, lineterminator="\n")
    accounts.iloc[4000:].to_csv(
        args.out / "holdout_features.csv", index=False, lineterminator="\n")


if __name__ == "__main__":
    main()
