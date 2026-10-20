"""run launch link: a policy's benchmark knows the launch it measures

Revision ID: 015_run_launch_link
Revises: 014_policy_secret_env

A benchmark a policy starts on one of its launches is a separate run against
that launch's endpoint. Nothing tied the two together, so when the engine
crashed mid-benchmark the benchmark kept going against a dead endpoint for
as long as LLMBench's timeouts allowed. `runs.launch_run_id` names the launch,
and the worker stops the benchmark when the launch ends.
"""

import sqlalchemy as sa

from alembic import op

revision = "015_run_launch_link"
down_revision = "014_policy_secret_env"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 001_initial materializes today's models, so a database created by this
    # chain from empty already has the column.
    columns = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("runs")}
    if "launch_run_id" in columns:
        return
    op.add_column("runs", sa.Column("launch_run_id", sa.Integer(), nullable=True))
    op.create_index("ix_runs_launch_run_id", "runs", ["launch_run_id"])
    op.create_foreign_key("fk_runs_launch_run_id", "runs", "runs", ["launch_run_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_runs_launch_run_id", "runs", type_="foreignkey")
    op.drop_index("ix_runs_launch_run_id", table_name="runs")
    op.drop_column("runs", "launch_run_id")
