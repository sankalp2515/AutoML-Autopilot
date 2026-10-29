"""Shared numeric representation of customer accounts."""

import pandas as pd

FEATURE_ORDER = [
    "monthly_charge", "tenure_months", "support_calls", "late_payments",
    "contract_monthly", "auto_pay", "internet_fiber", "paperless", "usage_gb",
]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    values = pd.DataFrame(index=df.index)
    for name in ("monthly_charge", "tenure_months", "support_calls", "late_payments",
                 "auto_pay", "paperless", "usage_gb"):
        values[name] = pd.to_numeric(df[name], errors="raise")
    values["contract_monthly"] = (df["contract_type"] == "monthly").astype(float)
    values["internet_fiber"] = (df["internet_service"] == "fiber").astype(float)
    return values[FEATURE_ORDER].astype(float)
