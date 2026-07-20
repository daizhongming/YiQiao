import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

pytest.importorskip("alembic", reason="alembic not installed")

import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from alembic.script import ScriptDirectory  # noqa: E402

_SERVER_DIR = Path(__file__).resolve().parents[1] / "server"
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))


def _alembic_config():
    config = Config(str(_SERVER_DIR / "alembic.ini"))
    config.set_main_option("script_location", (_SERVER_DIR / "alembic").as_posix())
    config.set_main_option("path_separator", "os")
    return config


def _create_pre_017_schema(engine):
    metadata = sa.MetaData()
    users = sa.Table(
        "users",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", sa.String(20), nullable=False, server_default="admin"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("email"),
    )
    api_keys = sa.Table(
        "api_keys",
        metadata,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("key_prefix", sa.String(12), nullable=False),
        sa.Column("key_hash", sa.Text(), nullable=False),
        sa.Column("label", sa.String(255), nullable=False),
        sa.Column("project_id", sa.String(128), nullable=False, server_default="default-project"),
        sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    metadata.create_all(engine)
    return users, api_keys


def test_revision_chain_is_linear_and_017_is_the_only_head():
    script = ScriptDirectory.from_config(_alembic_config())
    assert script.get_heads() == ["017"]

    chain = []
    revision = script.get_revision("017")
    while revision is not None:
        chain.append(revision.revision)
        assert revision.is_branch_point is False
        down_revision = revision.down_revision
        assert down_revision is None or isinstance(down_revision, str)
        revision = script.get_revision(down_revision) if down_revision else None

    assert chain == [f"{number:03d}" for number in range(17, 0, -1)]


def test_017_upgrade_downgrade_round_trip_preserves_existing_api_keys(tmp_path, monkeypatch):
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = sa.create_engine(database_url)
    users, api_keys = _create_pre_017_schema(engine)
    user_id = uuid.uuid4()
    key_id = uuid.uuid4()
    with engine.begin() as connection:
        connection.execute(
            users.insert().values(
                id=user_id,
                name="Admin",
                email="admin@example.com",
                password_hash="unused",
                role="admin",
                created_at=datetime.now(timezone.utc),
            )
        )
        connection.execute(
            api_keys.insert().values(
                id=key_id,
                key_prefix="yqsk_legacy1",
                key_hash="legacy-hash",
                label="Legacy",
                project_id="default-project",
                created_by=user_id,
                created_at=datetime.now(timezone.utc),
            )
        )

    monkeypatch.setenv("DATABASE_URL", database_url)
    config = _alembic_config()
    command.stamp(config, "016")
    command.upgrade(config, "017")

    inspector = sa.inspect(engine)
    columns = {column["name"]: column for column in inspector.get_columns("api_keys")}
    assert {"key_type", "scopes", "expires_at"}.issubset(columns)
    assert "boss_helper_pairings" in inspector.get_table_names()
    pairing_indexes = {index["name"]: index for index in inspector.get_indexes("boss_helper_pairings")}
    assert {
        "ix_boss_helper_pairings_device_code_hash",
        "ix_boss_helper_pairings_user_code_hash",
        "ix_boss_helper_pairings_status",
        "ix_boss_helper_pairings_project_id",
        "ix_boss_helper_pairings_api_key_id",
        "ix_boss_helper_pairings_expires_at",
    }.issubset(pairing_indexes)
    assert pairing_indexes["ix_boss_helper_pairings_device_code_hash"]["unique"] == 1
    assert pairing_indexes["ix_boss_helper_pairings_user_code_hash"]["unique"] == 1

    with engine.connect() as connection:
        migrated = connection.execute(
            sa.text("SELECT key_type, scopes, expires_at FROM api_keys WHERE id = :id"),
            {"id": key_id.hex},
        ).one()
        assert migrated.key_type == "standard"
        assert migrated.scopes is None
        assert migrated.expires_at is None
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "017"

    command.downgrade(config, "016")
    inspector = sa.inspect(engine)
    assert "boss_helper_pairings" not in inspector.get_table_names()
    assert {"key_type", "scopes", "expires_at"}.isdisjoint(
        {column["name"] for column in inspector.get_columns("api_keys")}
    )
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT label FROM api_keys WHERE id = :id"), {"id": key_id.hex}) == "Legacy"
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "016"

    command.upgrade(config, "head")
    inspector = sa.inspect(engine)
    assert "boss_helper_pairings" in inspector.get_table_names()
    assert {"key_type", "scopes", "expires_at"}.issubset(
        {column["name"] for column in inspector.get_columns("api_keys")}
    )
    engine.dispose()
