from __future__ import annotations

import json

import httpx

from release_automator.github_api import GitHubClient
from release_automator.models import ChecksConfig


class Clock:
    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        return self.value

    def sleep(self, seconds: float) -> None:
        self.value += seconds


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


def test_prerelease_is_not_marked_latest() -> None:
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
        prerelease=True,
    )
    assert captured["prerelease"] is True
    assert captured["make_latest"] == "false"
