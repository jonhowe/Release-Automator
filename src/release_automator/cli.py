from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Never

import typer
from rich.console import Console
from rich.markdown import Markdown

from release_automator.bundle import export_plan_bundle, import_plan_bundle
from release_automator.config import load_config
from release_automator.errors import AutomatorError
from release_automator.git_ops import GitRepo
from release_automator.models import FrozenPlan, Phase, ReleaseChannel
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


def _write_optional(path: Path | None, value: str) -> None:
    if path is None:
        return
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value, encoding="utf-8")


def _confirm_plan(plan: FrozenPlan, approved_plan_id: str | None) -> None:
    if approved_plan_id is not None:
        if approved_plan_id != plan.plan_id:
            raise AutomatorError("non-interactive approval must match the full frozen plan ID")
        return
    short_id = plan.plan_id[:12]
    confirmation = typer.prompt(f"Type {short_id} to approve this exact plan")
    if confirmation != short_id:
        raise AutomatorError("approval did not match the frozen plan ID")


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
    bundle_out: Annotated[
        Path | None,
        typer.Option("--bundle-out", help="Export a portable plan bundle for another runner."),
    ] = None,
    markdown_out: Annotated[
        Path | None,
        typer.Option("--markdown-out", help="Write the complete plan as Markdown."),
    ] = None,
    plan_id_out: Annotated[
        Path | None,
        typer.Option("--plan-id-out", help="Write the full frozen plan ID to a file."),
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
            portable=bundle_out is not None,
            overrides=overrides,
        )
        if bundle_out is not None:
            export_plan_bundle(repo, plan, bundle_out)
    except (AutomatorError, OSError, ValueError) as exc:
        _abort(exc)
    rendered = render_plan(plan)
    _write_optional(markdown_out, rendered)
    _write_optional(plan_id_out, f"{plan.plan_id}\n")
    if json_output:
        console.print_json(plan.model_dump_json())
    else:
        console.print(Markdown(rendered))
        console.print(f"[bold]Plan ID:[/bold] {plan.plan_id}")


@app.command("execute")
def execute_command(
    plan_id: Annotated[str, typer.Argument(help="Full or unique-prefix frozen plan ID.")],
    repo: Annotated[Path, typer.Option("--repo", help="Target Git repository.")] = Path("."),
    bundle: Annotated[
        Path | None,
        typer.Option("--bundle", help="Import a portable plan bundle before execution."),
    ] = None,
    approved_plan_id: Annotated[
        str | None,
        typer.Option(
            "--approved-plan-id",
            help="Non-interactive approval; must equal the full frozen plan ID.",
        ),
    ] = None,
    markdown_out: Annotated[
        Path | None,
        typer.Option("--markdown-out", help="Write the complete plan as Markdown."),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    try:
        if bundle is not None:
            plan = import_plan_bundle(repo, bundle, expected_plan_id=plan_id)
            git_repo = GitRepo(repo)
        else:
            git_repo, _store, plan = load_plan(repo, plan_id)
        rendered = render_plan(plan)
        _write_optional(markdown_out, rendered)
        console.print(Markdown(rendered))
        _confirm_plan(plan, approved_plan_id)
        state = approve_plan(git_repo, plan)
        state = run_plan(plan, state, repo_path=git_repo.root)
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
    bundle: Annotated[
        Path | None,
        typer.Option("--bundle", help="Import a portable plan bundle before resuming."),
    ] = None,
    run_state: Annotated[
        Path | None,
        typer.Option("--run-state", help="Import persisted run state from another runner."),
    ] = None,
    repository_bundle: Annotated[
        Path | None,
        typer.Option("--repository-bundle", help="Import preserved local Git objects."),
    ] = None,
    approved_plan_id: Annotated[
        str | None,
        typer.Option(
            "--approved-plan-id",
            help="Non-interactive approval; must equal the full frozen plan ID.",
        ),
    ] = None,
    json_output: Annotated[bool, typer.Option("--json", help="Print JSON.")] = False,
) -> None:
    try:
        if bundle is not None:
            plan = import_plan_bundle(repo, bundle, expected_plan_id=plan_id)
            git_repo = GitRepo(repo)
            store = StateStore(git_repo)
        else:
            git_repo, store, plan = load_plan(repo, plan_id)
        if approved_plan_id is not None:
            _confirm_plan(plan, approved_plan_id)
        state = (
            store.import_run(run_state, plan.plan_id)
            if run_state is not None
            else store.load_run(plan.plan_id)
        )
        if repository_bundle is not None and state.phase is not Phase.PLANNED:
            source = repository_bundle.expanduser().resolve()
            target = f"refs/heads/{plan.branch_name}:refs/heads/{plan.branch_name}"
            git_repo.run(["fetch", str(source), target])
        state = run_plan(plan, state, repo_path=git_repo.root)
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
