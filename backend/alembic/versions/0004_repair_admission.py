"""Atomic task creation keys and estimated spend/token reservations."""
from alembic import op

revision="0004_repair_admission"
down_revision="0003_repair_v5"
branch_labels=None
depends_on=None


def upgrade():
    from app.database import Base
    from app.repair.models import AdmissionLock,Reservation,CreateKey
    Base.metadata.create_all(op.get_bind(),tables=[AdmissionLock.__table__,Reservation.__table__,CreateKey.__table__],checkfirst=True)


def downgrade():
    from app.database import Base
    from app.repair.models import AdmissionLock,Reservation,CreateKey
    Base.metadata.drop_all(op.get_bind(),tables=[CreateKey.__table__,Reservation.__table__,AdmissionLock.__table__],checkfirst=True)
