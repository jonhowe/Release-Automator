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
- `OPENAI_API_KEY` in the process environment or GitHub Actions secrets
- `GITHUB_TOKEN`, or an authenticated GitHub CLI (`gh auth login`)
- Existing Git credentials capable of pushing `origin`

For local use, grant a fine-grained GitHub token Contents and Pull requests read/write access to the
target repository. GitHub Actions can use a separate built-in job token for check/status reads.
Tokens are read into process memory and are never written to plan files or logs.

## Install

```bash
uv sync --locked --all-groups
uv run release-automator --help
```

The committed `uv.lock` is the reproducible dependency source. Use `uv run --locked` in CI and
normal operation.

For a task-oriented walkthrough covering configuration, planning, approval, and recovery, see the
[usage guide](docs/usage.md).

## GitHub Action

The repository includes a composite `action.yml`, self-release workflows, and copy-ready consumer
workflows under `examples/consumer-workflows/`.
Planning produces a portable artifact, prints the entire proposed operation to the job log and job
summary, and outputs a full frozen plan ID. Execution requires that exact 64-character ID and is
gated by a protected `release` environment before it can access a write-capable GitHub token.

Create both values in the repository being released: store `OPENAI_API_KEY` as a repository Actions
secret and the scoped `RELEASE_AUTOMATOR_GITHUB_TOKEN` as an environment secret on `release`. The
write PAT needs Contents and Pull requests read/write; the workflow's built-in token handles
check/status reads. See the [GitHub Actions guide](docs/github-actions.md) for PAT creation, consumer
installation, permissions, approval, resume, GitHub App, and fork-safety guidance.

### Use it in another repository

1. Copy the three workflow templates into the target repository's `.github/workflows/` directory
   and copy the example `release-automator.toml` to its root.
2. Replace `checks.required = ["ci"]` with the target repository's exact required check-run names.
3. In the target repository, add `OPENAI_API_KEY` as a repository secret and create a `release`
   environment containing `RELEASE_AUTOMATOR_GITHUB_TOKEN`.
4. Commit the setup to the default branch, then run **Plan a release** from its Actions tab.

The [consumer quick start](docs/github-actions.md#consumer-quick-start) provides copy commands,
example workflow inputs, approval steps, and troubleshooting. Nothing needs to be configured in
the Release-Automator repository.

## Configure a repository

Copy `examples/python-project.toml` and adjust its validation commands, required GitHub check names,
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
  --config examples/python-project.toml \
  --include src \
  --include tests
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

When `--version` is supplied, the rendered rationale explicitly records that the release version
was overridden during planning rather than proposed from release history.

## Approve and execute

```bash
uv run release-automator execute --repo /path/to/repository PLAN_ID
```

The complete proposal is printed again, including the exact ordered execution actions, staging
scope, branch and commit, pull request content, check policy and timeouts, merge method, cleanup,
and release behavior. Execution starts only after the short plan ID is typed exactly. Before the
first Git or GitHub mutation, the tool verifies the base SHA, included-file hash, branch, tag, and
credentials have not drifted.

Automation uses a stricter non-interactive approval: `--approved-plan-id` must match the complete
64-character plan ID. `--bundle` securely rehydrates a portable plan on a separate runner.

If the process stops after approval, resume without repeating completed GitHub operations:

```bash
uv run release-automator status --repo /path/to/repository PLAN_ID
uv run release-automator resume --repo /path/to/repository PLAN_ID
```

## Safety boundaries

- Only explicit `--include` files are staged.
- Pre-existing staged files outside the selected scope are rejected.
- Sensitive file paths such as `.env`, private keys, and credential files are blocked before an
  OpenAI request.
- Secret-like values in otherwise safe text files are replaced with typed redaction markers before
  OpenAI receives the diff or validation output; the frozen plan records only the detected types.
- Model input is capped at 200 KB by default; binary contents are never sent.
- Invalid model output, failed checks, timeouts, merge conflicts, head drift, and tag collisions
  stop the workflow.
- GitHub merge requests include the frozen PR head SHA.
- A release targets the returned merge SHA, never an inferred local commit.
- Portable bundles validate repository identity, base SHA, paths, file modes, and content hashes;
  deleted-file contents are not stored in the bundle.
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

## Security

Report vulnerabilities through GitHub's private vulnerability reporting. See
[`SECURITY.md`](SECURITY.md) for the disclosure policy and credential-handling guidance.

## License

Release Automator is licensed under the [Apache License 2.0](LICENSE).

## Moving this project

The directory is self-contained. After moving it into a new repository, keep `pyproject.toml`,
`uv.lock`, `.python-version`, the source/tests, and the nested `.github/workflows/tests.yml` together.
