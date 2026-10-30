"""Local journald monitor. External uptime/alert routing is configured separately."""
import json
import shutil
import urllib.request

ready = json.load(urllib.request.urlopen("http://127.0.0.1:3135/api/v1/v5/ready", timeout=15))
disk = shutil.disk_usage("/srv/automl-data")
ok = bool(ready.get("ready")) and disk.free > 2 * 1024 ** 3
print(json.dumps({"ready": ready, "application_disk_free_bytes": disk.free, "healthy": ok}))
raise SystemExit(0 if ok else 1)
