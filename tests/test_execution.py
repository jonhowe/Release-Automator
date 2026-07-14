from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from conftest import run_git

from release_automator.git_ops import GitRepo
from release_automator.models import (
    ChangeClass,
    ChecksConfig,
    FrozenPlan,
    GitConfig,
    ModelProposal,
    Phase,
    ReleaseChannel,
    RepoConfig,
    RunState,
)
from release_automator.workflow import run_plan


class SuccessfulGitHub:
    def __init__(self, base_sha: str, head_sha: str) -> None:
        self.base_sha = base_sha
        self.head_sha = head_sha
        self.branch_lookups = 0
        self.deleted = False

    def branch_sha(self, branch: str) -> str:
        assert branch == "main"
        return self.base_sha

    def branch_exists(self, _branch: str) -> bool:
        self.branch_lookups += 1
        return self.branch_lookups > 1

    def find_pull_request(self, _head: str, _base: str) -> None:
        return None

    def create_pull_request(self, **_kwargs: object) -> dict[str, object]:
        return {
            "number": 7,
            "html_url": "https://example.test/pull/7",
            "head": {"sha": self.head_sha},
            "merged": False,
        }

    def get_pull_request(self, _number: int) -> dict[str, object]:
        return {"head": {"sha": self.head_sha}, "merged": False}

    def wait_for_checks(self, _sha: str, _config: ChecksConfig) -> None:
        return None

    def wait_until_mergeable(self, _number: int) -> dict[str, object]:
        return {"head": {"sha": self.head_sha}, "merged": False}

    def merge_pull_request(self, *_args: object, **_kwargs: object) -> str:
        return "merge-sha"

    def delete_branch(self, _branch: str) -> None:
        self.deleted = True


class ReleaseGitHub:
    def __init__(self) -> None:
        self.create_kwargs: dict[str, object] | None = None

    def release_by_tag(self, _tag: str) -> None:
        return None

    def tag_exists(self, _tag: str) -> bool:
        return False

    def create_release(self, **kwargs: object) -> dict[str, str]:
        self.create_kwargs = kwargs
        return {"html_url": "https://example.test/releases/v1.1.0"}


def test_run_recovers_commit_created_before_state_save(
    git_repository: Path, tmp_path: Path
) -> None:
    bare = tmp_path / "origin.git"
    run_git(tmp_path, "init", "--bare", str(bare))
    run_git(git_repository, "remote", "set-url", "origin", str(bare))
    run_git(git_repository, "push", "-u", "origin", "main")

    feature = git_repository / "feature.py"
    feature.write_text("enabled = True\n", encoding="utf-8")
    repo = GitRepo(git_repository)
    base_sha = repo.head_sha()
    snapshot = repo.snapshot_hash(["feature.py"])
    branch = "agent/add-feature"
    repo.create_branch(branch)
    repo.stage_only(["feature.py"])
    head_sha = repo.commit("Add feature")

    config = RepoConfig(
        git=GitConfig(delete_local_branch=False),
        checks=ChecksConfig(required=["ci"]),
    )
    plan = FrozenPlan(
        plan_id="a" * 64,
        repo_root=str(git_repository),
        repo_full_name="example/project",
        remote_url=str(bare),
        base_branch="main",
        base_sha=base_sha,
        branch_name=branch,
        include_paths=["feature.py"],
        excluded_paths=[],
        snapshot_hash=snapshot,
        config=config,
        validation_results=[],
        releases=[],
        release_enabled=False,
        proposal=ModelProposal(
            branch_slug="add-feature",
            commit_message="Add feature",
            pr_title="Add feature",
            pr_body="## Summary\n\n- Add feature.",
            change_class=ChangeClass.FEATURE,
        ),
    )
    state = RunState(
        plan_id=plan.plan_id,
        approved_at=datetime.now(UTC),
        branch_name=branch,
    )
    github = SuccessfulGitHub(base_sha, head_sha)

    result = run_plan(plan, state, github_client=github)  # type: ignore[arg-type]

    assert result.phase is Phase.MERGED
    assert result.commit_sha == head_sha
    assert result.merge_sha == "merge-sha"
    assert github.deleted is True
    assert run_git(git_repository, "ls-remote", "--heads", "origin", branch)


def test_run_uses_frozen_release_latest_setting(git_repository: Path) -> None:
    plan = FrozenPlan(
        plan_id="b" * 64,
        repo_root=str(git_repository),
        repo_full_name="example/project",
        remote_url="git@github.com:example/project.git",
        base_branch="main",
        base_sha="base-sha",
        branch_name="agent/not-latest",
        include_paths=["README.md"],
        excluded_paths=[],
        snapshot_hash="snapshot",
        config=RepoConfig(
            git=GitConfig(delete_remote_branch=False, delete_local_branch=False),
            checks=ChecksConfig(required=["ci"]),
        ),
        validation_results=[],
        releases=[],
        release_enabled=True,
        release_make_latest=False,
        proposal=ModelProposal(
            branch_slug="not-latest",
            commit_message="Publish historical release",
            pr_title="Publish historical release",
            pr_body="## Summary\n\n- Publish a historical release.",
            change_class=ChangeClass.INTERNAL,
            suggested_version="v1.1.0",
            release_channel=ReleaseChannel.STABLE,
            version_rationale="Publish without replacing the latest release.",
            release_notes="## Changes\n\n- Publish a historical release.",
        ),
    )
    state = RunState(
        plan_id=plan.plan_id,
        phase=Phase.MERGED,
        approved_at=datetime.now(UTC),
        branch_name=plan.branch_name,
        merge_sha="merge-sha",
    )
    github = ReleaseGitHub()

    result = run_plan(plan, state, github_client=github)  # type: ignore[arg-type]

    assert result.phase is Phase.RELEASED
    assert github.create_kwargs is not None
    assert github.create_kwargs["make_latest"] is False
