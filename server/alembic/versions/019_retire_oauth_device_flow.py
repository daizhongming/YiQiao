# This file was added in 2026 by YiQiao contributors. See NOTICE.

"""Retire the OAuth Device Flow and remove its persisted credentials.

Revision ID: 019
Revises: 018
Create Date: 2026-07-31

The upgrade intentionally invalidates every OAuth access token, refresh token,
pending device authorization, application registration, and OAuth audit row.
Those credentials cannot be restored safely by a downgrade.
"""

from typing import Sequence, Union

from alembic import op

revision: str = "019"
down_revision: Union[str, None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_table("oauth_audit_events")
    op.drop_table("oauth_refresh_tokens")
    op.drop_table("oauth_grants")
    op.drop_table("oauth_device_authorizations")
    op.drop_table("oauth_applications")


def downgrade() -> None:
    raise RuntimeError(
        "OAuth Device Flow credentials were intentionally destroyed by migration 019 and cannot be restored."
    )
