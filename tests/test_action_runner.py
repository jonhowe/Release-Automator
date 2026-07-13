from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).parents[1] / "scripts" / "action_runner.py"


def _run_action(repo: Path, **values: str) -> subprocess.CompletedProcess[str]:
    environment = os.environ.copy()
    environment.update(values)
    return subprocess.run(
        [sys.executable, str(RUNNER)],
        cwd=repo,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def test_action_runner_requires_openai_secret_for_planning(git_repository: Path) -> None:
    environment = {
        "RELEASE_AUTOMATOR_MODE": "plan",
        "RELEASE_AUTOMATOR_REPO_PATH": str(git_repository),
        "RELEASE_AUTOMATOR_INCLUDE_PATHS": "README.md",
        "OPENAI_API_KEY": "",
    }

    result = _run_action(git_repository, **environment)

    assert result.returncode == 1
    assert "OPENAI_API_KEY must be supplied through GitHub Secrets" in result.stderr


def test_action_runner_requires_exact_noninteractive_approval(git_repository: Path) -> None:
    environment = {
        "RELEASE_AUTOMATOR_MODE": "execute",
        "RELEASE_AUTOMATOR_REPO_PATH": str(git_repository),
        "RELEASE_AUTOMATOR_BUNDLE_PATH": "plan.zip",
        "RELEASE_AUTOMATOR_PLAN_ID": "a" * 64,
        "RELEASE_AUTOMATOR_APPROVED_PLAN_ID": "a" * 12,
        "GITHUB_TOKEN": "test-token",
    }

    result = _run_action(git_repository, **environment)

    assert result.returncode == 1
    assert "complete 64-character plan ID" in result.stderr
