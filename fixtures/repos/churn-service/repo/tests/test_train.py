import joblib

from churn.features import FEATURE_ORDER
from tests.helpers import model


def test_model_serialization(tmp_path):
    path = tmp_path / "model.joblib"
    joblib.dump(model(), path)
    assert path.is_file()
    assert joblib.load(path).n_features_in_ == len(FEATURE_ORDER)


def test_model_classes():
    assert list(model().classes_) == [0, 1]
