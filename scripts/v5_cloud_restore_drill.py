"""Restore a cloud backup into a separate empty, loopback-only project; no inference.

The original deployment is never stopped by this script. Stack.restore refuses
existing target data. Restored containers are stopped after comparison, retaining
their volumes for operator inspection. Credentials and manifests stay private.
"""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace

from v5_stack import Stack, sha, command


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory")
    p.add_argument("--project", default="automl-cloud-restore-drill")
    p.add_argument("--port", type=int, default=3136)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    if not args.project.startswith("automl-cloud-restore-") or not args.project.replace("-", "").isalnum():
        p.error("use a distinct automl-cloud-restore-* project")
    if not 1024 <= args.port <= 65535 or args.port == 3135:
        p.error("choose a distinct unprivileged loopback port")
    source = Path(args.directory).resolve()
    cloud = source / "cloud"
    metadata = json.loads((cloud / "manifest.json").read_text())
    expected = {"provider.json", "operator.json", "nebius-state.tar"}
    if set(metadata["files"]) != expected:
        raise ValueError("unexpected cloud backup members")
    for name, digest in metadata["files"].items():
        if sha(cloud / name) != digest:
            raise ValueError("cloud state checksum mismatch")
    existing = command(["docker", "ps", "-a", "--filter",
                        "label=com.docker.compose.project=" + args.project, "--format", "{{.ID}}"])
    volumes = command(["docker", "volume", "ls", "--format", "{{.Name}}"])
    if existing.strip() or any(name.startswith(args.project + "_") for name in volumes.decode().splitlines()):
        raise ValueError("restore drill refuses existing target containers or volumes")
    state = Path("/srv/automl-data") / args.project
    state.mkdir(mode=0o700, exist_ok=False)
    original = json.loads((cloud / "operator.json").read_text())
    original["project"] = args.project
    operator = state / "operator.json"
    operator.write_text(json.dumps(original))
    operator.chmod(0o600)
    # Bind explicitly to loopback and disable every external provider. The
    # restore tests persisted evidence, not execution or image resurrection.
    os.environ["WEB_BIND"] = "127.0.0.1"
    stack = Stack(SimpleNamespace(command="restore", project=args.project,
                  port=args.port, state_dir=str(state), judge_only=True, allow_live=False))
    try:
        result = stack.restore(str(source))
        result.update(new_model_calls=0, cloud_state_checksums_valid=True,
                      provider_image_rehydration_tested=False)
        target = Path(args.output)
        target.write_text(json.dumps(result, indent=2) + "\n")
        target.chmod(0o600)
        print(json.dumps({"restored": True, "tasks": result["snapshot"]["tasks"],
              "persisted_receipts": len(result["snapshot"]["receipt_hashes"]),
              "identical_receipt_hashes": result["identical_receipt_hashes"],
              "identical_replay_hashes": result["identical_replay_hashes"], "new_model_calls": 0}))
    finally:
        stack.compose("stop")


if __name__ == "__main__":
    main()
