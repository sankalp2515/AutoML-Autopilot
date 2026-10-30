"""Install TLS/reboot lifecycle and quiesced daily backups on the dedicated VM."""
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

assert os.geteuid() == 0
config = json.loads(Path("/etc/automl/provider.json").read_text())
hostname = urlsplit(config["PUBLIC_ORIGIN"]).hostname
assert hostname and re.fullmatch(r"[a-z0-9.-]+", hostname)

Path("/etc/caddy/Caddyfile").write_text("""%s {
    encode zstd gzip
    header Strict-Transport-Security "max-age=2592000"
    reverse_proxy 127.0.0.1:3135 {
        transport http {
            read_timeout 960s
            write_timeout 960s
        }
    }
}
""" % hostname)
Path("/etc/systemd/system/automl.service").write_text("""[Unit]
Description=AutoML Autopilot judge application
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
RequiresMountsFor=/srv/automl-data

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=/opt/automl
ExecStart=/usr/bin/python3 /opt/automl/scripts/v5_cloud.py up --no-build
ExecStop=/usr/bin/python3 /opt/automl/scripts/v5_cloud.py stop
TimeoutStartSec=600
TimeoutStopSec=120
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
""")
Path("/etc/systemd/system/automl-backup.service").write_text("""[Unit]
Description=Quiesced application and verifier backup
After=automl.service
RequiresMountsFor=/srv/automl-data

[Service]
Type=oneshot
WorkingDirectory=/opt/automl
ExecStart=/usr/bin/python3 /opt/automl/scripts/v5_cloud_backup.py
TimeoutStartSec=900
""")
Path("/etc/systemd/system/automl-backup.timer").write_text("""[Unit]
Description=Daily judge application backup
[Timer]
OnCalendar=*-*-* 03:00:00 UTC
RandomizedDelaySec=120
Persistent=true
[Install]
WantedBy=timers.target
""")
Path("/etc/systemd/system/automl-health.service").write_text("""[Unit]
Description=Check application dependency readiness
[Service]
Type=oneshot
ExecStart=/usr/bin/python3 /opt/automl/scripts/v5_cloud_health.py
TimeoutStartSec=30
""")
Path("/etc/systemd/system/automl-health.timer").write_text("""[Unit]
Description=Judge application readiness monitor
[Timer]
OnBootSec=120
OnUnitActiveSec=60
[Install]
WantedBy=timers.target
""")
for command in (["caddy", "validate", "--config", "/etc/caddy/Caddyfile"],
                ["systemctl", "daemon-reload"], ["systemctl", "reload", "caddy"],
                ["systemctl", "enable", "automl.service"],
                ["systemctl", "enable", "--now", "automl-backup.timer", "automl-health.timer"]):
    subprocess.run(command, check=True)
print("TLS, reboot startup, backup timer and local readiness monitor installed")
