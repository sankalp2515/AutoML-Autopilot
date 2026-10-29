# B02: train/serve representation regression

Only `../repo/` is agent input. This directory contains evaluator material and
must never be included in an agent workspace, prompt, repository history or
research context. The proof utility is a trusted local demonstration, not a
production sandbox or an adversarially secure grading service.

The batch representation has nine columns. Online scoring still uses the old
eight-column order, with tenure and monthly charge reversed relative to the
current representation. The wrong patch appends usage to match dimensionality
but preserves this semantic error. The reference patch uses the shared feature
module and adds a new regression test without editing existing tests.

Run from the platform repository root:

```sh
python scripts/prove_fixture.py fixtures/repos/churn-service
```

Each case is evaluated in a separate temporary copy. No dependency installation,
model call or network request is performed. Install the service requirements
before running. The holdout AUC is measured on batch predictions; parity is the
additional invariant that detects the wrong online patch. A high batch AUC alone
cannot validate serving behavior.

`harness/parity_check.py` can be copied into a sandbox independently of the labels:

```sh
python parity_check.py --contract autopilot.yaml --model artifacts/model.joblib --input data/holdout_features.csv --predictions artifacts/predictions.csv --out artifacts/parity.json
```

It resolves the online entrypoint from the contract and checks 200 rows by
default. A mismatch returns exit code 1, with errors represented explicitly.
The two sealed tests cover holdout parity and a future shared-feature addition.

Data generation uses seed 21042. `repo/data/generate.py` reproduces the public
CSVs. `sealed/generate_labels.py` reproduces holdout outcomes using an independent
draw seed that is not published in the agent-visible package. Public generation
does not return holdout labels. These are artificial accounts, not customer data.
