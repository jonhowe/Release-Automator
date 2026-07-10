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
from release_automator.workflow import create_plan


class FakeResponses:
    def parse(self, **_kwargs: object) -> SimpleNamespace:
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
    responses = FakeResponses()


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
