"""what a k8s machine's nodes hold right now

Revision ID: 009_node_pools
Revises: 008_replay_datasets

A k8s machine can be a pool of nodes (a GPU type label rather than one
hostname). Its card count alone did not say how many cards were free, how many
nodes it spans, or the widest pod any one of them can take. These are read
from the cluster for people to see and to refuse a launch no node could ever
hold; whether a run is running or waiting still comes from its pod.
"""

import sqlalchemy as sa

from alembic import op

revision = "009_node_pools"
down_revision = "008_replay_datasets"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("node_count", sa.Integer(), "0"),
    ("node_gpu_max", sa.Integer(), "0"),
    ("gpu_free", sa.Integer(), None),
    ("capacity_checked_at", sa.DateTime(timezone=True), None),
)


def upgrade() -> None:
    # 001_initial materializes today's models, so a database created by this
    # chain from empty already has the columns; one created by 0.2.x does not.
    have = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("machines")}
    for name, kind, default in _COLUMNS:
        if name not in have:
            op.add_column(
                "machines",
                sa.Column(name, kind, nullable=default is None, server_default=default),
            )


def downgrade() -> None:
    for name, _, _ in reversed(_COLUMNS):
        op.drop_column("machines", name)
