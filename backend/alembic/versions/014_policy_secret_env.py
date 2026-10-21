"""policy secret env: credentials a policy container needs, encrypted at rest

Revision ID: 014_policy_secret_env
Revises: 013_campaign_node_names

`policies.env` is plain JSON shown on the Policies page and rendered into the
container's launch command. An agent policy needs an LLM API key, which must
be neither: `policies.secret_env` holds such variables as one encrypted JSON
object (core/secrets.py), merged into the container's env at launch only.
"""

import sqlalchemy as sa

from alembic import op

revision = "014_policy_secret_env"
down_revision = "013_campaign_node_names"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 001_initial materializes today's models, so a database created by this
    # chain from empty already has the column.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("policies")}
    if "secret_env" in columns:
        return
    op.add_column(
        "policies",
        sa.Column("secret_env", sa.Text(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("policies", "secret_env")
