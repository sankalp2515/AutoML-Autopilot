"""Prepare a pinned public-data fixture; never runs on a judge request.

Source: Becker & Kohavi, Adult, UCI (1996), DOI 10.24432/C5XW20, CC BY 4.0.
Only a deterministic sample is redistributed. The incident is an injected age
unit conversion, not an observed production outage or a temporal experiment.
"""
import hashlib
import argparse
import io
import json
import urllib.request
import zipfile
from pathlib import Path

import pandas as pd

URL = "https://archive.ics.uci.edu/static/public/2/adult.zip"
OUT = Path(__file__).resolve().parents[1] / "backend" / "app" / "autopilot" / "data"
COLS = ["age", "workclass", "fnlwgt", "education", "education_num", "marital_status", "occupation",
        "relationship", "race", "sex", "capital_gain", "capital_loss", "hours_per_week", "native_country", "income"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, help="Previously downloaded UCI adult.zip")
    args = parser.parse_args()
    raw = args.archive.read_bytes() if args.archive else urllib.request.urlopen(URL, timeout=60).read()
    archive = zipfile.ZipFile(io.BytesIO(raw))
    df = pd.read_csv(archive.open("adult.data"), names=COLS, skipinitialspace=True, na_values="?")
    df = df.sample(n=8000, random_state=2026).reset_index(drop=True)
    df["above_50k"] = (df.pop("income") == ">50K").astype(int)
    OUT.mkdir(parents=True, exist_ok=True)
    dest = OUT / "adult_sample.csv"
    df.to_csv(dest, index=False)
    manifest = {"source": URL, "citation": "Becker, B. & Kohavi, R. (1996). Adult. UCI Machine Learning Repository. https://doi.org/10.24432/C5XW20",
                "license": "CC BY 4.0", "archive_sha256": hashlib.sha256(raw).hexdigest(),
                "sample_sha256": hashlib.sha256(dest.read_bytes()).hexdigest(), "rows": len(df),
                "sampling_seed": 2026, "transformation": "8,000 sampled rows; target encoded as above_50k; hyphens in names replaced by underscores"}
    (OUT / "adult_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    main()
