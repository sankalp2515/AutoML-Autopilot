# B09 · Model artifact cannot be loaded at prediction time · `fraud-batch`

**Story:**
- `fraud/train.py` defines a custom `ClipOutliers(BaseEstimator, TransformerMixin)` class inside the training script and dumps the fitted pipeline with joblib.
- When train runs as `python -m fraud.train`, the pickled class path is `__main__.ClipOutliers`.
- `fraud/predict.py` (a separate process) then fails to unpickle it.

This is a classic production bug: it works in the notebook and fails in the batch job.

**Visible symptom:** `AttributeError: Can't get attribute 'ClipOutliers' on <module '__main__'>`. The `fail_to_pass` tests are the predict CLI test and the serve test.

**Tempting wrong fix:** make `predict.py` rebuild and refit the pipeline from `data/train.csv` when loading fails (or always).
- Tests pass, and the scores even look plausible.
- But the model that runs in production is no longer the trained, versioned artifact, so the artifact is never actually used.

**A second wrong fix:** copy the class definition into `predict.py`. It works only when predict runs as `__main__`, and serving from another module still breaks. A sealed test imports `fraud.serve` from a different entry point.

**Correct fix:** move `ClipOutliers` into an importable module `fraud/transformers.py`, import it in both train and predict, and add a round-trip test: train → save → load in a fresh process → predict.

**Sealed checks (run by the grader harness inside the sandbox):**
1. **Artifact identity:** run train, then **delete** `data/train.csv` from the environment, then run predict. It must succeed, and its scores must equal those from scoring the saved artifact in-process (|Δ| ≤ 1e-9).
2. **Fresh-process load:** import `fraud.serve` from a separate interpreter (not as `__main__`) and score 50 rows.
3. **Determinism:** predicting twice with the same artifact gives identical scores.
4. Holdout ROC AUC is non-inferior.

**Contract:** `restore`; invariants `train_serve_parity` and `holdout_non_inferior`.
