"""keep the end of captured logs in the database

Revision ID: 007_log_tails
Revises: 006_drop_gitlab_promotion

A run's and a policy container's captured logs were written only as files on
the worker's disk. Unless the API shares that volume (runLogs.enabled, which
needs ReadWriteMany storage), the UI could never show them, so a failed run
showed no log at all. The end of each log now goes into the row as well.
"""

import sqlalchemy as sa

from alembic import op

revision = "007_log_tails"
down_revision = "006_drop_gitlab_promotion"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 001_initial materializes today's models, so a database created by this
    # chain from empty already has the columns; one created by 0.2.x does not.
    inspector = sa.inspect(op.get_bind())
    for table in ("runs", "policy_sessions"):
        if "log_tail" not in {c["name"] for c in inspector.get_columns(table)}:
            op.add_column(table, sa.Column("log_tail", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("policy_sessions", "log_tail")
    op.drop_column("runs", "log_tail")
