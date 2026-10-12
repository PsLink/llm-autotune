"""replay datasets set up from the Datasets page

Revision ID: 008_replay_datasets
Revises: 007_log_tails

A replay dataset is a collection profile on LLMBench. This table holds what
LLMBench is not told about one set up here: who set it up, and how often the
platform takes a new sample (app/datasets/refresh.py).
"""

import sqlalchemy as sa

from alembic import op

revision = "008_replay_datasets"
down_revision = "007_log_tails"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 001_initial materializes today's models, so a database created by this
    # chain from empty already has the table; one created by 0.2.x does not.
    if sa.inspect(op.get_bind()).has_table("replay_datasets"):
        return
    op.create_table(
        "replay_datasets",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("refresh_hours", sa.Integer(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("name"),
    )


def downgrade() -> None:
    op.drop_table("replay_datasets")
