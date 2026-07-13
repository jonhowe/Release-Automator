from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from release_automator.errors import AutomatorError, PlanDriftError
from release_automator.git_ops import GitRepo, parse_github_repo, run_validations
from release_automator.github_api import GitHubClient
from release_automator.models import (
    FrozenPlan,
    ModelProposal,
    Phase,
    ReleaseChannel,
    RepoConfig,
    RunState,
)
from release_automator.openai_planner import apply_overrides, propose_metadata
from release_automator.security import (
    assert_no_secrets,
    assert_payload_size,
    assert_safe_paths,
    redact_secrets,
)
from release_automator.state import StateStore
from release_automator.versioning import validate_suggested_version


def normalize_slug(value: str) -> str:
    slug = value.encode("ascii", errors="ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:63].rstrip("-")
    if not slug:
        raise AutomatorError("OpenAI branch name became empty after normalization")
    return slug


def _unique_branch_name(
    repo: GitRepo,
    github: GitHubClient,
    prefix: str,
    slug: str,
) -> str:
    base = f"{prefix}{slug}"
    candidate = base
    suffix = 2
    while repo.local_branch_exists(candidate) or github.branch_exists(candidate):
        candidate = f"{base}-{suffix}"
        suffix += 1
        if suffix > 100:
            raise AutomatorError("could not find an available branch name")
    return candidate


def _validate_release_proposal(
    proposal: ModelProposal,
    releases: list[Any],
    config: RepoConfig,
    release_enabled: bool,
) -> None:
    if not release_enabled:
        release_fields = (
            proposal.suggested_version,
            proposal.release_channel,
            proposal.version_rationale,
            proposal.release_notes,
        )
        if any(value is not None for value in release_fields):
            raise AutomatorError("release fields must be null when --no-release is used")
        return
    if not all(
        (
            proposal.suggested_version,
            proposal.release_channel,
            proposal.version_rationale,
            proposal.release_notes,
        )
    ):
        raise AutomatorError("OpenAI omitted required release metadata")
    validate_suggested_version(
        proposal.suggested_version or "",
        proposal.release_channel or ReleaseChannel.STABLE,
        [item.tag_name for item in releases],
        config.release.tag_prefix,
    )


def create_plan(
    *,
    repo_path: Path,
    include: list[Path],
    config: RepoConfig,
    no_release: bool,
    portable: bool = False,
    overrides: dict[str, Any] | None = None,
    openai_client: Any | None = None,
    github_client: GitHubClient | None = None,
) -> FrozenPlan:
    repo = GitRepo(repo_path)
    repo.assert_normal_state()
    include_paths = repo.resolve_includes(include)
    repo.assert_no_staged_outside(include_paths)
    assert_safe_paths(include_paths)

    repo_full_name = parse_github_repo(repo.origin_url())
    public_remote_url = f"https://github.com/{repo_full_name}.git"
    owns_github = github_client is None
    github = github_client or GitHubClient(repo_full_name)
    try:
        repository = github.repository()
        base_branch = repository["default_branch"]
        if repo.current_branch() != base_branch:
            raise AutomatorError(
                f"planning must start on default branch {base_branch!r}; "
                f"current branch is {repo.current_branch()!r}"
            )
        base_sha = github.branch_sha(base_branch)
        if repo.head_sha() != base_sha:
            raise PlanDriftError(
                f"local {base_branch} does not match GitHub ({repo.head_sha()} != {base_sha})"
            )
        if not config.checks.required and not config.checks.allow_no_checks:
            raise AutomatorError(
                "configure checks.required or explicitly set checks.allow_no_checks = true"
            )

        validations = run_validations(repo, config.validation)
        post_validation_paths = repo.resolve_includes(include)
        if post_validation_paths != include_paths:
            raise PlanDriftError(
                "validation commands changed the set of files selected by --include"
            )
        repo.assert_no_staged_outside(include_paths)
        excluded_paths = repo.excluded_paths(include_paths)
        diff, diff_redactions = redact_secrets(repo.model_diff(include_paths))
        safe_validations = []
        validation_redactions: list[str] = []
        for item in validations:
            output, detected = redact_secrets(item.output)
            validation_redactions.extend(detected)
            safe_validations.append(item.model_copy(update={"output": output}))
        redacted_secret_types = sorted(set(diff_redactions + validation_redactions))
        validation_text = "\n".join(item.output for item in safe_validations)
        model_material = f"{diff}\n{validation_text}"
        assert_payload_size(model_material.encode("utf-8"), config.openai.max_diff_bytes)
        assert_no_secrets(model_material)
        releases = github.list_releases()
        release_enabled = config.release.enabled_by_default and not no_release
        proposal = propose_metadata(
            config=config,
            repo_full_name=repo_full_name,
            base_branch=base_branch,
            include_paths=include_paths,
            excluded_paths=excluded_paths,
            diff=diff,
            validations=safe_validations,
            releases=releases,
            release_enabled=release_enabled,
            redacted_secret_types=redacted_secret_types,
            client=openai_client,
        )
        proposal = apply_overrides(proposal, overrides or {})
        _validate_release_proposal(proposal, releases, config, release_enabled)
        if release_enabled and github.tag_exists(proposal.suggested_version or ""):
            raise AutomatorError(f"release tag already exists: {proposal.suggested_version}")

        slug = normalize_slug(proposal.branch_slug)
        branch_name = _unique_branch_name(repo, github, config.git.branch_prefix, slug)
        plan = FrozenPlan(
            repo_root=str(repo.root),
            repo_full_name=repo_full_name,
            remote_url=public_remote_url,
            base_branch=base_branch,
            base_sha=base_sha,
            branch_name=branch_name,
            include_paths=include_paths,
            excluded_paths=excluded_paths,
            snapshot_hash=repo.snapshot_hash(include_paths, include_mode=portable),
            redacted_secret_types=redacted_secret_types,
            portable=portable,
            config=config,
            validation_results=safe_validations,
            releases=releases,
            release_enabled=release_enabled,
            proposal=proposal,
        )
        return StateStore(repo).save_plan(plan)
    finally:
        if owns_github:
            github.close()


def render_plan(plan: FrozenPlan) -> str:
    validation = (
        "\n".join(
            f"- {item.name}: {'passed' if item.returncode == 0 else 'failed'}"
            for item in plan.validation_results
        )
        or "- No local validation commands configured."
    )
    included = "\n".join(f"- `{path}`" for path in plan.include_paths)
    excluded = "\n".join(f"- `{path}`" for path in plan.excluded_paths) or "- None"
    release = "Release disabled for this plan (`--no-release`)."
    if plan.release_enabled:
        release = (
            f"- Tag/title: `{plan.proposal.suggested_version}`\n"
            f"- Channel: `{plan.proposal.release_channel}`\n"
            f"- Rationale: {plan.proposal.version_rationale}\n"
            f"- Target: the PR squash-merge commit on `{plan.base_branch}`\n\n"
            f"Release notes:\n\n{plan.proposal.release_notes}"
        )
    side_effect = (
        f"\n\nRelease side effect: {plan.config.release.side_effect_notice}"
        if plan.release_enabled and plan.config.release.side_effect_notice
        else ""
    )
    security = "- No secret-like values detected in model input."
    if plan.redacted_secret_types:
        security = (
            "- Redacted from OpenAI input: "
            + ", ".join(f"`{label}`" for label in plan.redacted_secret_types)
            + ".\n- Redacted values are not stored in this plan."
        )
    staged_paths = ", ".join(f"`{path}`" for path in plan.include_paths)
    required_checks = ", ".join(f"`{name}`" for name in plan.config.checks.required)
    accepted_conclusions = ", ".join(
        f"`{conclusion}`" for conclusion in plan.config.checks.accepted_conclusions
    )
    if required_checks:
        checks_action = (
            f"Wait for required checks {required_checks}; poll every "
            f"{plan.config.checks.poll_seconds} seconds, allow "
            f"{plan.config.checks.discovery_timeout_seconds} seconds for discovery and "
            f"{plan.config.checks.completion_timeout_seconds} seconds for completion, and accept "
            f"only {accepted_conclusions}."
        )
    else:
        checks_action = "Skip required-check waiting because no checks are explicitly allowed."
    if plan.config.git.delete_remote_branch:
        remote_cleanup = f"Delete remote branch `{plan.branch_name}` after the merge."
    else:
        remote_cleanup = f"Leave remote branch `{plan.branch_name}` on GitHub after the merge."
    if plan.release_enabled:
        prerelease = plan.proposal.release_channel is ReleaseChannel.PRERELEASE
        release_action = (
            f"Create a non-draft GitHub Release and tag `{plan.proposal.suggested_version}` "
            f"targeting the returned merge commit; set prerelease to `{prerelease}` and use the "
            "release notes printed above."
        )
    else:
        release_action = "Do not create a GitHub Release or tag."
    if plan.config.git.delete_local_branch:
        local_cleanup = (
            f"Fetch `origin/{plan.base_branch}`, check out `{plan.base_branch}`, fast-forward it, "
            f"and delete local branch `{plan.branch_name}`; report a warning instead if safe "
            "cleanup cannot be completed."
        )
    else:
        local_cleanup = f"Leave local branch `{plan.branch_name}` checked out after completion."
    approval_instruction = "Execute it by typing the displayed short plan ID when prompted."
    if plan.portable:
        approval_instruction = (
            "On the execution runner, supply the complete 64-character plan ID through "
            "`--approved-plan-id`."
        )
    return f"""# Publication proposal `{plan.plan_id[:12]}`

## Scope

Included:
{included}

Excluded:
{excluded}

## Validation

{validation}

## Model-input security

{security}

## Git and pull request

- Branch: `{plan.branch_name}`
- Commit: `{plan.proposal.commit_message}`
- Base: `{plan.base_branch}`
- Portable bundle: `{plan.portable}`
- Merge method: `{plan.config.git.merge_method}`
- Delete remote branch: `{plan.config.git.delete_remote_branch}`
- Required checks: {", ".join(plan.config.checks.required) or "none (explicitly allowed)"}
- PR title: `{plan.proposal.pr_title}`

PR body:

{plan.proposal.pr_body}

## Release

{release}{side_effect}

## Execution actions after approval

The validations above ran during planning and are frozen into this plan. Execution will not rerun
them; it will perform these actions in order:

1. Create or resume `.git/release-automator/runs/{plan.plan_id}.json` and persist progress after
   each phase.
2. Authenticate to GitHub and revalidate the repository, `{plan.base_branch}` base SHA, included
   file hash, staging scope, branch availability, and release tag availability before Git or GitHub
   mutations.
3. Create and check out local branch `{plan.branch_name}` from `{plan.base_branch}`.
4. Stage only {staged_paths} and verify that the staged set matches exactly.
5. Create commit `{plan.proposal.commit_message}`.
6. Push `{plan.branch_name}` to `origin` and set its upstream.
7. Find or open a non-draft pull request from `{plan.branch_name}` to `{plan.base_branch}` titled
   `{plan.proposal.pr_title}` with the body printed above, then verify its head SHA.
8. {checks_action}
9. Wait until the pull request is mergeable, verify its head SHA again, and
   `{plan.config.git.merge_method}`-merge it using the frozen commit SHA.
10. {remote_cleanup}
11. {release_action}
12. {local_cleanup}

One approval authorizes this exact frozen plan.
{approval_instruction}
"""


def _assert_plan_still_matches(repo: GitRepo, plan: FrozenPlan, github: GitHubClient) -> None:
    if not plan.portable and str(repo.root) != plan.repo_root:
        raise PlanDriftError("the plan belongs to a different repository")
    if parse_github_repo(repo.origin_url()) != plan.repo_full_name:
        raise PlanDriftError("the plan belongs to a different GitHub repository")
    if repo.current_branch() != plan.base_branch:
        raise PlanDriftError(f"current branch must still be {plan.base_branch}")
    if repo.head_sha() != plan.base_sha:
        raise PlanDriftError("local base HEAD changed after planning")
    if github.branch_sha(plan.base_branch) != plan.base_sha:
        raise PlanDriftError("GitHub base branch changed after planning")
    if (
        repo.snapshot_hash(plan.include_paths, include_mode=plan.portable)
        != plan.snapshot_hash
    ):
        raise PlanDriftError("included files changed after planning")
    repo.assert_no_staged_outside(plan.include_paths)
    if repo.local_branch_exists(plan.branch_name) or github.branch_exists(plan.branch_name):
        raise PlanDriftError(f"planned branch is no longer available: {plan.branch_name}")
    if plan.release_enabled:
        tag = plan.proposal.suggested_version or ""
        if github.tag_exists(tag) or github.release_by_tag(tag):
            raise PlanDriftError(f"planned release tag is no longer available: {tag}")


def approve_plan(repo: GitRepo, plan: FrozenPlan) -> RunState:
    store = StateStore(repo)
    if store.has_run(plan.plan_id):
        return store.load_run(plan.plan_id)
    state = RunState(
        plan_id=plan.plan_id,
        approved_at=datetime.now(UTC),
        branch_name=plan.branch_name,
    )
    store.save_run(state)
    return state


def run_plan(
    plan: FrozenPlan,
    state: RunState,
    *,
    repo_path: Path | None = None,
    github_client: GitHubClient | None = None,
) -> RunState:
    repo = GitRepo(repo_path or Path(plan.repo_root))
    store = StateStore(repo)
    owns_github = github_client is None
    github = github_client or GitHubClient(plan.repo_full_name)
    state.error = None
    store.save_run(state)
    try:
        if state.phase is Phase.PLANNED:
            if repo.local_branch_exists(plan.branch_name):
                if repo.current_branch() != plan.branch_name:
                    raise PlanDriftError(
                        "planned local branch exists but is not currently checked out"
                    )
                if (
                    repo.snapshot_hash(
                        plan.include_paths,
                        base_sha=plan.base_sha,
                        include_mode=plan.portable,
                    )
                    != plan.snapshot_hash
                ):
                    raise PlanDriftError("included files changed during branch recovery")
                branch_sha = repo.branch_sha(plan.branch_name)
                if branch_sha == plan.base_sha:
                    repo.stage_only(plan.include_paths)
                    state.commit_sha = repo.commit(plan.proposal.commit_message)
                else:
                    if (
                        repo.commit_parent(branch_sha) != plan.base_sha
                        or repo.commit_message(branch_sha) != plan.proposal.commit_message
                        or repo.commit_paths(branch_sha) != plan.include_paths
                    ):
                        raise PlanDriftError(
                            "existing local branch does not contain the frozen commit"
                        )
                    state.commit_sha = branch_sha
            else:
                _assert_plan_still_matches(repo, plan, github)
                repo.create_branch(plan.branch_name)
                repo.stage_only(plan.include_paths)
                state.commit_sha = repo.commit(plan.proposal.commit_message)
            state.phase = Phase.COMMITTED
            store.save_run(state)

        if state.phase is Phase.COMMITTED:
            if not state.commit_sha:
                raise AutomatorError("committed state is missing commit SHA")
            if (
                repo.local_branch_exists(plan.branch_name)
                and repo.branch_sha(plan.branch_name) != state.commit_sha
            ):
                raise PlanDriftError("local branch does not match the committed run state")
            remote_sha = (
                github.branch_sha(plan.branch_name)
                if github.branch_exists(plan.branch_name)
                else None
            )
            if remote_sha and remote_sha != state.commit_sha:
                raise PlanDriftError("remote branch exists at a different commit")
            if not remote_sha:
                if not repo.local_branch_exists(plan.branch_name):
                    raise AutomatorError("local branch is missing; cannot resume push")
                repo.push(plan.branch_name)
            state.phase = Phase.PUSHED
            store.save_run(state)

        if state.phase is Phase.PUSHED:
            pull = github.find_pull_request(plan.branch_name, plan.base_branch)
            if pull is None:
                pull = github.create_pull_request(
                    title=plan.proposal.pr_title,
                    body=plan.proposal.pr_body,
                    head=plan.branch_name,
                    base=plan.base_branch,
                )
            if pull["head"]["sha"] != state.commit_sha:
                raise PlanDriftError("pull request head SHA does not match the frozen commit")
            state.pr_number = int(pull["number"])
            state.pr_url = pull["html_url"]
            if pull.get("merged"):
                state.merge_sha = pull.get("merge_commit_sha")
                state.phase = Phase.MERGED
            else:
                state.phase = Phase.PR_OPEN
            store.save_run(state)

        if state.phase is Phase.PR_OPEN:
            if not state.commit_sha or not state.pr_number:
                raise AutomatorError("PR state is missing commit SHA or PR number")
            pull = github.get_pull_request(state.pr_number)
            if pull.get("head", {}).get("sha") != state.commit_sha:
                raise PlanDriftError("pull request head changed after approval")
            if pull.get("merged"):
                state.merge_sha = pull.get("merge_commit_sha")
                state.phase = Phase.MERGED
            else:
                github.wait_for_checks(state.commit_sha, plan.config.checks)
                state.phase = Phase.CHECKS_PASSED
            store.save_run(state)

        if state.phase is Phase.CHECKS_PASSED:
            if not state.commit_sha or not state.pr_number:
                raise AutomatorError("merge state is missing commit SHA or PR number")
            pull = github.wait_until_mergeable(state.pr_number)
            if pull.get("head", {}).get("sha") != state.commit_sha:
                raise PlanDriftError("pull request head changed before merge")
            if pull.get("merged"):
                state.merge_sha = pull.get("merge_commit_sha")
            else:
                state.merge_sha = github.merge_pull_request(
                    state.pr_number,
                    head_sha=state.commit_sha,
                    method=plan.config.git.merge_method,
                    title=plan.proposal.pr_title,
                )
            state.phase = Phase.MERGED
            store.save_run(state)

        if (
            state.phase is Phase.MERGED
            and plan.config.git.delete_remote_branch
            and github.branch_exists(plan.branch_name)
        ):
            github.delete_branch(plan.branch_name)

        if state.phase is Phase.MERGED and plan.release_enabled:
            if not state.merge_sha:
                raise AutomatorError("merged state is missing merge SHA")
            tag = plan.proposal.suggested_version or ""
            existing = github.release_by_tag(tag)
            if existing:
                state.release_url = existing.get("html_url")
            else:
                if github.tag_exists(tag):
                    raise PlanDriftError(f"tag {tag} exists without the approved release")
                release = github.create_release(
                    tag=tag,
                    target_sha=state.merge_sha,
                    title=tag,
                    notes=plan.proposal.release_notes or "",
                    prerelease=plan.proposal.release_channel is ReleaseChannel.PRERELEASE,
                )
                state.release_url = release["html_url"]
            state.phase = Phase.RELEASED
            store.save_run(state)

        terminal = state.phase is Phase.RELEASED or (
            state.phase is Phase.MERGED and not plan.release_enabled
        )
        if terminal and state.commit_sha and plan.config.git.delete_local_branch:
            state.warning = repo.sync_base_and_delete_branch(
                plan.base_branch,
                plan.branch_name,
                state.commit_sha,
            )
            store.save_run(state)
        return state
    except Exception as exc:
        state.error = str(exc)
        store.save_run(state)
        raise
    finally:
        if owns_github:
            github.close()


def load_plan(repo_path: Path, plan_id: str) -> tuple[GitRepo, StateStore, FrozenPlan]:
    repo = GitRepo(repo_path)
    store = StateStore(repo)
    return repo, store, store.load_plan(plan_id)


def render_state(state: RunState) -> str:
    fields = [
        f"Plan: {state.plan_id}",
        f"Phase: {state.phase}",
        f"Branch: {state.branch_name}",
    ]
    for label, value in (
        ("Commit", state.commit_sha),
        ("Pull request", state.pr_url),
        ("Merge commit", state.merge_sha),
        ("Release", state.release_url),
        ("Warning", state.warning),
        ("Error", state.error),
    ):
        if value:
            fields.append(f"{label}: {value}")
    return "\n".join(fields)
