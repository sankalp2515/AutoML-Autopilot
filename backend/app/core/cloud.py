"""S3 cloud-data seam (ROADMAP S3) — datasets as CONNECTIONS, not just uploads.

When a run's dataset_path is an s3:// URI, this fetches it once into the local
/data volume so the rest of the pipeline (which reads local CSVs) is unchanged —
the single integration point for cloud datasets. boto3 is imported lazily so the
default install stays slim; if it's absent the URI is reported as unsupported.

Full cloud BURST (ECR images, EC2/EKS Ray workers, autoscaling) is deployment
configuration, not application code — this module is only the DATA plane bridge.
Upstream ETL (Airflow/Snowflake) delivers a table to S3; we consume the URI.
"""
from __future__ import annotations

import os

from app.config import settings
from app.core.logging import get_logger

_log = get_logger("cloud")


def is_remote(path: str) -> bool:
    return isinstance(path, str) and path.startswith(("s3://",))


def localize(path: str, run_id: str) -> str:
    """Return a LOCAL csv path for `path`. If it's an s3:// URI, download it into
    the run's data dir; otherwise return it unchanged. Raises on unsupported/
    unreachable remotes (the auditor surfaces it as a clean failed run)."""
    if not is_remote(path):
        return path
    if not path.startswith("s3://"):
        raise ValueError(f"Unsupported remote scheme: {path}")
    try:
        import boto3  # lazy — only needed for cloud datasets
    except Exception as exc:  # pragma: no cover
        raise RuntimeError("s3:// dataset requires boto3 (pip install boto3)") from exc

    without = path[len("s3://"):]
    bucket, _, key = without.partition("/")
    if not bucket or not key:
        raise ValueError(f"Malformed S3 URI: {path}")
    dest_dir = os.path.join(settings.DATA_DIR, run_id)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, "dataset.csv")
    s3 = boto3.client("s3", region_name=settings.AWS_REGION or None)
    _log.info("s3_download", bucket=bucket, key=key, run_id=run_id)
    s3.download_file(bucket, key, dest)
    return dest
