"""the frozen dataset a campaign's replay measures against

Revision ID: 010_frozen_datasets
Revises: 009_node_pools

A replay workload can name a frozen LLMBench dataset, a file that never
changes, instead of a rolling collection profile. Nothing is pinned for it;
the campaign records which one it replays, to show it.
"""

import sqlalchemy as sa

from alembic import op

revision = "010_frozen_datasets"
down_revision = "009_node_pools"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 001_initial materializes today's models, so a database created by this
    # chain from empty already has the column; one created by 0.2.x does not.
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("campaigns")}
    if "dataset_frozen" not in have:
        op.add_column(
            "campaigns",
            sa.Column("dataset_frozen", sa.String(100), nullable=False, server_default=""),
        )


def downgrade() -> None:
    op.drop_column("campaigns", "dataset_frozen")
