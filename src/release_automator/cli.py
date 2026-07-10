from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Never

import typer
from rich.console import Console
from rich.markdown import Markdown

from release_automator.config import load_config
from release_automator.errors import AutomatorError
from release_automator.models import ReleaseChannel
from release_automator.state import StateStore
from release_automator.workflow import (
    approve_plan,
    create_plan,
    load_plan,
    render_plan,
    render_state,
    run_plan,
)

app = typer.Typer(
    no_args_is_help=True,
    help="Plan, approve, and execute deterministic GitHub publication workflows.",
)
console = Console()


def _read_optional(path: Path | None) -> str | None:
    return path.read_text(encoding="utf-8") if path else None


def _abort(exc: Exception) -> Never:
    console.print(f"[bold red]Error:[/bold red] {exc}", style="red")
    raise typer.Exit(code=1) from exc


@app.command("plan")
def plan_command(
    repo: Annotated[Path, typer.Option("--repo", help="Target Git repository.")] = Path("."),
    include: Annotated[
        list[Path] | None,
        typer.Option("--include", help="Changed file or directory to include; repeatable."),
    ] = None,
    config_path: Annotated[
        Path | None, typer.Option("--config", help="Repository TOML configuration.")
    ] = None,
    no_release: Annotated[
        bool, typer.Option("--no-release", help="Stop after merge without creating a release.")
    ] = False,
    branch: Annotated[str | None, typer.Option("--branch", help="Override branch slug.")] = None,
    commit_message: Annotated[
        str | None, typer.Option("--commit-message", help="Override commit message.")
    ] = None,
    pr_title: Annotated[
        str | None, typer.Option("--pr-title", help="Override pull request title.")
    ] = None,
    pr_body_file: Annotated[
        Path | None, typer.Option("--pr-body-file", help="Override PR body from a file.")
    ] = None,
    version: Annotated[
        str | None, typer.Option("--version", help="Override the proposed release tag.")
    ] = None,
    release_channel: Annotated[
        ReleaseChannel | None,
        typer.Option("--release-channel", help="Override stable/prerelease channel."),
    ] = None,
    release_notes_file: Annotated[
        Path | None,
        typer.Option("--release-notes-file", help="Override release notes from a file."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    try:
        config = load_config(config_path)
        overrides = {
            "branch_slug": branch,
            "commit_message": commit_message,
            "pr_title": pr_title,
            "pr_body": _read_optional(pr_body_file),
            "suggested_version": version,
            "release_channel": release_channel,
            "release_notes": _read_optional(release_notes_file),
        }
        plan = create_plan(
            repo_path=repo,
            include=include or [],
            config=config,
            no_release=no_release,
            overrides=overrides,
        )
    except (AutomatorError, OSError, ValueError) as exc:
        _abort(exc)
    if json_output:
        console.print_json(plan.model_dump_json())
    else:
        console.print(Markdown(render_plan(plan)))
        console.print(f"[bold]Plan ID:[/bold] {plan.plan_id}")


@app.command("execute")
def execute_command(
    plan_id: Annotated[str, typer.Argument(help="Full or unique-prefix frozen plan ID.")],
    repo: Annotated[Path, typer.Option("--repo", help="Target Git repository.")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    try:
        git_repo, _store, plan = load_plan(repo, plan_id)
        console.print(Markdown(render_plan(plan)))
        short_id = plan.plan_id[:12]
        confirmation = typer.prompt(f"Type {short_id} to approve this exact plan")
        if confirmation != short_id:
            raise AutomatorError("approval did not match the frozen plan ID")
        state = approve_plan(git_repo, plan)
        state = run_plan(plan, state)
    except (AutomatorError, OSError, ValueError) as exc:
        _abort(exc)
    if json_output:
        console.print_json(state.model_dump_json())
    else:
        console.print(render_state(state))


@app.command("resume")
def resume_command(
    plan_id: Annotated[str, typer.Argument(help="Full or unique-prefix approved plan ID.")],
    repo: Annotated[Path, typer.Option("--repo", help="Target Git repository.")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    try:
        _git_repo, store, plan = load_plan(repo, plan_id)
        state = store.load_run(plan.plan_id)
        state = run_plan(plan, state)
    except (AutomatorError, OSError, ValueError) as exc:
        _abort(exc)
    if json_output:
        console.print_json(state.model_dump_json())
    else:
        console.print(render_state(state))


@app.command("status")
def status_command(
    plan_id: Annotated[str, typer.Argument(help="Full or unique-prefix approved plan ID.")],
    repo: Annotated[Path, typer.Option("--repo", help="Target Git repository.")] = Path("."),
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    try:
        git_repo, _store, plan = load_plan(repo, plan_id)
        store = StateStore(git_repo)
        state = store.load_run(plan.plan_id)
    except (AutomatorError, OSError, ValueError) as exc:
        _abort(exc)
    if json_output:
        console.print(json.dumps(state.model_dump(mode="json"), indent=2, default=str))
    else:
        console.print(render_state(state))
