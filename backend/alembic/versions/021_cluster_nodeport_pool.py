"""Candidate NodePorts for concurrently running services.

Revision ID: 021_cluster_nodeport_pool
Revises: 020_cluster_policy_node_selector
"""

import sqlalchemy as sa

from alembic import op

revision = "021_cluster_nodeport_pool"
down_revision = "020_cluster_policy_node_selector"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 001_initial creates today's models on a fresh database.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("clusters")}
    if "service_nodeport_pool" not in columns:
        op.add_column("clusters", sa.Column(
            "service_nodeport_pool", sa.String(4096), nullable=False, server_default="",
        ))


def downgrade() -> None:
    op.drop_column("clusters", "service_nodeport_pool")
