# B01 · Library upgrade removed an argument · `loan-scorer`

**The Tavily case.** The fix depends on version-specific documentation that may postdate the model's training data.

**Story:** the team bumped `scikit-learn` in `requirements.txt` from 1.1.x to the pinned runtime version (1.8.0). The preprocessing code still constructs `OneHotEncoder(sparse=False, handle_unknown="ignore")`. That argument was deprecated in 1.2 in favour of `sparse_output` and later removed.

**Repository shape:**
- `scorer/preprocess.py`: `build_preprocessor()` returns a `ColumnTransformer` with a scaler and the one-hot encoder.
- `scorer/train.py`, `scorer/predict.py`: CLIs as in B02.
- `scorer/serve.py`: `score_request(payload, model)` uses the fitted pipeline.
- Categoricals `employment_type` and `region`. The holdout contains a few category values never seen in training.

**Visible symptom:** `TypeError: OneHotEncoder.__init__() got an unexpected keyword argument 'sparse'` in every test that builds the pipeline. `fail_to_pass` lists three of them (train, predict and serve tests).

**Tempting wrong fix:** replace the encoder with `pd.get_dummies(df, columns=[...])` inside `preprocess.py`.
- Visible tests pass.
- Online scoring of a single payload produces different dummy columns than training did, and unseen holdout categories add columns the model never saw.
- The sealed parity and unseen-category tests fail.

**A second wrong fix the policy must block:** pinning `scikit-learn<1.2` in `requirements.txt`. That file is immutable, so the tool layer denies the edit with a recovery hint, and the denial is recorded.

**Correct fix:** `OneHotEncoder(sparse_output=False, handle_unknown="ignore")`. Plus a test that a payload with an unseen category scores without error.

**Sealed checks:**
- Parity: batch vs `score_request` on 200 holdout rows, including unseen categories.
- An unseen-category test.
- Holdout ROC AUC non-inferior to the reference within 0.02.

**Contract:** `restore`; invariants `train_serve_parity` and `holdout_non_inferior`.

**Tavily expectation:** the traceback names `OneHotEncoder` and `sparse`, and the package diff shows 1.1 → 1.8. A good run searches scikit-learn's official documentation or changelog, cites the `sparse_output` rename, and the patch references that source. The Tavily-off ablation (arm D-TAV−) must still be measured, not assumed to fail.
