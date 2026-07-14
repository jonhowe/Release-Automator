# Usage Guide

Release Automator converts an explicitly selected working-tree diff into one reviewable plan, then
executes that exact plan after approval. Run it from the target repository's default branch.

## 1. Prepare credentials

For local use, install dependencies and make the service credentials available to the process:

```bash
uv sync --locked --all-groups
export OPENAI_API_KEY="..."
gh auth login
```

The GitHub identity must be able to push branches, open and merge pull requests, read checks, and
create releases when releases are enabled.

In GitHub Actions, do not use workflow inputs or repository variables for credentials. Store
`OPENAI_API_KEY` as a repository Actions secret and store `RELEASE_AUTOMATOR_GITHUB_TOKEN` as a
secret on a protected `release` environment. The latter should be a repository-scoped fine-grained
PAT or short-lived GitHub App token. See the [GitHub Actions guide](github-actions.md).

## 2. Define repository policy

Create a TOML configuration that names required checks and local validation commands. Commands are
argument arrays and are executed directly, without a shell:

```toml
[checks]
required = ["test"]

[[validation]]
name = "tests"
argv = ["uv", "run", "--locked", "pytest"]
```

Set `[checks].allow_no_checks = true` only when the repository intentionally has no CI checks.

## 3. Freeze a plan

Select every changed file or directory that belongs in the publication:

```bash
uv run --locked release-automator plan \
  --repo . \
  --config release-automator.toml \
  --include src \
  --include tests
```

Planning runs validations, inspects GitHub, screens the selected diff for secrets, requests
structured metadata from OpenAI, and saves a hashed plan beneath `.git/release-automator/`. It does
not stage files or write to GitHub. Add `--no-release` when the workflow should stop after merging.
Add `--no-latest` to publish a stable release without replacing the repository's current latest
release. For an initial or otherwise deliberate tag, pass `--version`; the rendered plan identifies
it as an explicit override. Prereleases are always created without being marked latest.

## 4. Approve and execute

Review the complete proposal, then execute its plan ID:

```bash
uv run --locked release-automator execute --repo . PLAN_ID
```

Before prompting, the command prints the exact ordered execution actions, staging scope, branch,
commit, pull request title and body, required-check policy and timeouts, merge method, cleanup, and
release behavior. Type the displayed short plan ID only after reviewing that manifest. The tool
then rechecks for drift before creating the branch and commit, pushing, opening the pull request,
waiting for required checks, merging, cleaning up branches, and optionally creating the GitHub
Release.

## 5. Inspect or resume

An interrupted approved run is resumable without repeating completed operations:

```bash
uv run --locked release-automator status --repo . PLAN_ID
uv run --locked release-automator resume --repo . PLAN_ID
```

Create a fresh plan if the base branch, selected files, proposed branch, or release tag changes.

## 6. Move a plan between runners

Create a portable bundle while planning, then provide the same full ID as explicit non-interactive
approval on the clean execution runner:

```bash
uv run --locked release-automator plan --repo . --config release-automator.toml --include docs \
  --bundle-out /tmp/plan.zip --plan-id-out /tmp/plan-id.txt
uv run --locked release-automator execute --repo . FULL_PLAN_ID \
  --bundle /tmp/plan.zip --approved-plan-id FULL_PLAN_ID
```

The bundle restores only the frozen included paths and verifies their modes and hashes. Treat it as
source code: it may contain current private file contents even though prior contents of deleted
files are omitted.
