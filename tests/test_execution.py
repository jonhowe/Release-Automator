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
