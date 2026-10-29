from churn.serve import score_request
from tests.helpers import accounts, model


def test_score_request_returns_probability():
    score = score_request(accounts().iloc[0].to_dict(), model())
    assert isinstance(score, float)
    assert 0 <= score <= 1
