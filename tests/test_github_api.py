from __future__ import annotations

import json
import subprocess

import httpx
import pytest

from release_automator.errors import AutomatorError
from release_automator.github_api import GitHubClient, resolve_github_token
from release_automator.models import ChecksConfig


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


def test_github_token_environment_precedence(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setenv("GH_TOKEN", "gh-token")

    assert resolve_github_token() == "github-token"


def test_gh_token_is_supported_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "gh-token")

    assert resolve_github_token() == "gh-token"


def test_existing_cli_token_is_the_last_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    def fake_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(["gh", "auth", "token"], 0, stdout="cli-token\n")

    monkeypatch.setattr("release_automator.github_api.subprocess.run", fake_run)

    assert resolve_github_token() == "cli-token"


def test_missing_github_token_reports_headless_options(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    def failed_run(*_args: object, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError

    monkeypatch.setattr("release_automator.github_api.subprocess.run", failed_run)

    with pytest.raises(AutomatorError) as raised:
        resolve_github_token()

    message = str(raised.value)
    assert "GITHUB_TOKEN or GH_TOKEN" in message
    assert "non-interactive" in message
    assert "login" not in message


def test_wait_for_delayed_check() -> None:
    clock = Clock()
    calls = 0
    read_authorizations: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        if request.url.path.endswith("/check-runs"):
            read_authorizations.append(request.headers.get("Authorization"))
            calls += 1
            checks = []
            if calls == 2:
                checks = [{"name": "ci", "status": "in_progress", "conclusion": None}]
            if calls >= 3:
                checks = [{"name": "ci", "status": "completed", "conclusion": "success"}]
            return httpx.Response(200, json={"check_runs": checks})
        if request.url.path.endswith("/status"):
            read_authorizations.append(request.headers.get("Authorization"))
            return httpx.Response(200, json={"statuses": []})
        raise AssertionError(request.url)

    client = GitHubClient(
        "example/project",
        token="test",
        checks_token="checks-read",
        transport=httpx.MockTransport(handler),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    result = client.wait_for_checks(
        "abc",
        ChecksConfig(
            required=["ci"],
            poll_seconds=1,
            discovery_timeout_seconds=5,
            completion_timeout_seconds=10,
        ),
    )
    assert result == {"ci": "success"}
    assert set(read_authorizations) == {"Bearer checks-read"}


def test_merge_is_protected_by_head_sha() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"merged": True, "sha": "merge-sha"})

    client = GitHubClient("example/project", token="test", transport=httpx.MockTransport(handler))
    result = client.merge_pull_request(
        4,
        head_sha="head-sha",
        method="squash",
        title="A title",
    )
    assert result == "merge-sha"
    assert captured == {
        "sha": "head-sha",
        "merge_method": "squash",
        "commit_title": "A title",
    }


@pytest.mark.parametrize(
    ("prerelease", "make_latest", "expected"),
    [
        (False, True, "true"),
        (False, False, "false"),
        (True, True, "false"),
    ],
)
def test_release_latest_setting(
    prerelease: bool, make_latest: bool, expected: str
) -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(201, json={"html_url": "https://example.test/release"})

    client = GitHubClient("example/project", token="test", transport=httpx.MockTransport(handler))
    client.create_release(
        tag="v1.0.0-beta1",
        target_sha="merge-sha",
        title="v1.0.0-beta1",
        notes="notes",
        prerelease=prerelease,
        make_latest=make_latest,
    )
    assert captured["prerelease"] is prerelease
    assert captured["make_latest"] == expected
