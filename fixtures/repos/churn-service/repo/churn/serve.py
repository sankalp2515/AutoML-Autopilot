"""Online scoring entrypoint for account records."""

import pandas as pd

from .features import build_features

SERVING_ORDER = [
    "tenure_months", "monthly_charge", "support_calls", "late_payments",
    "contract_monthly", "auto_pay", "internet_fiber", "paperless",
]


def score_request(payload: dict, model) -> float:
    frame = build_features(pd.DataFrame([payload]))
    row = frame[SERVING_ORDER].to_numpy()
    return float(model.predict_proba(row)[0, 1])
