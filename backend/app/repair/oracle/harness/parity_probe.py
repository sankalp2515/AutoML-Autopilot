"""Grader probe, uploaded into the candidate's environment by the ML oracle.

Standalone on purpose (no platform imports). It calls the repository's online entrypoint
`fn(payload: dict, model)` on holdout feature rows and RECORDS the raw outputs. It decides
nothing: the verifier compares them with the batch predictions it already holds, so candidate
code running in this process cannot fake the comparison (BUG-05). It receives feature rows
only, never labels or batch predictions. The model is loaded with the repository's own
`load_model` entrypoint when given, so any framework works; else joblib.
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
    ap.add_argument("--id-column", required=True)
    ap.add_argument("--rows", type=int, default=200)
    ap.add_argument("--out", required=True)
    ap.add_argument("--kind", choices=["number", "label"], default="number")
    ap.add_argument("--loader", default=None)
    a = ap.parse_args()
    sys.path.insert(0, os.getcwd())

    import pandas as pd

    report = {"online": [], "errors": []}
    try:
        mod, fn = a.entrypoint.split(":")
        score = getattr(importlib.import_module(mod), fn)
        if a.loader:
            lm, lf = a.loader.split(":")
            model = getattr(importlib.import_module(lm), lf)(a.model)
        else:
            import joblib
            model = joblib.load(a.model)
        for row in pd.read_csv(a.input).head(a.rows).to_dict(orient="records"):
            rid = str(row[a.id_column])
            try:
                out = score(dict(row), model)
                if a.kind == "label":
                    value = str(out).strip()
                else:
                    value = float(out)
                    if not math.isfinite(value):
                        raise ValueError("non-finite score")
                report["online"].append([rid, value])
            except Exception as e:  # noqa: BLE001 - every failure is evidence
                if len(report["errors"]) < 5:
                    report["errors"].append(f"{type(e).__name__}: {str(e)[:160]}")
                report["online"].append([rid, None])
    except Exception as e:  # noqa: BLE001
        report["errors"].append(f"{type(e).__name__}: {str(e)[:300]}")
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
