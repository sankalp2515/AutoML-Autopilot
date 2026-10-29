from functools import lru_cache

import pandas as pd

from churn.train import fit_model


@lru_cache(maxsize=1)
def accounts():
    return pd.read_csv("data/train.csv")


@lru_cache(maxsize=1)
def model():
    return fit_model(accounts())
