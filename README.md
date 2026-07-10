# Release Automator

Release Automator turns a selected working-tree diff into one frozen publication plan, asks for
one explicit approval, and then performs the branch, commit, push, pull request, check wait,
squash merge, branch cleanup, and optional GitHub Release workflow.

OpenAI proposes the human-facing metadata. Python validates, hashes, and freezes that proposal
before any Git or GitHub write. Once approved, the remaining workflow is deterministic and
resumable.

## Requirements

- Python 3.12 or newer
- Git and an `origin` remote hosted on GitHub
- `OPENAI_API_KEY`
- `GITHUB_TOKEN`, or an authenticated GitHub CLI (`gh auth login`)
- Existing Git credentials capable of pushing `origin`

For a fine-grained GitHub token, grant Contents read/write, Pull requests read/write, and Checks
read access to the target repository. Tokens are read into process memory and are never written
to plan files or logs.

## Install

```bash
uv sync --locked --all-groups
uv run release-automator --help
```

The committed `uv.lock` is the reproducible dependency source. Use `uv run --locked` in CI and
normal operation.

## Configure a repository

Copy `examples/media-atlas.toml` and adjust its validation commands, required GitHub check names,
release policy, and side-effect notice. Commands are argument arrays and are never evaluated by a
shell.

Required checks should be configured explicitly. Repositories with no checks must deliberately set:

```toml
[checks]
allow_no_checks = true
```

## Plan

Run from the target repository or pass `--repo`. Every changed file must be selected explicitly;
directories expand only to changed files beneath that directory.

```bash
uv run release-automator plan \
  --repo /path/to/repository \
  --config examples/media-atlas.toml \
  --include backend/app \
  --include frontend/src
```

The command performs read-only repository/GitHub inspection, runs configured validations, sends a
sanitized diff to OpenAI with API storage disabled, and saves the frozen plan under the target
repository's Git directory. It does not create a branch, stage files, or write to GitHub.

Use `--no-release` to stop after merge. Metadata may be overridden while creating a new plan:

```bash
uv run release-automator plan \
  --repo /path/to/repository \
  --config config.toml \
  --include src \
  --version v2.1.0-beta3 \
  --release-channel prerelease \
  --pr-body-file /tmp/pr-body.md
```

## Approve and execute

```bash
uv run release-automator execute --repo /path/to/repository PLAN_ID
```

The complete proposal is printed again. Execution starts only after the short plan ID is typed
exactly. Before the first mutation, the tool verifies the base SHA, included-file hash, branch,
tag, and credentials have not drifted.

If the process stops after approval, resume without repeating completed GitHub operations:

```bash
uv run release-automator status --repo /path/to/repository PLAN_ID
uv run release-automator resume --repo /path/to/repository PLAN_ID
```

## Safety boundaries

- Only explicit `--include` files are staged.
- Pre-existing staged files outside the selected scope are rejected.
- `.env`, private-key, credential, and likely token content is blocked before an OpenAI request.
- Model input is capped at 200 KB by default; binary contents are never sent.
- Invalid model output, failed checks, timeouts, merge conflicts, head drift, and tag collisions
  stop the workflow.
- GitHub merge requests include the frozen PR head SHA.
- A release targets the returned merge SHA, never an inferred local commit.
- The model is pinned to `gpt-5.4-mini-2026-03-17` by default. Text may still vary between fresh
  plans; determinism begins when a plan is frozen and hashed.

OpenAI's Responses API is used with Pydantic structured output and `store=False`:

- <https://developers.openai.com/api/docs/guides/structured-outputs>
- <https://developers.openai.com/api/docs/guides/migrate-to-responses>
- <https://developers.openai.com/api/docs/models/gpt-5.4-mini>

GitHub operations use its documented REST endpoints:

- <https://docs.github.com/en/rest/pulls/pulls>
- <https://docs.github.com/en/rest/checks/runs>
- <https://docs.github.com/en/rest/releases/releases>

## Moving this project

The directory is self-contained. After moving it into a new repository, keep `pyproject.toml`,
`uv.lock`, `.python-version`, the source/tests, and the nested `.github/workflows/tests.yml` together.
The parent Media-Atlas `AGENTS.md` is intentionally not modified by this project.
