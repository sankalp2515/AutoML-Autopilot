#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
test "$(id -u)" = 0

# This deployment creates a distinct, empty 20-GiB data disk. Never format the
# boot disk or reuse a disk containing another filesystem.
data_disk=/dev/sdb
test -b "$data_disk"
test "$(blockdev --getsize64 "$data_disk")" = 21474836480
if ! blkid "$data_disk" >/dev/null 2>&1; then
  test -z "$(lsblk -n -o MOUNTPOINTS "$data_disk" | tr -d '[:space:]')"
  mkfs.ext4 -L automl-data "$data_disk"
fi
test "$(blkid -s LABEL -o value "$data_disk")" = automl-data
mkdir -p /srv/automl-data
grep -q '^LABEL=automl-data ' /etc/fstab || printf '%s\n' 'LABEL=automl-data /srv/automl-data ext4 defaults,nosuid,nodev 0 2' >> /etc/fstab
mountpoint -q /srv/automl-data || mount /srv/automl-data

apt-get update -qq
apt-get install -y --no-install-recommends docker.io docker-compose-v2 docker-buildx caddy python3 curl ca-certificates git
mkdir -p /etc/docker
# Docker volumes (uploaded repos, artifacts and databases) are bounded by this
# physical data filesystem independently of the OS disk. On Docker versions
# using the containerd image store, /var/lib/containerd stays on the boot disk;
# monitor both filesystems. Candidate execution runs remotely in Nebius.
python3 - <<'PY'
import json
from pathlib import Path
p=Path('/etc/docker/daemon.json')
config=json.loads(p.read_text()) if p.exists() else {}
if config.get('data-root') not in (None,'/srv/automl-data/docker'):
    raise SystemExit('refusing to change an existing Docker data root')
config.update({'data-root':'/srv/automl-data/docker','log-driver':'json-file',
               'log-opts':{'max-size':'10m','max-file':'3'}})
p.write_text(json.dumps(config,indent=2)+'\n')
PY
systemctl enable --now docker
systemctl restart docker
install -d -m 0755 /opt/automl
install -d -m 0700 /etc/automl /srv/automl-data/operator /srv/automl-data/backups
docker compose version
df -h / /srv/automl-data
