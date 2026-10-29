import pandas as pd
from refunds.model import scores

def score_request(payload, model):
    return float(scores(pd.DataFrame([payload]), model)[0])
