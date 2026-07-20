import json
from unittest.mock import MagicMock, patch

from server import telemetry


def _capture_properties(capture):
    client = MagicMock()
    with (
        patch.object(telemetry, "ENABLED", True),
        patch.object(telemetry, "_get_client", return_value=client),
        patch.object(telemetry, "_load_state", return_value={}),
        patch.object(telemetry, "_save_state"),
        patch.object(telemetry, "_install_id", return_value="anonymous-install"),
    ):
        capture()
    return client.capture.call_args.kwargs["properties"]


def test_admin_registration_telemetry_excludes_email_and_domain():
    properties = _capture_properties(
        lambda: telemetry.capture_admin_registered("private-person@sensitive-company.example")
    )

    serialized = json.dumps(properties, sort_keys=True)
    assert properties == {"server_version": telemetry.mem0.__version__}
    assert "private-person" not in serialized
    assert "sensitive-company" not in serialized


def test_onboarding_telemetry_excludes_email_and_free_text_use_case():
    properties = _capture_properties(
        lambda: telemetry.capture_onboarding_completed(
            "private-person@sensitive-company.example",
            "Confidential acquisition planning for Project Nightfall",
        )
    )

    serialized = json.dumps(properties, sort_keys=True)
    assert properties == {"server_version": telemetry.mem0.__version__}
    assert "private-person" not in serialized
    assert "sensitive-company" not in serialized
    assert "Nightfall" not in serialized
