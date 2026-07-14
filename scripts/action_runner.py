from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Never

PLAN_ID_PATTERN = re.compile(r"[0-9a-f]{64}")


def _input(name: str, default: str = "") -> str:
    return os.environ.get(f"RELEASE_AUTOMATOR_{name}", default).strip()


def _required(value: str, label: str) -> str:
    if not value:
        _fail(f"{label} is required")
    return value


def _fail(message: str) -> Never:
    raise SystemExit(f"release-automator action: {message}")


def _require_secret(name: str) -> None:
    if not os.environ.get(name, "").strip():
        _fail(f"{name} must be supplied through GitHub Secrets")


def _boolean(name: str, default: bool = False) -> bool:
    value = _input(name, str(default).lower()).lower()
    if value in {"1", "true", "yes"}:
        return True
    if value in {"0", "false", "no"}:
        return False
    _fail(f"{name.lower().replace('_', '-')} must be true or false")


def _resolve(repo: Path, value: str, fallback: Path) -> Path:
    if not value:
        return fallback.resolve()
    path = Path(value).expanduser()
    return (path if path.is_absolute() else repo / path).resolve()


def _write_output(name: str, value: str) -> None:
    if "\n" in value or "\r" in value:
        _fail(f"unsafe newline in {name} output")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as stream:
            stream.write(f"{name}={value}\n")


def _append_summary(markdown_path: Path, plan_id: str) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary or not markdown_path.is_file():
        return
    with Path(summary).open("a", encoding="utf-8") as stream:
        stream.write("## Release Automator plan\n\n")
        stream.write(f"Full plan ID: `{plan_id}`\n\n")
        stream.write(markdown_path.read_text(encoding="utf-8"))
        stream.write("\n")


def _git(repo: Path, args: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=check,
        capture_output=True,
        text=True,
    )


def _capture_state(repo: Path, plan_id: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    run_output = destination / "run.json"
    repository_bundle = destination / "repository.bundle"
    run_output.unlink(missing_ok=True)
    repository_bundle.unlink(missing_ok=True)

    raw_run_path = _git(
        repo,
        ["rev-parse", "--git-path", f"release-automator/runs/{plan_id}.json"],
    ).stdout.strip()
    run_path = Path(raw_run_path)
    if not run_path.is_absolute():
        run_path = (repo / run_path).resolve()
    if not run_path.is_file():
        return

    shutil.copy2(run_path, run_output)
    try:
        state = json.loads(run_output.read_text(encoding="utf-8"))
        branch = state["branch_name"]
    except (KeyError, json.JSONDecodeError, OSError, TypeError):
        print("::warning::Run state was saved, but its branch could not be preserved.")
        return
    if not isinstance(branch, str):
        return
    if _git(repo, ["check-ref-format", "--branch", branch], check=False).returncode != 0:
        print("::warning::Run state contains an invalid branch name; Git bundle was skipped.")
        return
    ref = f"refs/heads/{branch}"
    if _git(repo, ["show-ref", "--verify", "--quiet", ref], check=False).returncode != 0:
        return
    result = _git(repo, ["bundle", "create", str(repository_bundle), ref], check=False)
    if result.returncode != 0:
        repository_bundle.unlink(missing_ok=True)
        print("::warning::Run state was saved, but the local branch bundle could not be created.")


def _run(command: list[str], repo: Path) -> int:
    print("Running Release Automator with secrets supplied only through the process environment.")
    return subprocess.run(command, cwd=repo, check=False).returncode


def main() -> int:
    mode = _input("MODE", "plan")
    if mode not in {"plan", "execute", "resume"}:
        _fail("mode must be plan, execute, or resume")

    repo = Path(_input("REPO_PATH", ".")).expanduser().resolve()
    if (
        not repo.is_dir()
        or _git(repo, ["rev-parse", "--is-inside-work-tree"], check=False).returncode != 0
    ):
        _fail(f"repo-path is not a Git working tree: {repo}")

    runner_temp = Path(os.environ.get("RUNNER_TEMP", repo / ".git"))
    output_root = (runner_temp / "release-automator-action").resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    bundle_path = _resolve(repo, _input("BUNDLE_PATH"), output_root / "plan.zip")
    markdown_path = _resolve(repo, _input("MARKDOWN_PATH"), output_root / "plan.md")
    plan_id_path = output_root / "plan-id.txt"
    state_path = _resolve(repo, _input("STATE_PATH"), output_root / "state")

    _write_output("bundle-path", str(bundle_path))
    _write_output("markdown-path", str(markdown_path))
    _write_output("state-path", str(state_path))

    command = [sys.executable, "-m", "release_automator"]
    plan_id = _input("PLAN_ID")

    if mode == "plan":
        _require_secret("OPENAI_API_KEY")
        _require_secret("GITHUB_TOKEN")
        include_paths = [
            line.strip() for line in _input("INCLUDE_PATHS").splitlines() if line.strip()
        ]
        if not include_paths:
            _fail("include-paths must contain at least one path in plan mode")
        command.extend(
            [
                "plan",
                "--repo",
                str(repo),
                "--bundle-out",
                str(bundle_path),
                "--markdown-out",
                str(markdown_path),
                "--plan-id-out",
                str(plan_id_path),
            ]
        )
        for path in include_paths:
            command.extend(["--include", path])
        config_path = _input("CONFIG_PATH")
        if config_path:
            command.extend(["--config", config_path])
        if _boolean("NO_RELEASE"):
            command.append("--no-release")
        if _boolean("NO_LATEST"):
            command.append("--no-latest")
        for input_name, option_name in (
            ("BRANCH", "--branch"),
            ("COMMIT_MESSAGE", "--commit-message"),
            ("PR_TITLE", "--pr-title"),
            ("PR_BODY_FILE", "--pr-body-file"),
            ("VERSION", "--version"),
            ("RELEASE_CHANNEL", "--release-channel"),
            ("RELEASE_NOTES_FILE", "--release-notes-file"),
        ):
            value = _input(input_name)
            if value:
                command.extend([option_name, value])
    else:
        _require_secret("GITHUB_TOKEN")
        approved_plan_id = _required(_input("APPROVED_PLAN_ID"), "approved-plan-id")
        if PLAN_ID_PATTERN.fullmatch(approved_plan_id) is None:
            _fail("approved-plan-id must be the complete 64-character plan ID")
        plan_id = plan_id or approved_plan_id
        if plan_id != approved_plan_id:
            _fail("plan-id and approved-plan-id must match exactly")
        _required(_input("BUNDLE_PATH"), "bundle-path")
        command.extend(
            [
                mode,
                plan_id,
                "--repo",
                str(repo),
                "--bundle",
                str(bundle_path),
                "--approved-plan-id",
                approved_plan_id,
            ]
        )
        if mode == "execute":
            command.extend(["--markdown-out", str(markdown_path)])
        else:
            run_state = _required(_input("RUN_STATE"), "run-state")
            command.extend(["--run-state", str(_resolve(repo, run_state, output_root))])
            repository_bundle = _input("REPOSITORY_BUNDLE")
            if repository_bundle:
                repository_bundle_path = _resolve(repo, repository_bundle, output_root)
                if repository_bundle_path.is_file():
                    command.extend(["--repository-bundle", str(repository_bundle_path)])

    returncode = _run(command, repo)
    if returncode != 0:
        if plan_id and PLAN_ID_PATTERN.fullmatch(plan_id):
            try:
                _capture_state(repo, plan_id, state_path)
            except (OSError, subprocess.SubprocessError) as exc:
                print(f"::warning::Could not preserve resumable state: {type(exc).__name__}")
        return returncode

    if mode == "plan":
        plan_id = plan_id_path.read_text(encoding="utf-8").strip()
        if PLAN_ID_PATTERN.fullmatch(plan_id) is None:
            _fail("planner did not produce a valid full plan ID")
        _append_summary(markdown_path, plan_id)
    _write_output("plan-id", plan_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
