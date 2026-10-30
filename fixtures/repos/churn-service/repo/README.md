# Churn service

Customer account churn probabilities for retention planning. The Python package
provides model training, CSV batch scoring and an online scoring entrypoint.
All account records are synthetic and contain no personal information.

Use Python 3.12 and install `requirements.txt`. Run commands from this directory:

```sh
python -m pip install -r requirements.txt
python -m pytest -q tests
python -m churn.train --data data/train.csv --out artifacts/model.joblib
python -m churn.predict --model artifacts/model.joblib --input data/holdout_features.csv --out artifacts/predictions.csv
```

Batch output contains `customer_id` and `score`, the probability of `churned=1`.
The online interface is `churn.serve.score_request(payload, model)`; pass one
account dictionary and a model loaded with joblib.

Account inputs include tenure, monthly charges, support calls, late payments,
contract type, automatic payment, internet service, paperless billing and usage.
`churn.features` defines their numeric representation. Training uses a standard
scaler followed by logistic regression. `data/generate.py` reproduces the local
training and scoring CSVs with a stable random seed.
