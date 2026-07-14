from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import run_git

from release_automator.errors import PlanDriftError
from release_automator.models import (
    ChangeClass,
    ChecksConfig,
    ModelProposal,
    ReleaseChannel,
    ReleaseInfo,
    RepoConfig,
    ValidationCommand,
)
from release_automator.openai_planner import apply_overrides
from release_automator.workflow import create_plan, render_plan


class FakeResponses:
    def __init__(self) -> None:
        self.last_kwargs: dict[str, object] | None = None

    def parse(self, **kwargs: object) -> SimpleNamespace:
        self.last_kwargs = kwargs
        return SimpleNamespace(
            output_parsed=ModelProposal(
                branch_slug="add-release-automation",
                commit_message="Add release automation",
                pr_title="Add release automation",
                pr_body=(
                    "## Summary\n\n- Add automation.\n\n"
                    "## Why\n\nSafer releases.\n\n"
                    "## Validation\n\n- Tests passed.\n\n"
                    "## Scope\n\nSelected files only."
                ),
                change_class=ChangeClass.FEATURE,
                suggested_version="v1.1.0",
                release_channel=ReleaseChannel.STABLE,
                version_rationale="Backward-compatible functionality.",
                release_notes="## Changes\n\n- Add release automation.",
            )
        )


class FakeOpenAI:
    def __init__(self) -> None:
        self.responses = FakeResponses()


class FakeGitHub:
    def __init__(self, base_sha: str) -> None:
        self.base_sha = base_sha

    def repository(self) -> dict[str, str]:
        return {"default_branch": "main"}

    def branch_sha(self, _branch: str) -> str:
        return self.base_sha

    def branch_exists(self, _branch: str) -> bool:
        return False

    def list_releases(self) -> list[ReleaseInfo]:
        return [ReleaseInfo(tag_name="v1.0.0")]

    def tag_exists(self, _tag: str) -> bool:
        return False


def test_plan_freezes_metadata_without_git_writes(git_repository: Path) -> None:
    (git_repository / "feature.py").write_text("enabled = True\n", encoding="utf-8")
    base_sha = run_git(git_repository, "rev-parse", "HEAD")
    config = RepoConfig(checks=ChecksConfig(required=["ci"]))

    plan = create_plan(
        repo_path=git_repository,
        include=[Path("feature.py")],
        config=config,
        no_release=False,
        openai_client=FakeOpenAI(),
        github_client=FakeGitHub(base_sha),  # type: ignore[arg-type]
    )

    assert plan.plan_id
    assert plan.branch_name == "agent/add-release-automation"
    assert run_git(git_repository, "branch", "--show-current") == "main"
    assert run_git(git_repository, "diff", "--cached", "--name-only") == ""
    assert (git_repository / ".git" / "release-automator" / "plans").is_dir()

    rendered = render_plan(plan)
    assert "## Execution actions after approval" in rendered
    assert "Create and check out local branch `agent/add-release-automation`" in rendered
    assert "Stage only `feature.py`" in rendered
    assert "Create commit `Add release automation`" in rendered
    assert "Push `agent/add-release-automation` to `origin`" in rendered
    assert "open a non-draft pull request" in rendered
    assert "Wait for required checks `ci`" in rendered
    assert "`squash`-merge it using the frozen commit SHA" in rendered
    assert "Delete remote branch `agent/add-release-automation`" in rendered
    assert "Create a non-draft GitHub Release and tag `v1.1.0`" in rendered
    assert "Mark as latest: `true`" in rendered
    assert "set make-latest to `true`" in rendered
    assert "delete local branch `agent/add-release-automation`" in rendered

    no_release = render_plan(plan.model_copy(update={"release_enabled": False}))
    assert "Do not create a GitHub Release or tag." in no_release

    overridden = apply_overrides(plan.proposal, {"suggested_version": "v1.0.1"})
    assert overridden.suggested_version == "v1.0.1"
    assert overridden.version_rationale == (
        "Release version explicitly overridden to v1.0.1 during planning."
    )


def test_plan_can_freeze_stable_release_as_not_latest(git_repository: Path) -> None:
    (git_repository / "feature.py").write_text("enabled = True\n", encoding="utf-8")
    base_sha = run_git(git_repository, "rev-parse", "HEAD")

    plan = create_plan(
        repo_path=git_repository,
        include=[Path("feature.py")],
        config=RepoConfig(checks=ChecksConfig(required=["ci"])),
        no_release=False,
        no_latest=True,
        openai_client=FakeOpenAI(),
        github_client=FakeGitHub(base_sha),  # type: ignore[arg-type]
    )

    assert plan.release_make_latest is False
    rendered = render_plan(plan)
    assert "Mark as latest: `false`" in rendered
    assert "set make-latest to `false`" in rendered


def test_plan_stops_if_validation_expands_selected_scope(git_repository: Path) -> None:
    source = git_repository / "src"
    source.mkdir()
    (source / "one.py").write_text("one = 1\n", encoding="utf-8")
    base_sha = run_git(git_repository, "rev-parse", "HEAD")
    config = RepoConfig(
        checks=ChecksConfig(required=["ci"]),
        validation=[
            ValidationCommand(
                name="generate file",
                argv=[
                    sys.executable,
                    "-c",
                    "from pathlib import Path; Path('src/two.py').write_text('two = 2\\n')",
                ],
            )
        ],
    )

    with pytest.raises(PlanDriftError, match="changed the set of files"):
        create_plan(
            repo_path=git_repository,
            include=[Path("src")],
            config=config,
            no_release=False,
            openai_client=FakeOpenAI(),
            github_client=FakeGitHub(base_sha),  # type: ignore[arg-type]
        )


def test_plan_redacts_secret_like_diff_before_model_request(git_repository: Path) -> None:
    token = "ghp_" + "abcdefghijklmnopqrstuvwxyz123456"
    (git_repository / "cleanup.py").write_text(f'token = "{token}"\n', encoding="utf-8")
    base_sha = run_git(git_repository, "rev-parse", "HEAD")
    client = FakeOpenAI()

    plan = create_plan(
        repo_path=git_repository,
        include=[Path("cleanup.py")],
        config=RepoConfig(checks=ChecksConfig(required=["ci"])),
        no_release=False,
        openai_client=client,
        github_client=FakeGitHub(base_sha),  # type: ignore[arg-type]
    )

    assert plan.redacted_secret_types == ["GitHub token"]
    assert client.responses.last_kwargs is not None
    assert token not in str(client.responses.last_kwargs)
    assert "<REDACTED GITHUB TOKEN>" in str(client.responses.last_kwargs)
    assert "Redacted from OpenAI input: `GitHub token`" in render_plan(plan)


def test_plan_does_not_persist_remote_credentials(git_repository: Path) -> None:
    credential = "github_pat_credential-value"
    run_git(
        git_repository,
        "remote",
        "set-url",
        "origin",
        f"https://x-access-token:{credential}@github.com/example/project.git",
    )
    (git_repository / "feature.py").write_text("enabled = True\n", encoding="utf-8")
    base_sha = run_git(git_repository, "rev-parse", "HEAD")

    plan = create_plan(
        repo_path=git_repository,
        include=[Path("feature.py")],
        config=RepoConfig(checks=ChecksConfig(required=["ci"])),
        no_release=False,
        openai_client=FakeOpenAI(),
        github_client=FakeGitHub(base_sha),  # type: ignore[arg-type]
    )

    assert plan.remote_url == "https://github.com/example/project.git"
    assert credential not in plan.model_dump_json()
