# Repository Guidelines

## Project Structure & Module Organization

Release Automator is a Python 3.12 CLI with application code in `src/release_automator/`.
`cli.py` defines Typer commands, `workflow.py` coordinates planning and execution, and supporting
modules isolate side effects. Tests live in `tests/` as `test_<area>.py`.
Repository configuration examples and copy-ready consumer workflows belong in `examples/`, while
this repository's own CI and release workflows are in `.github/workflows/`. The composite action is
`action.yml`; its non-shelling adapter lives in `scripts/action_runner.py`, and operational
documentation lives in `docs/`.

## Build, Test, and Development Commands

- `uv sync --locked --all-groups` installs the exact runtime and development dependencies from
  `uv.lock`.
- `uv run release-automator --help` runs the local CLI entry point.
- `uv run --locked pytest --cov=release_automator` runs the full suite and records coverage.
- `uv run --locked ruff check .` checks imports, style, and common correctness issues.
- `uv run --locked pyright` performs standard-mode type checking across `src/` and `tests/`.

Run all three quality checks before a pull request. Update `uv.lock` with dependency changes.

## Coding Style & Naming Conventions

Use four-space indentation, explicit type annotations, and a 100-character line limit. Ruff
enforces `E`, `F`, `I`, `UP`, `B`, and `SIM`. Name modules and functions in
`snake_case`, classes in `PascalCase`, and constants in `UPPER_SNAKE_CASE`. Keep CLI presentation
separate from Git, GitHub, persistence, and validation logic. Use typed Pydantic boundary models.

## Testing Guidelines

Pytest is configured with strict markers. Name tests `test_<behavior>` and place shared fixtures in
`tests/conftest.py`. Use `tmp_path` repositories for Git behavior and `respx` for HTTP interactions;
tests must not mutate real repositories or call live GitHub/OpenAI services. Add regression tests
for safety checks, resumability, validation drift, and failure paths. No minimum coverage threshold
is configured, but new behavior should be covered.

## Commit & Pull Request Guidelines

Use a concise, imperative subject, such as `Handle renames in git path detection`; add a body for
non-obvious safety decisions. Pull requests should explain behavior and risk, link issues, note
configuration or lockfile changes, and include CLI output for user-facing behavior. Ruff, Pyright,
and pytest must pass.

## Security & Configuration

Never commit `.env` files, tokens, private keys, generated plan state, or credentials. Preserve the
explicit-include, frozen-plan, and approval boundaries when changing workflow code. Validation
commands in TOML are argument arrays, not shell strings; keep them shell-free. GitHub Actions
credentials belong in named repository or protected-environment secrets, never action inputs.

## Browser-Free Operations

Release Automator must remain fully operable without a web browser. Codex and other agents must use
repository files, Git, the `gh` CLI, and documented REST APIs; do not use browser-control tools,
`gh --web`, a bare `gh auth login`, or UI-only setup and approval instructions. Credentials and Git
push authentication are pre-provisioned inputs. If a required credential, permission, or explicit
plan approval is unavailable through the supported CLI/API path, stop and request it rather than
opening or automating a browser. Browser-free operation does not relax the requirement that a human
review and explicitly confirm the complete frozen plan ID before execution is dispatched.
