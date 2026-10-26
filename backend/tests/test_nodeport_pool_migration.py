"""Existing installs and fresh schemas both support the pool migration."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.mark.parametrize("already_present", [False, True])
def test_pool_upgrade_and_downgrade(already_present, monkeypatch):
    path = Path(__file__).parents[1] / "alembic/versions/021_cluster_nodeport_pool.py"
    spec = importlib.util.spec_from_file_location("pool_migration", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.exec_driver_sql("CREATE TABLE clusters (id INTEGER PRIMARY KEY, name TEXT)")
        conn.exec_driver_sql("INSERT INTO clusters (id, name) VALUES (1, 'existing')")
        if already_present:
            conn.exec_driver_sql(
                "ALTER TABLE clusters ADD COLUMN service_nodeport_pool VARCHAR(4096) "
                "NOT NULL DEFAULT ''"
            )
        monkeypatch.setattr(migration, "op", Operations(MigrationContext.configure(conn)))
        migration.upgrade()
        assert conn.exec_driver_sql(
            "SELECT service_nodeport_pool FROM clusters WHERE id = 1"
        ).scalar_one() == ""
        migration.downgrade()
        assert "service_nodeport_pool" not in {
            c["name"] for c in sa.inspect(conn).get_columns("clusters")
        }
    engine.dispose()
