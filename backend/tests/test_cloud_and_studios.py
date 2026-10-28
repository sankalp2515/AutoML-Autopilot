"""S3 cloud seam + studios endpoint smoke (no live cloud)."""

import pytest

from app.core import cloud


def test_is_remote():
    assert cloud.is_remote("s3://bucket/key.csv")
    assert not cloud.is_remote("/data/x/dataset.csv")
    assert not cloud.is_remote("")


def test_localize_passthrough_local():
    assert cloud.localize("/data/x/dataset.csv", "x") == "/data/x/dataset.csv"


def test_localize_malformed_s3_raises():
    with pytest.raises((ValueError, RuntimeError)):
        cloud.localize("s3://", "run1")
