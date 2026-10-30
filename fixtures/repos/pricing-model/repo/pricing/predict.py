import argparse
from pathlib import Path
import joblib
import pandas as pd
from pricing.model import scores
from pricing.policy import decisions

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--model", required=True)
    p.add_argument("--input", required=True)
    p.add_argument("--out", required=True)
    a = p.parse_args()
    frame = pd.read_csv(a.input)
    model = joblib.load(a.model)
    batch_frame = frame.copy()
    batch_frame.loc[batch_frame.list_price == 0, "quote_amount"] = 0
    batch_frame.loc[batch_frame.list_price == 0, "list_price"] = 1
    values = scores(batch_frame, model)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"row_id": frame.row_id, "score": values,
                  "decision": decisions(frame, values)}).to_csv(a.out, index=False)

if __name__ == "__main__":
    main()
