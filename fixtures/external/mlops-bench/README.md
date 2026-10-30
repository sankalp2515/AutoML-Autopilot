# Pinned public MLOps-Bench subset

Source: https://github.com/oracle-samples/mlops-bench at
`8f48b685df0491af048bde4b56c7917d0e42d336`.
Two independently published task patterns: Great Expectations serving and MLflow
feature engineering. These are released contract workspaces grounded in those
repositories, not full clones or independent production pilots. No leaderboard or
autonomous-agent result is claimed.

`provenance.json` records SHA-256 for every imported file. LICENSE,
THIRD_PARTY_LICENSES.txt and applicable per-source license texts are preserved.
Upstream files are unchanged. Our harness lives in scripts/evaluate_mlops_subset.py.

- `prove --out report.json`: reproduce baseline F2P failure, baseline P2P preservation,
  and reference F2P/P2P success using the unchanged released tests.
- `export-agent <task> --out <new-directory>`: materialize **only problem.json and repo/**.
- `score <task> --workspace <agent-workspace> --out report.json`: external public
  F2P/P2P scoring. Never expose oracle/, expected_changes.patch, evaluator tests or
  metadata to the agent. Do not treat this Python test harness as a hostile-code sandbox.

The public-task tests are not the separate auxiliary oracle contract suite. Report
this subset's exact denominators; do not compare it to the full 212-task paper score.
