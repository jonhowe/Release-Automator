from __future__ import annotations

from pathlib import Path

import pytest
from conftest import run_git

from release_automator.errors import AutomatorError, ConfigurationError
from release_automator.git_ops import GitRepo


def test_explicit_include_expands_only_changed_files(git_repository: Path) -> None:
    (git_repository / "src").mkdir()
    (git_repository / "src" / "one.py").write_text("one = 1\n", encoding="utf-8")
    (git_repository / "other.py").write_text("other = 2\n", encoding="utf-8")
    repo = GitRepo(git_repository)

    assert repo.resolve_includes([Path("src")]) == ["src/one.py"]
    assert repo.excluded_paths(["src/one.py"]) == ["other.py"]


def test_staged_outsider_is_rejected(git_repository: Path) -> None:
    (git_repository / "selected.py").write_text("selected = True\n", encoding="utf-8")
    (git_repository / "outside.py").write_text("outside = True\n", encoding="utf-8")
    run_git(git_repository, "add", "outside.py")
    repo = GitRepo(git_repository)

    with pytest.raises(AutomatorError, match="outside.py"):
        repo.assert_no_staged_outside(["selected.py"])


def test_snapshot_changes_when_included_content_changes(git_repository: Path) -> None:
    path = git_repository / "README.md"
    path.write_text("changed once\n", encoding="utf-8")
    repo = GitRepo(git_repository)
    first = repo.snapshot_hash(["README.md"])
    path.write_text("changed twice\n", encoding="utf-8")
    assert repo.snapshot_hash(["README.md"]) != first


def test_path_outside_repo_is_rejected(git_repository: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("outside", encoding="utf-8")
    repo = GitRepo(git_repository)
    with pytest.raises(ConfigurationError):
        repo.resolve_includes([outside])
