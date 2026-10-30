"""Quiesced backup; retention only touches root-owned, named backup directories."""
from datetime import datetime, timezone
from pathlib import Path
import re
import shutil
import time
import json
import tarfile
from v5_stack import sha


def backup_cloud(stack, directory, config_path):
    def private_state(out):
        # Captured while ingress, workers and verifier remain stopped. Keep the
        # portable database manifest unchanged for the empty-project drill.
        target = out / "cloud"
        target.mkdir(mode=0o700)
        for source, name in ((Path(config_path), "provider.json"),
                             (stack.state / "operator.json", "operator.json")):
            shutil.copyfile(source, target / name)
            (target / name).chmod(0o600)
        with tarfile.open(target / "nebius-state.tar", "w") as archive:
            archive.add(stack.env["REPAIR_NEBIUS_STATE_DIR"], arcname="nebius-state", recursive=True)
        (target / "nebius-state.tar").chmod(0o600)
        manifest = {"version": 1, "files": {p.name: sha(p) for p in target.iterdir()},
                    "requires_provider_images": True,
                    "note": "Image UUID metadata cannot restore provider images after provider retention expires."}
        (target / "manifest.json").write_text(json.dumps(manifest, indent=2))
        (target / "manifest.json").chmod(0o600)
    return stack.backup(directory, extra_backup=private_state)


def main():
    from v5_cloud import cloud_stack
    root = Path("/srv/automl-data/backups").resolve()
    target = root / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_cloud(cloud_stack("/etc/automl/provider.json"), str(target), "/etc/automl/provider.json")
    for candidate in root.iterdir():
        if candidate.is_dir() and not candidate.is_symlink() and re.fullmatch(r"\d{8}T\d{6}Z", candidate.name):
            if candidate.resolve().parent == root and time.time() - candidate.stat().st_mtime > 3 * 86400:
                shutil.rmtree(candidate)
    print("Application, verifier and private cloud state backed up; local retention is three days")


if __name__ == "__main__":
    main()
