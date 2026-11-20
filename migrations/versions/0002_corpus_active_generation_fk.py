"""Constrain active generations to their owning corpus."""

import sqlalchemy as sa
from alembic import op

revision = "0002_corpus_active_generation_fk"
down_revision = "0001_b03_fact_store"
branch_labels = None
depends_on = None

UNIQUE_NAME = "uq_generation_corpus_id"
FOREIGN_KEY_NAME = "fk_corpora_active_generation_same_corpus"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    unique_constraints = {item["name"] for item in inspector.get_unique_constraints("generations")}
    if UNIQUE_NAME not in unique_constraints:
        op.create_unique_constraint(UNIQUE_NAME, "generations", ["corpus_id", "id"])

    inspector = sa.inspect(bind)
    foreign_keys = {item["name"] for item in inspector.get_foreign_keys("corpora")}
    if FOREIGN_KEY_NAME not in foreign_keys:
        op.create_foreign_key(
            FOREIGN_KEY_NAME,
            "corpora",
            "generations",
            ["id", "active_generation_id"],
            ["corpus_id", "id"],
            deferrable=True,
            initially="DEFERRED",
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    foreign_keys = {item["name"] for item in inspector.get_foreign_keys("corpora")}
    if FOREIGN_KEY_NAME in foreign_keys:
        op.drop_constraint(FOREIGN_KEY_NAME, "corpora", type_="foreignkey")

    inspector = sa.inspect(bind)
    unique_constraints = {item["name"] for item in inspector.get_unique_constraints("generations")}
    if UNIQUE_NAME in unique_constraints:
        op.drop_constraint(UNIQUE_NAME, "generations", type_="unique")
