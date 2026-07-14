from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

import httpx

from release_automator.errors import AutomatorError, ExternalServiceError
from release_automator.models import ChecksConfig, ReleaseInfo

GITHUB_API_VERSION = "2026-03-10"


def resolve_github_token() -> str:
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token
    try:
        result = subprocess.run(
            ["gh", "auth", "token"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        raise AutomatorError(
            "set GITHUB_TOKEN or authenticate GitHub CLI with `gh auth login`"
        ) from exc
    token = result.stdout.strip()
    if not token:
        raise AutomatorError("GitHub CLI returned an empty token")
    return token


class GitHubClient:
    def __init__(
        self,
        repo_full_name: str,
        *,
        token: str | None = None,
        checks_token: str | None = None,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repo_full_name = repo_full_name
        self.owner, self.repo = repo_full_name.split("/", 1)
        self.checks_token = (
            checks_token or os.environ.get("GITHUB_CHECKS_TOKEN", "").strip() or None
        )
        self.sleep = sleep
        self.monotonic = monotonic
        root = (base_url or os.environ.get("GITHUB_API_URL") or "https://api.github.com").rstrip(
            "/"
        )
        self.client = httpx.Client(
            base_url=root,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token or resolve_github_token()}",
                "X-GitHub-Api-Version": GITHUB_API_VERSION,
                "User-Agent": "release-automator/0.3.0",
            },
            timeout=30,
            transport=transport,
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _request(
        self,
        method: str,
        path: str,
        *,
        expected: set[int] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = self.client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ExternalServiceError(f"GitHub request failed: {exc}") from exc
        accepted = expected or {200}
        if response.status_code not in accepted:
            detail = response.text[:2_000]
            raise ExternalServiceError(
                f"GitHub {method} {path} returned {response.status_code}: {detail}"
            )
        return response

    def repository(self) -> dict[str, Any]:
        return self._request("GET", f"/repos/{self.repo_full_name}").json()

    def branch_sha(self, branch: str) -> str:
        encoded = quote(branch, safe="")
        data = self._request("GET", f"/repos/{self.repo_full_name}/branches/{encoded}").json()
        return str(data["commit"]["sha"])

    def branch_exists(self, branch: str) -> bool:
        encoded = quote(branch, safe="")
        response = self.client.get(f"/repos/{self.repo_full_name}/branches/{encoded}")
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise ExternalServiceError(
                f"GitHub branch lookup returned {response.status_code}: {response.text[:2_000]}"
            )
        return True

    def list_releases(self) -> list[ReleaseInfo]:
        releases: list[ReleaseInfo] = []
        page = 1
        while True:
            data = self._request(
                "GET",
                f"/repos/{self.repo_full_name}/releases",
                params={"per_page": 100, "page": page},
            ).json()
            for item in data:
                releases.append(
                    ReleaseInfo(
                        tag_name=item["tag_name"],
                        name=item.get("name") or "",
                        prerelease=bool(item.get("prerelease")),
                        draft=bool(item.get("draft")),
                        published_at=item.get("published_at"),
                        url=item.get("html_url"),
                    )
                )
            if len(data) < 100:
                break
            page += 1
        return releases

    def release_by_tag(self, tag: str) -> dict[str, Any] | None:
        encoded = quote(tag, safe="")
        response = self.client.get(f"/repos/{self.repo_full_name}/releases/tags/{encoded}")
        if response.status_code == 404:
            return None
        if response.status_code != 200:
            raise ExternalServiceError(
                f"GitHub release lookup returned {response.status_code}: {response.text[:2_000]}"
            )
        return response.json()

    def tag_exists(self, tag: str) -> bool:
        encoded = quote(f"tags/{tag}", safe="")
        response = self.client.get(f"/repos/{self.repo_full_name}/git/ref/{encoded}")
        if response.status_code == 404:
            return False
        if response.status_code != 200:
            raise ExternalServiceError(
                f"GitHub tag lookup returned {response.status_code}: {response.text[:2_000]}"
            )
        return True

    def create_pull_request(
        self,
        *,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/repos/{self.repo_full_name}/pulls",
            expected={201},
            json={
                "title": title,
                "body": body,
                "head": head,
                "base": base,
                "draft": False,
                "maintainer_can_modify": True,
            },
        ).json()

    def find_pull_request(self, head: str, base: str) -> dict[str, Any] | None:
        data = self._request(
            "GET",
            f"/repos/{self.repo_full_name}/pulls",
            params={
                "state": "all",
                "head": f"{self.owner}:{head}",
                "base": base,
                "per_page": 20,
            },
        ).json()
        return data[0] if data else None

    def get_pull_request(self, number: int) -> dict[str, Any]:
        return self._request("GET", f"/repos/{self.repo_full_name}/pulls/{number}").json()

    def _check_states(self, sha: str) -> dict[str, tuple[str, str | None, str | None]]:
        headers = (
            {"Authorization": f"Bearer {self.checks_token}"} if self.checks_token else None
        )
        checks_data = self._request(
            "GET",
            f"/repos/{self.repo_full_name}/commits/{sha}/check-runs",
            params={"per_page": 100, "filter": "latest"},
            headers=headers,
        ).json()
        states: dict[str, tuple[str, str | None, str | None]] = {}
        for check in checks_data.get("check_runs", []):
            states[check["name"]] = (
                check["status"],
                check.get("conclusion"),
                check.get("html_url") or check.get("details_url"),
            )

        status_data = self._request(
            "GET",
            f"/repos/{self.repo_full_name}/commits/{sha}/status",
            headers=headers,
        ).json()
        for status in status_data.get("statuses", []):
            context = status["context"]
            if context not in states:
                state = status["state"]
                states[context] = (
                    "completed" if state != "pending" else "pending",
                    state,
                    status.get("target_url"),
                )
        return states

    def wait_for_checks(self, sha: str, config: ChecksConfig) -> dict[str, str]:
        if not config.required:
            if config.allow_no_checks:
                return {}
            raise AutomatorError(
                "no required checks configured; set checks.required or checks.allow_no_checks"
            )

        started = self.monotonic()
        discovery_deadline = started + config.discovery_timeout_seconds
        completion_deadline = started + config.completion_timeout_seconds
        required = set(config.required)
        accepted = set(config.accepted_conclusions)
        while True:
            states = self._check_states(sha)
            missing = required - states.keys()
            if missing and self.monotonic() >= discovery_deadline:
                raise ExternalServiceError(
                    "required checks did not appear: " + ", ".join(sorted(missing))
                )

            pending: list[str] = []
            for name in sorted(required - missing):
                status, conclusion, url = states[name]
                if status != "completed":
                    pending.append(name)
                    continue
                if conclusion not in accepted:
                    suffix = f" ({url})" if url else ""
                    raise ExternalServiceError(
                        f"required check {name!r} concluded {conclusion!r}{suffix}"
                    )
            if not missing and not pending:
                return {name: states[name][1] or "" for name in sorted(required)}
            if self.monotonic() >= completion_deadline:
                waiting = sorted(missing | set(pending))
                raise ExternalServiceError("timed out waiting for checks: " + ", ".join(waiting))
            self.sleep(config.poll_seconds)

    def wait_until_mergeable(self, number: int, timeout_seconds: int = 60) -> dict[str, Any]:
        deadline = self.monotonic() + timeout_seconds
        while True:
            pull = self.get_pull_request(number)
            if pull.get("merged"):
                return pull
            mergeable = pull.get("mergeable")
            if mergeable is True:
                return pull
            if mergeable is False:
                raise ExternalServiceError(
                    f"pull request is not mergeable: {pull.get('mergeable_state', 'unknown')}"
                )
            if self.monotonic() >= deadline:
                raise ExternalServiceError("GitHub did not calculate pull request mergeability")
            self.sleep(2)

    def merge_pull_request(
        self,
        number: int,
        *,
        head_sha: str,
        method: str,
        title: str,
    ) -> str:
        data = self._request(
            "PUT",
            f"/repos/{self.repo_full_name}/pulls/{number}/merge",
            json={
                "sha": head_sha,
                "merge_method": method,
                "commit_title": title,
            },
        ).json()
        if not data.get("merged"):
            raise ExternalServiceError(f"GitHub did not merge the pull request: {data}")
        return str(data["sha"])

    def delete_branch(self, branch: str) -> None:
        encoded = quote(f"heads/{branch}", safe="")
        self._request(
            "DELETE",
            f"/repos/{self.repo_full_name}/git/refs/{encoded}",
            expected={204},
        )

    def create_release(
        self,
        *,
        tag: str,
        target_sha: str,
        title: str,
        notes: str,
        prerelease: bool,
        make_latest: bool = True,
    ) -> dict[str, Any]:
        effective_make_latest = make_latest and not prerelease
        return self._request(
            "POST",
            f"/repos/{self.repo_full_name}/releases",
            expected={201},
            json={
                "tag_name": tag,
                "target_commitish": target_sha,
                "name": title,
                "body": notes,
                "draft": False,
                "prerelease": prerelease,
                "make_latest": str(effective_make_latest).lower(),
            },
        ).json()
