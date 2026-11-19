"""Create B03 fact tables and generation membership constraints."""

from alembic import op

from knowledge_weaver.storage.schema import Base

revision = "0001_b03_fact_store"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=True)
