"""v5 repair tables (tasks, candidates, attempts, receipts, events, workers) plus accounts,
workspaces, memberships and GitHub App links/deliveries (BE-19, BE-23, BE-24).

Upgrading from the v4 schema creates every missing repair_* table and adds the v5 columns
(spec, report) to a repair_tasks table created by an earlier v5 build. Idempotent, so it is
safe on a database the app already bootstrapped with create_all.

Revision ID: 0003_repair_v5
Revises: 0002_tenant_id
Create Date: 2026-10-30
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0003_repair_v5"
down_revision: Union[str, None] = "0002_tenant_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _tables():
    from app.database import Base
    from app.repair import models  # noqa: F401
    original={"repair_tasks","repair_candidates","repair_attempts","repair_receipts","repair_events",
              "repair_workers","repair_accounts","repair_workspaces","repair_memberships",
              "repair_github_links","repair_webhook_deliveries"}
    return [t for name, t in Base.metadata.tables.items() if name in original]


def upgrade() -> None:
    from app.database import Base
    from app.repair.models import ensure_columns
    bind = op.get_bind()
    ensure_columns(bind)                                   # older v5 repair_tasks without spec/report
    Base.metadata.create_all(bind, tables=_tables(), checkfirst=True)


def downgrade() -> None:
    from app.database import Base
    Base.metadata.drop_all(op.get_bind(), tables=_tables(), checkfirst=True)
