# GitHub Actions: Fully Headless Runbook

Release Automator ships a composite action and copy-ready plan, execute, and resume workflows.
Every supported operation is available through Git, the `gh` CLI, or GitHub's REST API. No setup,
dispatch, review, approval, or recovery step requires a web browser.

The supported workflow begins after GitHub, OpenAI, and Git push credentials have been provisioned
by a password manager, CI secret store, or authorized operator. Release Automator never mints a
credential or starts an interactive authentication flow.

## Credential and permission contract

Load secrets into the shell without putting their values in command arguments or shell history.
The commands below expect these variables to exist:

```bash
: "${GH_REPO:?set GH_REPO to owner/repository}"
: "${GH_TOKEN:?inject the operator GitHub token}"
: "${OPENAI_API_KEY:?inject the OpenAI API key}"
: "${RELEASE_AUTOMATOR_GITHUB_TOKEN:?inject the release GitHub token}"
```

`GH_TOKEN` is the control-plane credential used by `gh`. Keep it separate from the token stored for
release execution.

| Credential | Required access | Purpose |
| --- | --- | --- |
| `GH_TOKEN` | Actions read/write and Contents read; Secrets, Environments, and Administration write during initial setup | Creates the environment and secrets, dispatches workflows, and reads runs and artifacts. |
| `OPENAI_API_KEY` | OpenAI API access | Plans publication metadata only. |
| `RELEASE_AUTOMATOR_GITHUB_TOKEN` | Contents and Pull requests read/write; Workflows read/write only when a planned change includes workflow files | Pushes, opens and merges the PR, deletes the branch, and creates the release. |
| `REVIEWER_GH_TOKEN` | Actions read, Deployments write, and/or Pull requests write | Optional distinct identity for environment or PR approval. |

The workflows use `${{ github.token }}` for artifact and check/status reads inside their jobs. That
built-in token is not a value the operator creates or stores.

## Consumer quick start

Run the following commands from the target repository. They copy the workflows and configuration;
review the downloaded files before committing them.

```bash
mkdir -p .github/workflows
curl -fsSL https://raw.githubusercontent.com/jonhowe/Release-Automator/v0.3.0/examples/consumer-workflows/release-automator-plan.yml -o .github/workflows/release-automator-plan.yml
curl -fsSL https://raw.githubusercontent.com/jonhowe/Release-Automator/v0.3.0/examples/consumer-workflows/release-automator-execute.yml -o .github/workflows/release-automator-execute.yml
curl -fsSL https://raw.githubusercontent.com/jonhowe/Release-Automator/v0.3.0/examples/consumer-workflows/release-automator-resume.yml -o .github/workflows/release-automator-resume.yml
curl -fsSL https://raw.githubusercontent.com/jonhowe/Release-Automator/v0.3.0/examples/consumer-workflows/release-automator.toml -o release-automator.toml
```

Replace `checks.required = ["ci"]` with every required check-run name exactly as GitHub reports it.
Add project validation commands as argument arrays. For production, replace the action tag in each
workflow with the verified full commit SHA behind that release.

Create the `release` environment with no required reviewer. In this default configuration, the
human's exact-plan-ID dispatch is the approval boundary.

```bash
gh api --method PUT \
  -H 'X-GitHub-Api-Version: 2026-03-10' \
  'repos/{owner}/{repo}/environments/release' \
  -F wait_timer=0 \
  -F prevent_self_review=false \
  -F 'reviewers[]' \
  -F deployment_branch_policy=null \
  --silent

printf '%s' "$OPENAI_API_KEY" |
  gh secret set OPENAI_API_KEY --repo "$GH_REPO"
printf '%s' "$RELEASE_AUTOMATOR_GITHUB_TOKEN" |
  gh secret set RELEASE_AUTOMATOR_GITHUB_TOKEN --repo "$GH_REPO" --env release
unset OPENAI_API_KEY RELEASE_AUTOMATOR_GITHUB_TOKEN

gh secret list --repo "$GH_REPO"
gh secret list --repo "$GH_REPO" --env release
```

Commit the workflow files and configuration to the target repository's default branch. GitHub only
accepts a `workflow_dispatch` request for a workflow present on that branch.

## Optional protected-environment reviewer

Repositories whose GitHub plan supports required environment reviewers can configure one through
the environment API. This adds a second approval after the exact-plan-ID dispatch.

```bash
: "${REVIEWER_LOGIN:?set the authorized GitHub reviewer login}"
REVIEWER_ID=$(gh api "users/$REVIEWER_LOGIN" --jq .id)

gh api --method PUT \
  -H 'X-GitHub-Api-Version: 2026-03-10' \
  'repos/{owner}/{repo}/environments/release' \
  -F wait_timer=0 \
  -F prevent_self_review=true \
  -F 'reviewers[][type]=User' \
  -F "reviewers[][id]=$REVIEWER_ID" \
  -F deployment_branch_policy=null \
  --silent
```

When self-review is prevented, the reviewer credential must represent a different authorized user
from the identity that dispatched the workflow.

## Dispatch and review a plan

Push the source commit to the target repository, then set the full commit SHA and newline-delimited
paths. The API's `return_run_details` option makes dispatch return the exact run ID, avoiding any
ambiguous search for the newest run.

| Input | Example | Notes |
| --- | --- | --- |
| `source_sha` | `0123456789abcdef0123456789abcdef01234567` | Full SHA of a pushed commit in the target repository. |
| `include_paths` | `src` and `README.md` on separate lines | Only changed files under these paths are included. |
| `config_path` | `release-automator.toml` | Path in the reconstructed working tree. |
| `no_release` | `false` | Set `true` to merge without creating a tag or release. |
| `no_latest` | `false` | Set `true` to create a stable release without marking it latest. |
| `version` | `v1.2.3` | Optional explicit SemVer tag; otherwise OpenAI proposes one. |

```bash
SOURCE_SHA=$(git rev-parse HEAD)
INCLUDE_PATHS=$'src\nREADME.md'
DEFAULT_BRANCH=$(gh repo view "$GH_REPO" --json defaultBranchRef --jq .defaultBranchRef.name)

PLAN_RUN_ID=$(
  gh api --method POST \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    'repos/{owner}/{repo}/actions/workflows/release-automator-plan.yml/dispatches' \
    -f ref="$DEFAULT_BRANCH" \
    -F return_run_details=true \
    -f "inputs[source_sha]=$SOURCE_SHA" \
    -f "inputs[include_paths]=$INCLUDE_PATHS" \
    -f 'inputs[config_path]=release-automator.toml' \
    -F 'inputs[no_release]=false' \
    -F 'inputs[no_latest]=false' \
    --jq .workflow_run_id
)
printf 'Plan run ID: %s\n' "$PLAN_RUN_ID"
```

Optional plan inputs such as `branch`, `version`, or `release_channel` use the same
`inputs[name]=value` form.

This shell function waits through the run API and returns failure unless the conclusion is
`success`:

```bash
wait_for_run() {
  local run_id=$1 status conclusion
  while :; do
    status=$(gh api "repos/{owner}/{repo}/actions/runs/$run_id" --jq .status)
    if [ "$status" = completed ]; then
      conclusion=$(gh api "repos/{owner}/{repo}/actions/runs/$run_id" --jq .conclusion)
      [ "$conclusion" = success ]
      return
    fi
    sleep 5
  done
}

wait_for_run "$PLAN_RUN_ID"
```

Get the artifact name from that exact run, derive and validate the full plan ID, and download the
reviewable Markdown and portable bundle:

```bash
ARTIFACT_NAME=$(
  gh api "repos/{owner}/{repo}/actions/runs/$PLAN_RUN_ID/artifacts" \
    --jq '.artifacts[] | select(.name | startswith("release-automator-plan-")) | .name'
)
PLAN_ID=${ARTIFACT_NAME#release-automator-plan-}

if [ "${#PLAN_ID}" -ne 64 ]; then
  printf 'invalid plan ID from artifact: %s\n' "$PLAN_ID" >&2
  exit 1
fi
case "$PLAN_ID" in
  *[!0-9a-f]*) printf 'plan ID is not lowercase hexadecimal\n' >&2; exit 1 ;;
esac

PLAN_DIR=$(mktemp -d)
gh run download "$PLAN_RUN_ID" \
  --repo "$GH_REPO" \
  --name "$ARTIFACT_NAME" \
  --dir "$PLAN_DIR"
cat "$PLAN_DIR/plan.md"
```

Review the entire Markdown manifest, including its complete plan ID, scope, validations, PR
metadata, release behavior, and ordered side effects. A Codex agent must present this manifest and
receive explicit human confirmation of the complete ID; it must never infer approval from the plan
it created.

## Approve and dispatch execution

The approving human must type the complete ID rather than copying it automatically from `PLAN_ID`:

```bash
printf 'Type the complete plan ID to approve: '
IFS= read -r APPROVED_PLAN_ID
if [ "$APPROVED_PLAN_ID" != "$PLAN_ID" ]; then
  printf 'approval does not match the frozen plan\n' >&2
  exit 1
fi

EXECUTE_RUN_ID=$(
  gh api --method POST \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    'repos/{owner}/{repo}/actions/workflows/release-automator-execute.yml/dispatches' \
    -f ref="$DEFAULT_BRANCH" \
    -F return_run_details=true \
    -f "inputs[plan_run_id]=$PLAN_RUN_ID" \
    -f "inputs[plan_id]=$APPROVED_PLAN_ID" \
    --jq .workflow_run_id
)
printf 'Execute run ID: %s\n' "$EXECUTE_RUN_ID"
```

Without required environment reviewers, call `wait_for_run "$EXECUTE_RUN_ID"`. With reviewers,
the job waits before receiving the release credential.

## Approve a pending environment through the API

Use the distinct reviewer credential only after that reviewer has checked the full plan ID and
manifest:

```bash
: "${REVIEWER_GH_TOKEN:?inject the authorized reviewer token}"
ENVIRONMENT_ID=$(
  GH_TOKEN="$REVIEWER_GH_TOKEN" gh api \
    "repos/{owner}/{repo}/actions/runs/$EXECUTE_RUN_ID/pending_deployments" \
    --jq '.[] | select(.environment.name == "release") | .environment.id'
)

GH_TOKEN="$REVIEWER_GH_TOKEN" gh api --method POST \
  "repos/{owner}/{repo}/actions/runs/$EXECUTE_RUN_ID/pending_deployments" \
  -F "environment_ids[]=$ENVIRONMENT_ID" \
  -f state=approved \
  -f "comment=Approved frozen plan $PLAN_ID" \
  --silent

wait_for_run "$EXECUTE_RUN_ID"
```

## Approve a protected pull request and resume

If branch protection requires a PR review, execution safely stops after opening the PR. Copy the
release branch from `plan.md`, approve the PR with a distinct authorized identity, then resume from
the failed execution run:

```bash
: "${RELEASE_BRANCH:?copy the release branch from the reviewed plan}"
: "${REVIEWER_GH_TOKEN:?inject the authorized PR reviewer token}"

PR_NUMBER=$(
  GH_TOKEN="$REVIEWER_GH_TOKEN" gh pr list \
    --repo "$GH_REPO" \
    --head "$RELEASE_BRANCH" \
    --json number \
    --jq '.[0].number'
)
GH_TOKEN="$REVIEWER_GH_TOKEN" gh pr review "$PR_NUMBER" \
  --repo "$GH_REPO" \
  --approve

FAILED_STATE_RUN_ID=$EXECUTE_RUN_ID
RESUME_RUN_ID=$(
  gh api --method POST \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    'repos/{owner}/{repo}/actions/workflows/release-automator-resume.yml/dispatches' \
    -f ref="$DEFAULT_BRANCH" \
    -F return_run_details=true \
    -f "inputs[plan_run_id]=$PLAN_RUN_ID" \
    -f "inputs[state_run_id]=$FAILED_STATE_RUN_ID" \
    -f "inputs[plan_id]=$PLAN_ID" \
    --jq .workflow_run_id
)
wait_for_run "$RESUME_RUN_ID"
```

If a resume run fails after making progress, use that resume run ID as the next
`FAILED_STATE_RUN_ID`. The state artifact prevents completed GitHub operations from being repeated.

## Use the composite action directly

Advanced workflows can call the action directly. Pin a release or verified full commit SHA and
pass credentials only through secret-backed environment variables:

```yaml
- uses: jonhowe/Release-Automator@v0.3.0
  with:
    mode: plan
    include-paths: |
      src
      tests
    config-path: release-automator.toml
  env:
    GITHUB_TOKEN: ${{ github.token }}
    OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
```

For execution, pass the environment-scoped release credential as `GITHUB_TOKEN` and the built-in
job token as `GITHUB_CHECKS_TOKEN`.

## Troubleshooting without a browser

- **Workflow dispatch returns 404:** confirm the workflow file is committed to the default branch
  and that `GH_REPO` names the target repository.
- **Environment setup returns 403:** the operator credential needs Administration write; loading
  secrets additionally needs repository Secrets write and Environments write.
- **Workflow dispatch returns 403:** the operator credential needs Actions write.
- **Run inspection or artifact download returns 403:** the operator credential needs Actions read.
- **The plan artifact is missing:** inspect the exact `PLAN_RUN_ID` conclusion and failed logs with
  `gh run view "$PLAN_RUN_ID" --repo "$GH_REPO" --log-failed`.
- **Execution is waiting:** query the exact run's `pending_deployments`; approve it with the distinct
  reviewer credential or reject it and investigate.
- **A required check never appears:** copy its exact name from the check-runs REST response for an
  existing PR and update `checks.required`.
- **A workflow-file push returns 403:** grant Workflows read/write to the release credential only
  when workflow files are deliberately included in the approved plan.

Do not use `secrets: inherit`, secret-bearing `pull_request_target` workflows, unreviewed source
commits, browser-launching `gh` flags, or UI-only approval procedures. Portable bundles contain
included file bytes and must follow the target repository's confidentiality policy.
