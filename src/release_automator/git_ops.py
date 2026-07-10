from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path, PurePosixPath

from release_automator.errors import AutomatorError, ConfigurationError
from release_automator.models import ValidationCommand, ValidationResult


class GitRepo:
    def __init__(self, path: Path) -> None:
        candidate = path.expanduser().resolve()
        result = self._run_at(candidate, ["rev-parse", "--show-toplevel"])
        self.root = Path(result.stdout.strip()).resolve()

    @staticmethod
    def _run_at(
        cwd: Path,
        args: list[str],
        *,
        check: bool = True,
        text: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=cwd,
                check=check,
                capture_output=True,
                text=text,
            )
        except FileNotFoundError as exc:
            raise AutomatorError("git is not installed or is not on PATH") from exc
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "git command failed").strip()
            raise AutomatorError(f"git {' '.join(args)}: {detail}") from exc

    def run(self, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        return self._run_at(self.root, args, check=check)

    def git_path(self, name: str) -> Path:
        raw = self.run(["rev-parse", "--git-path", name]).stdout.strip()
        path = Path(raw)
        return path if path.is_absolute() else (self.root / path).resolve()

    def current_branch(self) -> str:
        return self.run(["branch", "--show-current"]).stdout.strip()

    def head_sha(self) -> str:
        return self.run(["rev-parse", "HEAD"]).stdout.strip()

    def origin_url(self) -> str:
        return self.run(["remote", "get-url", "origin"]).stdout.strip()

    def assert_normal_state(self) -> None:
        markers = [
            ("merge", self.git_path("MERGE_HEAD")),
            ("rebase", self.git_path("rebase-merge")),
            ("rebase", self.git_path("rebase-apply")),
            ("cherry-pick", self.git_path("CHERRY_PICK_HEAD")),
            ("revert", self.git_path("REVERT_HEAD")),
        ]
        active = sorted({name for name, marker in markers if marker.exists()})
        if active:
            raise AutomatorError(f"repository has an in-progress operation: {', '.join(active)}")

    @staticmethod
    def _split_null(output: str) -> list[str]:
        return [item for item in output.split("\0") if item]

    def changed_paths(self) -> list[str]:
        tracked = self._split_null(
            self.run(["diff", "--no-renames", "--name-only", "-z", "HEAD"]).stdout
        )
        untracked = self._split_null(
            self.run(["ls-files", "--others", "--exclude-standard", "-z"]).stdout
        )
        return sorted(set(tracked + untracked))

    def staged_paths(self) -> list[str]:
        output = self.run(["diff", "--cached", "--no-renames", "--name-only", "-z"]).stdout
        return sorted(self._split_null(output))

    def resolve_includes(self, requested: list[Path]) -> list[str]:
        if not requested:
            raise ConfigurationError("at least one --include path is required")
        changed = self.changed_paths()
        selected: set[str] = set()
        for raw in requested:
            absolute = raw if raw.is_absolute() else self.root / raw
            absolute = absolute.resolve(strict=False)
            try:
                relative = absolute.relative_to(self.root).as_posix()
            except ValueError as exc:
                raise ConfigurationError(f"included path is outside the repository: {raw}") from exc
            if relative == ".git" or relative.startswith(".git/"):
                raise ConfigurationError("paths inside .git cannot be included")
            matches = [
                path
                for path in changed
                if path == relative or path.startswith(f"{relative.rstrip('/')}/")
            ]
            if not matches:
                raise ConfigurationError(f"included path has no changes: {raw}")
            selected.update(matches)
        return sorted(selected)

    def assert_no_staged_outside(self, include_paths: list[str]) -> None:
        unexpected = sorted(set(self.staged_paths()) - set(include_paths))
        if unexpected:
            raise AutomatorError("staged changes outside --include scope: " + ", ".join(unexpected))

    def excluded_paths(self, include_paths: list[str]) -> list[str]:
        return sorted(set(self.changed_paths()) - set(include_paths))

    def snapshot_hash(self, include_paths: list[str], *, base_sha: str | None = None) -> str:
        digest = hashlib.sha256()
        digest.update((base_sha or self.head_sha()).encode())
        for path_text in sorted(include_paths):
            digest.update(b"\0PATH\0")
            digest.update(path_text.encode())
            path = self.root / path_text
            if path.is_symlink():
                digest.update(b"\0SYMLINK\0")
                digest.update(os.readlink(path).encode())
            elif path.is_file():
                digest.update(b"\0FILE\0")
                digest.update(path.read_bytes())
            elif not path.exists():
                digest.update(b"\0DELETED\0")
            else:
                raise AutomatorError(f"unsupported included path: {path_text}")
        return digest.hexdigest()

    def model_diff(self, include_paths: list[str]) -> str:
        tracked = set(self._split_null(self.run(["ls-files", "-z", "--", *include_paths]).stdout))
        chunks: list[str] = []
        if tracked:
            chunks.append(
                self.run(
                    [
                        "diff",
                        "--no-ext-diff",
                        "--unified=3",
                        "HEAD",
                        "--",
                        *sorted(tracked),
                    ]
                ).stdout
            )
        for path_text in include_paths:
            if path_text in tracked:
                continue
            path = self.root / path_text
            if not path.is_file():
                continue
            content = path.read_bytes()
            if b"\0" in content[:8192]:
                chunks.append(f"\n--- untracked binary file: {path_text} ({len(content)} bytes)\n")
                continue
            text = content.decode("utf-8", errors="replace")
            prefixed = "\n".join(f"+{line}" for line in text.splitlines())
            chunks.append(
                f"\ndiff --git a/{path_text} b/{path_text}\n"
                f"new file mode 100644\n--- /dev/null\n+++ b/{path_text}\n{prefixed}\n"
            )
        return "".join(chunks)

    def local_branch_exists(self, name: str) -> bool:
        result = self.run(["show-ref", "--verify", "--quiet", f"refs/heads/{name}"], check=False)
        return result.returncode == 0

    def branch_sha(self, name: str) -> str:
        return self.run(["rev-parse", name]).stdout.strip()

    def commit_parent(self, sha: str) -> str:
        return self.run(["rev-parse", f"{sha}^"]).stdout.strip()

    def commit_message(self, sha: str) -> str:
        return self.run(["show", "-s", "--format=%s", sha]).stdout.strip()

    def commit_paths(self, sha: str) -> list[str]:
        return sorted(
            self._split_null(
                self.run(
                    [
                        "diff-tree",
                        "--no-renames",
                        "--no-commit-id",
                        "--name-only",
                        "-r",
                        "-z",
                        sha,
                    ]
                ).stdout
            )
        )

    def create_branch(self, name: str) -> None:
        self.run(["checkout", "-b", name])

    def stage_only(self, paths: list[str]) -> None:
        self.run(["add", "--", *paths])
        staged = self.staged_paths()
        if staged != sorted(paths):
            raise AutomatorError(
                f"staged files do not match frozen plan; expected {sorted(paths)}, got {staged}"
            )

    def commit(self, message: str) -> str:
        self.run(["commit", "-m", message])
        return self.head_sha()

    def push(self, branch: str) -> None:
        self.run(["push", "-u", "origin", branch])

    def remote_branch_sha(self, branch: str) -> str | None:
        result = self.run(["ls-remote", "--heads", "origin", branch])
        output = result.stdout.strip()
        return output.split()[0] if output else None

    def sync_base_and_delete_branch(
        self,
        base_branch: str,
        branch_name: str,
        expected_branch_sha: str,
    ) -> str | None:
        try:
            actual = self.run(["rev-parse", branch_name]).stdout.strip()
            if actual != expected_branch_sha:
                return "local branch tip changed; local cleanup was skipped"
            self.run(["fetch", "origin", base_branch])
            self.run(["checkout", base_branch])
            self.run(["merge", "--ff-only", f"origin/{base_branch}"])
            self.run(["branch", "-D", branch_name])
        except AutomatorError as exc:
            return f"local cleanup was skipped: {exc}"
        return None


def parse_github_repo(remote_url: str) -> str:
    value = remote_url.strip()
    if value.startswith("git@github.com:"):
        value = value.removeprefix("git@github.com:")
    elif "github.com/" in value:
        value = value.split("github.com/", 1)[1]
    else:
        raise ConfigurationError("origin must point to github.com")
    value = value.removesuffix(".git").strip("/")
    if value.count("/") != 1:
        raise ConfigurationError(f"could not derive owner/repository from origin: {remote_url}")
    return value


def run_validations(repo: GitRepo, commands: list[ValidationCommand]) -> list[ValidationResult]:
    results: list[ValidationResult] = []
    for command in commands:
        cwd = (repo.root / PurePosixPath(command.cwd)).resolve()
        try:
            cwd.relative_to(repo.root)
        except ValueError as exc:
            raise ConfigurationError(f"validation cwd leaves repository: {command.cwd}") from exc
        completed = subprocess.run(
            command.argv,
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )
        output = f"{completed.stdout}{completed.stderr}"[-20_000:]
        result = ValidationResult(
            name=command.name,
            argv=command.argv,
            cwd=command.cwd,
            returncode=completed.returncode,
            output=output,
        )
        results.append(result)
        if completed.returncode != 0:
            raise AutomatorError(f"validation failed: {command.name}\n{output.strip()}")
    return results
