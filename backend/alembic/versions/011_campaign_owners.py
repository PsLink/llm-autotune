"""campaign owners: the campaigns plugins run for something of their own

Revision ID: 011_campaign_owners
Revises: 010_frozen_datasets

A plugin runs its work as managed campaigns. This table records which plugin
owns a campaign, what to call it, and the page it belongs to, so the campaign
list can keep those apart from the campaigns people create.
"""

import sqlalchemy as sa

from alembic import op

revision = "011_campaign_owners"
down_revision = "010_frozen_datasets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 001_initial materializes today's models, so a database created by this
    # chain from empty already has the table; one created by 0.2.x does not.
    if "campaign_owners" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "campaign_owners",
        sa.Column(
            "campaign_id",
            sa.Integer(),
            sa.ForeignKey("campaigns.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("plugin", sa.String(40), nullable=False),
        sa.Column("label", sa.String(200), nullable=False, server_default=""),
        sa.Column("link", sa.String(300), nullable=False, server_default=""),
    )
    op.create_index("ix_campaign_owners_plugin", "campaign_owners", ["plugin"])


def downgrade() -> None:
    op.drop_index("ix_campaign_owners_plugin", table_name="campaign_owners")
    op.drop_table("campaign_owners")
