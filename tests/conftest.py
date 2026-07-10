from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def run_git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


@pytest.fixture
def git_repository(tmp_path: Path) -> Path:
    repo = tmp_path / "repository"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.name", "Release Automator Tests")
    run_git(repo, "config", "user.email", "tests@example.com")
    run_git(repo, "remote", "add", "origin", "git@github.com:example/project.git")
    (repo / "README.md").write_text("initial\n", encoding="utf-8")
    run_git(repo, "add", "README.md")
    run_git(repo, "commit", "-m", "Initial commit")
    return repo
