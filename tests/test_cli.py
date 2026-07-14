from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from release_automator.cli import _confirm_plan, app
from release_automator.errors import AutomatorError
from release_automator.models import ChangeClass, FrozenPlan, ModelProposal, RepoConfig

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def _plan() -> FrozenPlan:
    return FrozenPlan(
        plan_id="a" * 64,
        repo_root="/tmp/repository",
        repo_full_name="example/project",
        remote_url="git@github.com:example/project.git",
        base_branch="main",
        base_sha="b" * 40,
        branch_name="agent/change",
        include_paths=["feature.py"],
        excluded_paths=[],
        snapshot_hash="c" * 64,
        config=RepoConfig(),
        validation_results=[],
        releases=[],
        release_enabled=False,
        proposal=ModelProposal(
            branch_slug="change",
            commit_message="Apply change",
            pr_title="Apply change",
            pr_body="## Summary\n\n- Apply change.",
            change_class=ChangeClass.INTERNAL,
        ),
    )


def test_noninteractive_approval_requires_full_plan_id() -> None:
    plan = _plan()
    _confirm_plan(plan, plan.plan_id)

    with pytest.raises(AutomatorError, match="full frozen plan ID"):
        _confirm_plan(plan, plan.plan_id[:12])

    with pytest.raises(AutomatorError, match="full frozen plan ID"):
        _confirm_plan(plan, "b" * 64)


def test_plan_help_includes_no_latest() -> None:
    result = CliRunner().invoke(app, ["plan", "--help"])

    assert result.exit_code == 0
    assert "--no-latest" in ANSI_ESCAPE_RE.sub("", result.stdout)
