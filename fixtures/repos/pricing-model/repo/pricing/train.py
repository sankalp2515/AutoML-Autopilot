import argparse
from pathlib import Path
import joblib
import pandas as pd
from pricing.model import fit

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--data", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(fit(pd.read_csv(a.data)), a.out)

if __name__ == "__main__":
    main()
