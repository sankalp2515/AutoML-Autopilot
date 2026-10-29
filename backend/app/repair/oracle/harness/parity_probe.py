"""Grader probe, uploaded into the candidate's environment by the ML oracle.

Standalone on purpose (no platform imports). It calls the repository's online entrypoint
`fn(payload: dict, model)` on rows that already have batch predictions and reports the
largest difference. It receives feature rows only, never labels. It runs next to
candidate code, so it is an execution probe, not a security boundary; the oracle treats
anything it cannot parse as a failure.
"""
import argparse
import importlib
import json
import math
import os
import sys


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--entrypoint", required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--input", required=True)
    ap.add_argument("--predictions", required=True)
    ap.add_argument("--id-column", required=True)
    ap.add_argument("--rows", type=int, default=200)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    sys.path.insert(0, os.getcwd())

    import joblib
    import pandas as pd

    report = {"n_rows": 0, "max_abs_diff": None, "mismatch_rows": 0, "errors": []}
    try:
        mod, fn = a.entrypoint.split(":")
        score = getattr(importlib.import_module(mod), fn)
        model = joblib.load(a.model)
        rows = pd.read_csv(a.input).head(a.rows)
        batch = pd.read_csv(a.predictions).set_index(a.id_column)["score"]
        diffs = []
        for row in rows.to_dict(orient="records"):
            try:
                online = float(score(dict(row), model))
                if not math.isfinite(online):
                    raise ValueError("non-finite score")
                diffs.append(abs(online - float(batch.loc[row[a.id_column]])))
            except Exception as e:  # noqa: BLE001 - every failure is evidence
                if len(report["errors"]) < 5:
                    report["errors"].append(f"{type(e).__name__}: {str(e)[:160]}")
                diffs.append(float("inf"))
        report["n_rows"] = len(diffs)
        finite = [d for d in diffs if math.isfinite(d)]
        report["max_abs_diff"] = max(finite) if len(finite) == len(diffs) and finite else None
        report["diffs"] = [d if math.isfinite(d) else None for d in diffs]
    except Exception as e:  # noqa: BLE001
        report["errors"].append(f"{type(e).__name__}: {str(e)[:300]}")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
