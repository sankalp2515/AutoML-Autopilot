from churn.predict import predict_frame
from tests.helpers import accounts, model


def test_predict_schema_and_identity():
    sample = accounts().head(8)
    result = predict_frame(sample, model())
    assert list(result.columns) == ["customer_id", "score"]
    assert result.customer_id.tolist() == sample.customer_id.tolist()


def test_predict_probability_range():
    result = predict_frame(accounts().head(20), model())
    assert result.score.between(0, 1).all()
