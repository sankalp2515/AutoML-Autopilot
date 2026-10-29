from sklearn.linear_model import LogisticRegression
from refunds.features import matrix

def fit(frame):
    clf = LogisticRegression(C=1, random_state=7, max_iter=200)
    return clf.fit(matrix(frame), frame["outcome"])

def scores(frame, model):
    return model.predict_proba(matrix(frame))[:, 1]
