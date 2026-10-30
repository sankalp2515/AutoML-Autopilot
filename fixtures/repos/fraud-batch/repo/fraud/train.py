import argparse
from pathlib import Path
import joblib
import pandas as pd
from fraud.model import fit

class ClipOutliers:
    def transform(self, frame):
        return frame

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"classifier": fit(pd.read_csv(a.data)), "transformer": ClipOutliers()}, a.out)

if __name__ == "__main__":
    main()
