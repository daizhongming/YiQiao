# This file was added in 2026 by YiQiao contributors. See NOTICE.

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_oauth_device_flow_surfaces_are_removed():
    removed_paths = [
        "server/oauth_service.py",
        "server/connector_protocol.py",
        "server/routers/oauth.py",
        "server/scripts/prune_oauth.py",
        "server/dashboard/src/app/(root)/dashboard/connected-apps/page.tsx",
        "server/dashboard/src/lib/public-connector-proxy.ts",
    ]
    assert all(not (ROOT / path).exists() for path in removed_paths)

    main_source = (ROOT / "server/main.py").read_text(encoding="utf-8")
    auth_source = (ROOT / "server/auth.py").read_text(encoding="utf-8")
    models_source = (ROOT / "server/models.py").read_text(encoding="utf-8")
    navigation_source = (ROOT / "server/dashboard/src/app/(root)/dashboard/components/main-nav.tsx").read_text(
        encoding="utf-8"
    )
    smoke_source = (ROOT / "scripts/full_stack_smoke.py").read_text(encoding="utf-8").casefold()

    assert "oauth_router" not in main_source
    assert 'startswith("yqoa_")' not in auth_source
    assert "oauth_applications" not in models_source
    assert "connected-apps" not in navigation_source
    assert "oauth" not in smoke_source


def test_retirement_migration_drops_all_oauth_tables():
    migration = (ROOT / "server/alembic/versions/019_retire_oauth_device_flow.py").read_text(encoding="utf-8")
    assert 'revision: str = "019"' in migration
    assert 'down_revision: Union[str, None] = "018"' in migration
    for table in (
        "oauth_audit_events",
        "oauth_refresh_tokens",
        "oauth_grants",
        "oauth_device_authorizations",
        "oauth_applications",
    ):
        assert f'op.drop_table("{table}")' in migration
