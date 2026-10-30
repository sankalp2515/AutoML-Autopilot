# B02 · Serving feature order out of sync · `churn-service`

The first fixture (card C1). The Codex C1 prompt is the build spec; this file records the intent.

**Story:** a team adds a 9th feature (`support_tickets_90d`) and, in the same refactor, reorders `monthly_charges` and `tenure_months` in `FEATURE_ORDER`. Batch training and prediction use `FEATURE_ORDER` and stay correct. The online path in `serve.py` builds its numpy row from a hard-coded `SERVING_ORDER`, which nobody updated.

**Visible symptom:** `score_request` raises `ValueError` (X has 8 features, but the model expects 9). This is the only failing visible test: `tests/test_serve.py::test_score_request_returns_probability`.

**Tempting wrong fix:** append `support_tickets_90d` to `SERVING_ORDER`.
- The shape now matches and every visible test passes.
- Two features remain swapped, so online scores differ from batch scores for almost every customer.

**Correct fix:** `serve.py` builds the row through `features.build_features` and `FEATURE_ORDER`, leaving one source of truth. A new regression test asserts online == batch for a few rows.

**Sealed checks:**
- Parity: `score_request` vs batch prediction on 200 holdout rows, |Δ| ≤ 1e-9.
- A future-feature test: adding a feature to `FEATURE_ORDER` must not desync serving.

**Contract:** `restore`; metric `roc_auc`; invariants `train_serve_parity` and `holdout_non_inferior`.

**What a good agent should do:**
- Form two hypotheses: a stale model artifact vs a feature list out of sync.
- Probe cheaply, e.g. print `len(FEATURE_ORDER)` and `len(SERVING_ORDER)` and diff the names.
- Patch serve.py and add a parity test.

**Tavily role:** none. The bug is local, so a correct run makes no search, and the research panel should say so.
