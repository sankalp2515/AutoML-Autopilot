"""Compare an online entrypoint with independently produced batch predictions.

Copy this script into the execution environment; it needs feature rows and
predictions, never holdout labels. This is an execution probe, not an isolation
boundary for adversarial code.
"""

import argparse
import importlib
import json
import math
from pathlib import Path
import re

import joblib
import pandas as pd


def serve_entrypoint(contract: Path):
    # The v1 contract uses a quoted scalar here. Do not execute YAML or import a
    # YAML dependency into the small service just to resolve this one field.
    text = contract.read_text(encoding="utf-8")
    match = re.search(r'^entrypoints:\s*\n  serve: "([\w.]+):([\w]+)"\s*$', text, re.MULTILINE)
    if not match:
        raise ValueError("expected a v1 entrypoints.serve module:function scalar")
    module, name = match.groups()
    return getattr(importlib.import_module(module), name)


def check(contract: Path, model_path: Path, inputs: Path, predictions: Path, n_rows: int):
    rows = pd.read_csv(inputs)
    batch = pd.read_csv(predictions)
    if list(batch.columns) != ["customer_id", "score"]:
        raise ValueError("prediction schema must be customer_id,score")
    if rows.customer_id.duplicated().any() or batch.customer_id.duplicated().any():
        raise ValueError("duplicate customer IDs")
    if set(rows.customer_id) != set(batch.customer_id):
        raise ValueError("prediction customer IDs do not match inputs")
    if not batch.score.map(lambda x: math.isfinite(x) and 0 <= x <= 1).all():
        raise ValueError("invalid batch probabilities")
    if n_rows <= 0 or len(rows) < n_rows:
        raise ValueError("insufficient rows for parity check")
    score_request = serve_entrypoint(contract)
    model = joblib.load(model_path)
    expected = batch.set_index("customer_id").score
    diffs, errors = [], []
    for row in rows.head(n_rows).to_dict(orient="records"):
        try:
            score = float(score_request(row, model))
            if not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("invalid online probability")
            diffs.append(abs(score - float(expected.loc[row["customer_id"]])))
        except Exception as exc:
            errors.append({"customer_id": row["customer_id"], "error": type(exc).__name__})
    return {
        "max_abs_diff": max(diffs) if diffs else None,
        "n_rows": n_rows,
        "mismatches": sum(diff > 1e-9 for diff in diffs) + len(errors),
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, default=Path("autopilot.yaml"))
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--rows", type=int, default=200)
    args = parser.parse_args()
    report = check(args.contract, args.model, args.input, args.predictions, args.rows)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return 0 if report["mismatches"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
