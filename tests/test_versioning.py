from __future__ import annotations

import pytest

from release_automator.errors import AutomatorError
from release_automator.models import ReleaseChannel
from release_automator.versioning import parse_semver, validate_suggested_version


def test_semver_orders_prereleases_and_stable() -> None:
    assert parse_semver("v1.0.1-beta2") < parse_semver("v1.0.1-beta3")
    assert parse_semver("v1.0.1-beta9") < parse_semver("v1.0.1-beta10")
    assert parse_semver("v1.0.1-beta3") < parse_semver("v1.0.1")
    assert parse_semver("v1.0.1") < parse_semver("v1.1.0")


def test_validate_suggested_version_warns_for_legacy_tags() -> None:
    warnings = validate_suggested_version(
        "v1.0.1",
        ReleaseChannel.STABLE,
        ["v1.0.1-beta2", "v.9", "v0.81"],
        "v",
    )
    assert warnings == [
        "ignored non-SemVer release tag: v.9",
        "ignored non-SemVer release tag: v0.81",
    ]


@pytest.mark.parametrize(
    ("proposed", "channel"),
    [
        ("v1.0.0", ReleaseChannel.STABLE),
        ("v1.0.1-beta3", ReleaseChannel.STABLE),
        ("v1.0.1", ReleaseChannel.PRERELEASE),
    ],
)
def test_validate_suggested_version_rejects_invalid_progression(
    proposed: str, channel: ReleaseChannel
) -> None:
    with pytest.raises(AutomatorError):
        validate_suggested_version(
            proposed,
            channel,
            ["v1.0.0", "v1.0.1-beta2"],
            "v",
        )
