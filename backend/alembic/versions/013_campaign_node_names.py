"""campaign node names: the k8s nodes a campaign's runs may land on

Revision ID: 013_campaign_node_names
Revises: 012_catalog_env_categories

A k8s machine may be a pool of nodes whose weights live on only some of them.
`campaigns.node_names` names those nodes (hostnames); empty means any node the
machine's selector matches, which is what every existing campaign means.
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "013_campaign_node_names"
down_revision = "012_catalog_env_categories"
branch_labels = None
depends_on = None

JsonType = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    # 001_initial materializes today's models, so a database created by this
    # chain from empty already has the column.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("campaigns")}
    if "node_names" in columns:
        return
    op.add_column(
        "campaigns",
        sa.Column("node_names", JsonType, nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("campaigns", "node_names")
