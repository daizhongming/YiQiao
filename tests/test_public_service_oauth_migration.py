# This file was modified in 2026 by YiQiao contributors. See NOTICE.

import hashlib
import sys
import uuid
from datetime import datetime, timedelta, timezone
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

import models  # noqa: E402

_MIGRATION_017_SHA256 = "ecfef8f4af65120e72232c4dc845086397be2f7c5cdcedd166e1f12de12416d7"
_OAUTH_TABLES = {
    "oauth_applications",
    "oauth_device_authorizations",
    "oauth_grants",
    "oauth_refresh_tokens",
    "oauth_audit_events",
}


def _alembic_config() -> Config:
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


def test_revision_chain_is_linear_and_017_is_byte_identical():
    script = ScriptDirectory.from_config(_alembic_config())
    assert script.get_heads() == ["018"]

    chain = []
    revision = script.get_revision("018")
    while revision is not None:
        chain.append(revision.revision)
        assert revision.is_branch_point is False
        down_revision = revision.down_revision
        assert down_revision is None or isinstance(down_revision, str)
        revision = script.get_revision(down_revision) if down_revision else None

    assert chain == [f"{number:03d}" for number in range(18, 0, -1)]
    migration_017 = _SERVER_DIR / "alembic" / "versions" / "017_boss_helper_pairing.py"
    canonical_bytes = migration_017.read_bytes().replace(b"\r\n", b"\n")
    assert hashlib.sha256(canonical_bytes).hexdigest() == _MIGRATION_017_SHA256


def test_018_upgrade_retires_only_legacy_pairing_credentials(tmp_path, monkeypatch):
    database_path = tmp_path / "migration.db"
    database_url = f"sqlite:///{database_path.as_posix()}"
    engine = sa.create_engine(database_url)
    users, api_keys_pre_017 = _create_pre_017_schema(engine)
    user_id = uuid.uuid4()
    standard_before_017_id = uuid.uuid4()
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
            api_keys_pre_017.insert().values(
                id=standard_before_017_id,
                key_prefix="yqsk_std0001",
                key_hash="standard-before-017",
                label="Standard before 017",
                project_id="default-project",
                created_by=user_id,
                created_at=datetime.now(timezone.utc),
            )
        )

    monkeypatch.setenv("DATABASE_URL", database_url)
    config = _alembic_config()
    command.stamp(config, "016")
    command.upgrade(config, "017")

    metadata_017 = sa.MetaData()
    api_keys = sa.Table("api_keys", metadata_017, autoload_with=engine)
    pairings = sa.Table("boss_helper_pairings", metadata_017, autoload_with=engine)
    boss_active_id = uuid.uuid4()
    boss_already_revoked_id = uuid.uuid4()
    standard_named_boss_id = uuid.uuid4()
    unknown_id = uuid.uuid4()
    already_revoked_at = datetime(2025, 1, 2, 3, 4, tzinfo=timezone.utc)
    with engine.begin() as connection:
        connection.execute(
            api_keys.insert(),
            [
                {
                    "id": boss_active_id.hex,
                    "key_prefix": "yqsk_boss001",
                    "key_hash": "boss-active",
                    "label": "Legacy pairing",
                    "project_id": "default-project",
                    "key_type": "boss_helper",
                    "created_by": user_id.hex,
                    "revoked_at": None,
                    "created_at": datetime.now(timezone.utc),
                },
                {
                    "id": boss_already_revoked_id.hex,
                    "key_prefix": "yqsk_boss002",
                    "key_hash": "boss-revoked",
                    "label": "Already revoked",
                    "project_id": "default-project",
                    "key_type": "boss_helper",
                    "created_by": user_id.hex,
                    "revoked_at": already_revoked_at,
                    "created_at": datetime.now(timezone.utc),
                },
                {
                    "id": standard_named_boss_id.hex,
                    "key_prefix": "yqsk_std0002",
                    "key_hash": "standard-boss-name",
                    "label": "BossHelper standard key",
                    "project_id": "default-project",
                    "key_type": "standard",
                    "created_by": user_id.hex,
                    "revoked_at": None,
                    "created_at": datetime.now(timezone.utc),
                },
                {
                    "id": unknown_id.hex,
                    "key_prefix": "yqsk_future01",
                    "key_hash": "future-key-type",
                    "label": "Future extension",
                    "project_id": "default-project",
                    "key_type": "future_extension",
                    "created_by": user_id.hex,
                    "revoked_at": None,
                    "created_at": datetime.now(timezone.utc),
                },
            ],
        )
        connection.execute(
            pairings.insert().values(
                id=uuid.uuid4().hex,
                device_code_hash="d" * 64,
                user_code_hash="u" * 64,
                status="pending",
                expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
                created_at=datetime.now(timezone.utc),
                updated_at=datetime.now(timezone.utc),
            )
        )

    command.upgrade(config, "018")
    inspector = sa.inspect(engine)
    assert _OAUTH_TABLES.issubset(inspector.get_table_names())
    assert "boss_helper_pairings" not in inspector.get_table_names()
    for table_name in _OAUTH_TABLES:
        model_table = models.Base.metadata.tables[table_name]
        assert {column.name for column in model_table.columns} == {
            column["name"] for column in inspector.get_columns(table_name)
        }
        assert {index.name for index in model_table.indexes} == {
            index["name"] for index in inspector.get_indexes(table_name)
        }
        assert {
            constraint.name for constraint in model_table.constraints if isinstance(constraint, sa.CheckConstraint)
        } == {constraint["name"] for constraint in inspector.get_check_constraints(table_name)}
        assert {
            constraint.name
            for constraint in model_table.constraints
            if isinstance(constraint, sa.UniqueConstraint) and constraint.name is not None
        } == {constraint["name"] for constraint in inspector.get_unique_constraints(table_name)}

    reflected = sa.MetaData()
    applications = sa.Table("oauth_applications", reflected, autoload_with=engine)
    migrated_keys = sa.Table("api_keys", reflected, autoload_with=engine)
    with engine.connect() as connection:
        application = (
            connection.execute(sa.select(applications).where(applications.c.client_id == "boss-helper"))
            .mappings()
            .one()
        )
        assert application["display_name"] == "BossHelper"
        assert application["client_type"] == "public"
        assert application["allowed_audiences"] == ["yiqiao:memory-api"]
        assert application["allowed_scopes"] == ["memory:read", "memory:write"]

        keys = {
            uuid.UUID(str(row.id)): row
            for row in connection.execute(
                sa.select(migrated_keys.c.id, migrated_keys.c.key_type, migrated_keys.c.revoked_at)
            )
        }
        assert keys[standard_before_017_id].key_type == "standard"
        assert keys[standard_before_017_id].revoked_at is None
        assert keys[standard_named_boss_id].revoked_at is None
        assert keys[unknown_id].revoked_at is None
        assert keys[boss_active_id].revoked_at is not None
        assert keys[boss_already_revoked_id].revoked_at.replace(tzinfo=None) == already_revoked_at.replace(tzinfo=None)
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "018"

    command.downgrade(config, "017")
    inspector = sa.inspect(engine)
    assert _OAUTH_TABLES.isdisjoint(inspector.get_table_names())
    assert "boss_helper_pairings" in inspector.get_table_names()
    with engine.connect() as connection:
        assert connection.scalar(sa.text("SELECT COUNT(*) FROM boss_helper_pairings")) == 0
        assert (
            connection.scalar(sa.text("SELECT revoked_at FROM api_keys WHERE id = :id"), {"id": boss_active_id.hex})
            is not None
        )
        assert (
            connection.scalar(
                sa.text("SELECT revoked_at FROM api_keys WHERE id = :id"), {"id": standard_named_boss_id.hex}
            )
            is None
        )
        assert connection.scalar(sa.text("SELECT version_num FROM alembic_version")) == "017"
    engine.dispose()
