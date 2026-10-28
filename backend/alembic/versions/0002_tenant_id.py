"""add runs.tenant_id (Phase 4 multi-tenancy)

Adds the tenant owner column. Existing rows default to 'public' (single-tenant
mode), so this is backward-compatible. Replaces the old hand-run ALTER TABLE.

Revision ID: 0002_tenant_id
Revises: 0001_baseline
Create Date: 2026-06-18
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_tenant_id"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs",
        sa.Column("tenant_id", sa.String(length=64), nullable=False, server_default="public"),
    )
    op.create_index("ix_runs_tenant_id", "runs", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_runs_tenant_id", table_name="runs")
    op.drop_column("runs", "tenant_id")
