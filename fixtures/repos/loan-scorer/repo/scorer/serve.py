import pandas as pd
from scorer.model import scores

def score_request(payload, model):
    return float(scores(pd.DataFrame([payload]), model)[0])
