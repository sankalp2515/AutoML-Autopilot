"""Prove the three B02 outcomes using fresh local copies, without network calls."""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

import pandas as pd
from sklearn.metrics import roc_auc_score

FAIL_TO_PASS = "tests/test_serve.py::test_score_request_returns_probability"


def run(args, cwd, env, timeout=120):
    kwargs = {"start_new_session": True} if os.name != "nt" else {
        "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    with subprocess.Popen([str(arg) for arg in args], cwd=cwd, env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, encoding="utf-8", errors="replace", **kwargs) as process:
        try:
            output, _ = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               capture_output=True, timeout=15)
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.kill()
            output, _ = process.communicate()
            raise RuntimeError(f"command timed out: {args}\n{output[-6000:]}")
    return subprocess.CompletedProcess(args, process.returncode, output)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def successful(args, cwd, env):
    result = run(args, cwd, env)
    require(result.returncode == 0, f"command failed: {args}\n{result.stdout[-6000:]}")
    return result


def junit(path):
    cases = ET.parse(path).findall(".//testcase")
    failed = [f"{case.attrib['classname'].replace('.', '/')}.py::"
              f"{case.attrib['name']}" for case in cases if case.find("failure") is not None]
    errors = sum(case.find("error") is not None for case in cases)
    skipped = sum(case.find("skipped") is not None for case in cases)
    require(errors == 0 and skipped == 0, f"unexpected errors/skips in {path}")
    return len(cases) - len(failed), failed


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_inputs(root):
    train = pd.read_csv(root / "repo/data/train.csv")
    holdout = pd.read_csv(root / "repo/data/holdout_features.csv")
    labels = pd.read_csv(root / "sealed/holdout_labels.csv")
    require(len(train) == 4000 and len(holdout) == len(labels) == 1500, "unexpected data sizes")
    require("churned" in train and "churned" not in holdout, "label boundary violated")
    require(all(frame.customer_id.notna().all() and frame.customer_id.is_unique
                for frame in (train, holdout, labels)), "invalid customer IDs")
    require(not set(train.customer_id) & set(holdout.customer_id), "training/holdout overlap")
    require(set(holdout.customer_id) == set(labels.customer_id), "holdout labels misaligned")
    require(set(train.churned) == set(labels.churned) == {0, 1}, "invalid outcome classes")
    for path in (root / "repo").rglob("*"):
        if any(part in ("__pycache__", ".pytest_cache", ".git", ".venv", "artifacts")
               for part in path.relative_to(root / "repo").parts):
            continue
        if path.is_file() and path.suffix in (".py", ".md", ".txt", ".yaml", ".ini", ".csv"):
            text = path.read_text(encoding="utf-8").lower()
            require(not any(word in text for word in ("bug", "sealed", "fixture", "fix")),
                    f"agent-visible clue in {path}")


def prove(root, case, temp):
    repo = temp / "repo"
    shutil.copytree(root / "repo", repo, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    protected = {str(path.relative_to(repo)): digest(path) for path in repo.rglob("*")
                 if path.is_file() and (path.relative_to(repo).parts[0] in ("data", "tests")
                                       or path.name in ("autopilot.yaml", "requirements.txt"))}
    env = os.environ.copy()
    env.update({"PYTHONPATH": str(repo), "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
                "PYTHONUTF8": "1", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1"})
    # Avoid inheriting a calling worktree's Git plumbing or pytest selection.
    for key in list(env):
        if key.startswith("GIT_") or key in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
            env.pop(key)
    if case != "unpatched":
        patch = root / "sealed" / f"{case}.patch"
        successful(["git", "apply", "--check", patch], repo, env)
        successful(["git", "apply", patch], repo, env)
    require(all((repo / path).is_file() and digest(repo / path) == value
                for path, value in protected.items()), f"{case}: protected input changed")

    visible = run([sys.executable, "-m", "pytest", "-q", "tests",
                   f"--junitxml={temp / 'visible.xml'}"], repo, env)
    n_pass, failures = junit(temp / "visible.xml")
    expected = [FAIL_TO_PASS] if case == "unpatched" else []
    require(visible.returncode == (1 if expected else 0) and failures == expected
            and n_pass == (9 if expected else 11 if case == "reference_fix" else 10),
            f"{case}: unexpected visible tests\n{visible.stdout}")

    model, predictions = temp / "model.joblib", temp / "predictions.csv"
    successful([sys.executable, "-m", "churn.train", "--data", "data/train.csv",
                "--out", model], repo, env)
    successful([sys.executable, "-m", "churn.predict", "--model", model,
                "--input", "data/holdout_features.csv", "--out", predictions], repo, env)
    env.update({"CHURN_MODEL": str(model), "CHURN_PREDICTIONS": str(predictions),
                "CHURN_HOLDOUT_FEATURES": str(repo / "data/holdout_features.csv")})
    sealed = run([sys.executable, "-m", "pytest", "-q", "-c", repo / "pytest.ini",
                  root / "sealed/tests", f"--junitxml={temp / 'sealed.xml'}"], repo, env)
    sealed_pass, sealed_fail = junit(temp / "sealed.xml")
    require(sealed.returncode == (0 if case == "reference_fix" else 1)
            and sealed_pass + len(sealed_fail) == 2
            and sealed_pass == (2 if case == "reference_fix" else 0),
            f"{case}: unexpected sealed tests\n{sealed.stdout}")

    # Exercise the same standalone probe that can be copied into a runner.
    harness = temp / "parity_check.py"
    shutil.copyfile(root / "sealed/harness/parity_check.py", harness)
    parity_path = temp / "parity.json"
    parity_run = run([sys.executable, harness, "--contract", repo / "autopilot.yaml",
                      "--model", model, "--input", repo / "data/holdout_features.csv",
                      "--predictions", predictions, "--out", parity_path], repo, env)
    require(parity_path.is_file(), f"{case}: missing parity report\n{parity_run.stdout}")
    parity = json.loads(parity_path.read_text(encoding="utf-8"))
    require(parity["n_rows"] == 200, f"{case}: parity sample count")
    if case == "reference_fix":
        require(parity_run.returncode == 0 and parity["mismatches"] == 0
                and not parity["errors"] and parity["max_abs_diff"] <= 1e-9,
                f"reference parity: {parity}")
    elif case == "wrong_fix":
        require(parity_run.returncode == 1 and not parity["errors"]
                and parity["max_abs_diff"] > 1e-6 and parity["mismatches"] > 0,
                f"wrong patch did not expose semantic error: {parity}")
    else:
        require(parity_run.returncode == 1 and len(parity["errors"]) == 200
                and all(error["error"] == "ValueError" for error in parity["errors"]),
                "unpatched serving did not consistently raise ValueError")

    labels = pd.read_csv(root / "sealed/holdout_labels.csv")
    scores = pd.read_csv(predictions)
    require(list(scores.columns) == ["customer_id", "score"], "bad prediction schema")
    require(list(labels.columns) == ["customer_id", "churned"], "bad label schema")
    require(len(scores) == len(labels) == 1500 and set(scores.customer_id) == set(labels.customer_id),
            "holdout row mismatch")
    aligned = labels.merge(scores, on="customer_id", validate="one_to_one")
    require(aligned.score.map(lambda x: math.isfinite(x) and 0 <= x <= 1).all(),
            "invalid probabilities")
    auc = roc_auc_score(aligned.churned, aligned.score)
    if case == "reference_fix":
        require(auc >= 0.75, f"AUC too low: {auc}")
    return {"case": case, "visible": f"{n_pass} pass/{len(failures)} fail",
            "sealed": f"{sealed_pass} pass/{len(sealed_fail)} fail",
            "parity": parity, "auc": auc, "prediction_digest": digest(predictions)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fixture", type=Path)
    args = parser.parse_args()
    root = args.fixture.resolve()
    try:
        validate_inputs(root)
        results = []
        for case in ("unpatched", "wrong_fix", "reference_fix"):
            with tempfile.TemporaryDirectory(prefix=f"churn-{case}-") as directory:
                results.append(prove(root, case, Path(directory)))
        require(len({result["prediction_digest"] for result in results}) == 1,
                "patches changed batch predictions")
        print(f"{'case':<15} {'visible':<16} {'sealed':<16} {'max_abs_diff':<17} {'mismatches':<11} AUC")
        for result in results:
            parity = result["parity"]
            difference = "ValueError" if parity["max_abs_diff"] is None else f"{parity['max_abs_diff']:.10g}"
            print(f"{result['case']:<15} {result['visible']:<16} {result['sealed']:<16} "
                  f"{difference:<17} {parity['mismatches']:<11} {result['auc']:.6f}")
        print("PASS: all three outcomes match; batch predictions are identical across patches.")
        return 0
    except (AssertionError, RuntimeError, OSError, ValueError, ET.ParseError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
