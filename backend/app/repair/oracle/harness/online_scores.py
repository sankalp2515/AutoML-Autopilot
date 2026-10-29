"""Untrusted execution adapter: returns online scores, never a verdict or a tolerance.

The verifier compares these values outside this process/container. No batch
prediction file, sealed tests or labels are supplied to this adapter.
"""
import argparse
import importlib
import json
import math
import os
import sys


def main():
    p = argparse.ArgumentParser()
    for name in ("entrypoint", "model", "input", "out"):
        p.add_argument("--" + name, required=True)
    p.add_argument("--rows", type=int, required=True)
    a = p.parse_args()
    sys.path.insert(0, os.getcwd())
    result = {"scores": [], "errors": []}
    try:
        import joblib
        import pandas as pd
        module, func = a.entrypoint.split(":")
        score = getattr(importlib.import_module(module), func)
        model = joblib.load(a.model)
        for row in pd.read_csv(a.input).head(a.rows).to_dict("records"):
            try:
                value = float(score(row, model))
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError("invalid probability")
                result["scores"].append(value)
            except Exception as exc:
                result["scores"].append(None)
                if len(result["errors"]) < 3:
                    result["errors"].append(type(exc).__name__)
    except Exception as exc:
        result["errors"].append(type(exc).__name__)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(result, f, allow_nan=False)


if __name__ == "__main__":
    main()
