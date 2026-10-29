"""Write batch churn probabilities keyed by customer ID."""

import argparse
from pathlib import Path

import joblib
import pandas as pd

from .features import FEATURE_ORDER, build_features


def predict_frame(df: pd.DataFrame, model) -> pd.DataFrame:
    scores = model.predict_proba(build_features(df)[FEATURE_ORDER].to_numpy())[:, 1]
    return pd.DataFrame({"customer_id": df["customer_id"].to_numpy(), "score": scores})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--input", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    output = predict_frame(pd.read_csv(args.input), joblib.load(args.model))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    output.to_csv(args.out, index=False)


if __name__ == "__main__":
    main()
