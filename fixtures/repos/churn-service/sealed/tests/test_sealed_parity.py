"""Behavioral checks kept outside the agent-visible repository."""

import os

import joblib
import numpy as np
import pandas as pd

from churn import features, serve


def test_online_matches_batch_on_holdout():
    rows = pd.read_csv(os.environ["CHURN_HOLDOUT_FEATURES"]).head(200)
    batch = pd.read_csv(os.environ["CHURN_PREDICTIONS"]).set_index("customer_id")
    model = joblib.load(os.environ["CHURN_MODEL"])
    assert len(rows) == 200
    for row in rows.to_dict(orient="records"):
        actual = serve.score_request(row, model)
        expected = batch.loc[row["customer_id"], "score"]
        assert abs(actual - expected) <= 1e-9


def test_new_feature_preserves_shared_representation(monkeypatch):
    expanded_order = [*features.FEATURE_ORDER, "loyalty_credit"]
    row = pd.read_csv(os.environ["CHURN_HOLDOUT_FEATURES"]).iloc[0].to_dict()
    row["loyalty_credit"] = 17.0
    base = features.build_features(pd.DataFrame([row]))

    def expanded(frame):
        result = base.copy()
        result["loyalty_credit"] = frame["loyalty_credit"].astype(float)
        return result[expanded_order]

    monkeypatch.setattr(features, "build_features", expanded)
    monkeypatch.setattr(features, "FEATURE_ORDER", expanded_order)
    expected = expanded(pd.DataFrame([row])).to_numpy()

    class TenFeatureModel:
        def predict_proba(self, values):
            np.testing.assert_array_equal(values, expected)
            return np.array([[0.63, 0.37]])

    assert serve.score_request(row, TenFeatureModel()) == 0.37
