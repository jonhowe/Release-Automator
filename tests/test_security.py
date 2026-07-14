from __future__ import annotations

import pytest

from release_automator.errors import AutomatorError
from release_automator.security import (
    assert_no_secrets,
    assert_payload_size,
    assert_safe_paths,
    redact_secrets,
)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.production",
        ".env.example.bak",
        "config/prod.pem",
        "secrets/id_rsa",
    ],
)
def test_sensitive_paths_are_blocked(path: str) -> None:
    with pytest.raises(AutomatorError):
        assert_safe_paths([path])


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        ".env.sample",
        ".env.docker.example",
        "config/.env.production.sample",
    ],
)
def test_example_environment_file_is_allowed(path: str) -> None:
    assert_safe_paths([path])


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("sk-" + "exampleexampleexampleexample", id="openai-key"),
        pytest.param("ghp_" + "abcdefghijklmnopqrstuvwxyz123456", id="github-token"),
        pytest.param("-----BEGIN " + "PRIVATE KEY-----", id="private-key"),
    ],
)
def test_secret_content_is_blocked(value: str) -> None:
    with pytest.raises(AutomatorError):
        assert_no_secrets(value)


def test_secret_content_is_redacted() -> None:
    token = "ghp_" + "abcdefghijklmnopqrstuvwxyz123456"
    redacted, detected = redact_secrets(f"token={token}")

    assert redacted == "token=<REDACTED GITHUB TOKEN>"
    assert detected == ["GitHub token"]
    assert token not in redacted


def test_payload_limit_is_enforced() -> None:
    with pytest.raises(AutomatorError):
        assert_payload_size(b"1234", 3)
