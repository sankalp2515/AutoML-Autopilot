import argparse
from pathlib import Path
import joblib
import pandas as pd
from refunds.model import scores
from refunds.policy import decisions

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    frame = pd.read_csv(a.input)
    model = joblib.load(a.model)
    values = scores(frame, model)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"row_id": frame.row_id, "score": values,
                  "decision": decisions(frame, values)}).to_csv(a.out, index=False)

if __name__ == "__main__":
    main()
