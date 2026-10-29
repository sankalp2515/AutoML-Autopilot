"""Train the account churn probability model."""

import argparse
from pathlib import Path

import joblib
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import FEATURE_ORDER, build_features


def fit_model(df: pd.DataFrame):
    model = make_pipeline(StandardScaler(), LogisticRegression(random_state=42, max_iter=500))
    model.fit(build_features(df)[FEATURE_ORDER].to_numpy(), df["churned"].to_numpy())
    return model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    model = fit_model(pd.read_csv(args.data))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, args.out)


if __name__ == "__main__":
    main()
