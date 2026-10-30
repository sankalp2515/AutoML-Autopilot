from sklearn.pipeline import make_pipeline
from sklearn.linear_model import LogisticRegression
from scorer.features import build_preprocessor

def fit(frame):
    return make_pipeline(build_preprocessor(), LogisticRegression(random_state=7)).fit(frame, frame.outcome)

def scores(frame, model):
    return model.predict_proba(frame)[:, 1]
