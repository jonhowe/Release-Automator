from __future__ import annotations

import pytest

from release_automator.errors import AutomatorError
from release_automator.security import (
    assert_no_secrets,
    assert_payload_size,
    assert_safe_paths,
)


@pytest.mark.parametrize("path", [".env", "config/prod.pem", "secrets/id_rsa"])
def test_sensitive_paths_are_blocked(path: str) -> None:
    with pytest.raises(AutomatorError):
        assert_safe_paths([path])


def test_example_environment_file_is_allowed() -> None:
    assert_safe_paths([".env.example"])


@pytest.mark.parametrize(
    "value",
    [
        "sk-exampleexampleexampleexample",
        "ghp_abcdefghijklmnopqrstuvwxyz123456",
        "-----BEGIN PRIVATE KEY-----",
    ],
)
def test_secret_content_is_blocked(value: str) -> None:
    with pytest.raises(AutomatorError):
        assert_no_secrets(value)


def test_payload_limit_is_enforced() -> None:
    with pytest.raises(AutomatorError):
        assert_payload_size(b"1234", 3)
