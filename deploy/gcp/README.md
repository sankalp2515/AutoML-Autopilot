# Google Cloud host with Nebius inference and execution

This is the judge deployment's single-host topology. Google Cloud runs the control plane; Token Factory runs Nemotron; Nebius Sandboxes runs candidate/trusted execution. [Actual validation and limitations](../../docs/NEBIUS_DEPLOYMENT_VALIDATION.md) · [Architecture](../../docs/V5_ARCHITECTURE.md).

## Host prerequisites

- Dedicated Ubuntu 24.04 CPU VM; the validated deployment uses `e2-standard-4`, 30-GiB boot disk and a **distinct empty 20-GiB data disk at `/dev/sdb`**.
- Source checked out at `/opt/automl`. Python 3 and Docker Compose v2 supporting `!override` / `!reset`.
- Public hostname resolving to the reserved IP. Allow inbound 80/443 for Caddy and certificate issuance; use IAP-restricted SSH. Do not expose PostgreSQL, backend or sandbox ports. The validated VM has no attached service account.
- A real Token Factory API key and a sandbox-enabled project ID. Live judge mode also requires a private invite. Use public code and synthetic data because of the sandbox beta restrictions.

Infrastructure provisioning is an operator step; these files are not a general Terraform module. `bootstrap.sh` validates the second disk's size, empty filesystem and label before formatting it, mounts `/srv/automl-data`, installs dependencies and configures Docker. Do not run it on a shared machine or substitute an existing data disk.

## Configure and start

On the dedicated host:

```bash
cd /opt/automl
sudo bash deploy/gcp/bootstrap.sh
sudo install -m 0600 deploy/gcp/provider.example.json /etc/automl/provider.json
sudoedit /etc/automl/provider.json
# Set the HTTPS origin, actual credentials and a private invite in that file.
sudo python3 scripts/v5_cloud.py up
sudo python3 deploy/gcp/install_services.py
sudo systemctl start automl.service
sudo python3 scripts/v5_cloud.py doctor
```

All configuration values are strings. Keep the real file root-owned with mode 0600 outside Git. Do not rerun the example-file installation over configured credentials. Never print Compose's expanded environment in public logs. The launcher does not read the developer's `.env`; it generates internal credentials in `/srv/automl-data/operator`.

The cloud overlay replaces the Docker socket mount entirely. The sandbox gateway owns only its sandbox control credentials; candidate code receives none. Model keys go to the repair worker. The legacy worker receives no provider credentials.

`install_services.py` configures HTTPS, boot startup, daily quiesced application backups and local health checks. Configure the Google disk snapshot policy and external email monitoring separately. `scripts/configure_gcp_alerts.py` uses the active gcloud identity and `httpx` to create HTTPS uptime and gross-cost budget alerts; choose the actual billing currency and keep its resource-ID output private. Budget alerts are notifications, not spend caps.

## Update and recover

```bash
# After reviewing/updating source; rebuild only the web service.
sudo python3 scripts/v5_cloud.py web
# Full service/image update: schedule while no repairs are active.
sudo python3 scripts/v5_cloud.py up
# Consistent backup: refuses active repair tasks and restarts services afterward.
sudo python3 scripts/v5_cloud.py backup --directory /srv/automl-data/backups/UNIQUE_BACKUP_NAME
# Restore into a NEW empty private project. Production is not replaced.
sudo python3 scripts/v5_cloud_restore_drill.py /srv/automl-data/backups/UNIQUE_BACKUP_NAME \
  --project automl-cloud-restore-UNIQUE --output /srv/automl-data/operator/restore-result.json
```

The restore drill verifies receipt/ledger consistency with live model calls disabled, then stops the restored containers. It retains their private volumes for inspection. A complete disaster recovery also needs the backed-up private cloud/operator state and still-retained Nebius image identities. UUID metadata is not an image export and cannot restore expired provider images.

Both disks need monitoring: Docker volumes are bounded by the 20-GiB data filesystem, while Docker's containerd service-image store can remain on the boot disk. Local backups and provider metadata have separate retention from off-host snapshots. See the operations guide before changing credentials, deleting data or restoring over a retained installation.
