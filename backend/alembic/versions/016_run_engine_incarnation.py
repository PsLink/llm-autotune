"""run engine incarnation: a policy launch knows which engine process it serves

Revision ID: 016_run_engine_incarnation
Revises: 015_run_launch_link

On Kubernetes an engine is a Deployment, which brings a dead engine back under
the same name: a crashed container is restarted in place, a deleted or evicted
pod is recreated. The launch never read as crashed, so a benchmark ran on
against a cold, restarting engine. `runs.engine_incarnation` records the pod
and restart count the launch started serving from; a change fails the launch.
"""

import sqlalchemy as sa

from alembic import op

revision = "016_run_engine_incarnation"
down_revision = "015_run_launch_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 001_initial materializes today's models, so a database created by this
    # chain from empty already has the column.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("runs")}
    if "engine_incarnation" in columns:
        return
    op.add_column(
        "runs",
        sa.Column("engine_incarnation", sa.String(500), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("runs", "engine_incarnation")
