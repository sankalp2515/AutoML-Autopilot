"""Cloud stack launcher. Secrets live outside source; local recording is untouched."""
import argparse
import json
import os
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit

from v5_stack import Stack, ROOT


def cloud_stack(path):
    source = Path(path)
    if source.stat().st_mode & 0o077:
        raise ValueError("cloud configuration must have mode 0600")
    config = json.loads(source.read_text())
    if not all(isinstance(k, str) and isinstance(v, str) for k, v in config.items()):
        raise ValueError("cloud configuration must contain string values")
    origin = config["PUBLIC_ORIGIN"]
    if urlsplit(origin).scheme != "https" or urlsplit(origin).path:
        raise ValueError("public HTTPS origin required")
    os.environ.update(config)
    state = Path("/srv/automl-data/operator")
    sandbox = Path("/srv/automl-data/nebius-state")
    sandbox.mkdir(exist_ok=True)
    os.chown(sandbox, 10001, 10001)
    sandbox.chmod(0o700)
    stack = Stack(SimpleNamespace(command="up", project="automl-judge", port=3135,
                                  state_dir=str(state), judge_only=True, allow_live=True))
    stack.env.update(PUBLIC_ORIGIN=origin, WEB_BIND="127.0.0.1", REPAIR_NEBIUS_STATE_DIR=str(sandbox))
    stack.prefix.extend(["-f", str(ROOT / "deploy/gcp/nebius.compose.yml")])
    return stack


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["up", "doctor", "backup", "warm", "stop", "web"])
    p.add_argument("--config", default="/etc/automl/provider.json")
    p.add_argument("--directory")
    p.add_argument("--no-build", action="store_true")
    args = p.parse_args()
    stack = cloud_stack(args.config)
    if args.command == "up":
        result = stack.up(build=not args.no_build)
    elif args.command == "doctor":
        result = stack.doctor()
    elif args.command == "backup":
        if not args.directory:
            p.error("backup requires --directory")
        from v5_cloud_backup import backup_cloud
        result = backup_cloud(stack, args.directory, args.config)
    elif args.command == "stop":
        stack.compose("stop")
        result = {"stopped": True, "data_deleted": False}
    elif args.command == "web":
        stack.compose("build", "web")
        stack.compose("up", "-d", "--no-deps", "--wait", "--wait-timeout", "120", "web")
        result = {"web_refreshed": True, "backend_restarted": False}
    else:
        result = stack.operator("warm")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
