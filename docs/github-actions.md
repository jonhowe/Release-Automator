# GitHub Actions

Release Automator ships a composite action and copy-ready plan, execute, and resume workflows.
Planning prints the branch, commit, pull request, checks, merge, cleanup, and release manifest. The
write phase accepts only the exact 64-character frozen plan ID and can wait at a protected
environment.

## Consumer quick start

Run these commands from the root of the repository you want Release Automator to operate on:

```bash
mkdir -p .github/workflows
curl -fsSL https://raw.githubusercontent.com/jonhowe/Release-Automator/v0.3.0/examples/consumer-workflows/release-automator-plan.yml -o .github/workflows/release-automator-plan.yml
curl -fsSL https://raw.githubusercontent.com/jonhowe/Release-Automator/v0.3.0/examples/consumer-workflows/release-automator-execute.yml -o .github/workflows/release-automator-execute.yml
curl -fsSL https://raw.githubusercontent.com/jonhowe/Release-Automator/v0.3.0/examples/consumer-workflows/release-automator-resume.yml -o .github/workflows/release-automator-resume.yml
curl -fsSL https://raw.githubusercontent.com/jonhowe/Release-Automator/v0.3.0/examples/consumer-workflows/release-automator.toml -o release-automator.toml
```

Then:

1. Edit `release-automator.toml`. Replace `checks.required = ["ci"]` with the exact check-run names
   shown on that repository's pull requests. Add project validation commands if desired.
2. Create the two secrets and `release` environment described below in the target repository.
3. Commit these four files to the target repository's default branch. Manual Actions workflows do
   not appear in the Actions tab until their workflow files exist on the default branch.
4. Push the change you want to release to a branch in the same repository and copy its full SHA with
   `git rev-parse HEAD`.
5. Run **Plan a release** with that SHA, newline-delimited changed paths, and
   `release-automator.toml`. Review the complete manifest and copy its run ID and 64-character plan
   ID into **Execute an approved release plan**.
6. If required reviewers are enabled, approve the `release` environment job. The action creates the
   branch, commit, PR, merge, and optional release only after the execute workflow receives the
   exact plan ID and any configured environment approval.

The public action works from public or private target repositories, subject to the target account's
Actions policy. An organization that restricts third-party actions must allow
`jonhowe/Release-Automator`. GitHub Free supports environment secrets only for public repositories;
private or internal repositories require a plan that supports environment secrets. Required
environment reviewers on GitHub Free, Pro, and Team are available only for public repositories.

## Configure the target repository

Configure everything below in the repository being released—for example, `your-org/your-repo`—not
in Release-Automator. GitHub environments and their secrets are repository-specific.

There are two secret values to create. `release` is an environment, not a third secret.

| Name | Target-repository location | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | Repository Actions secret | Used only while planning metadata. |
| `RELEASE_AUTOMATOR_GITHUB_TOKEN` | `release` environment secret | Pushes, opens and merges the PR, deletes the branch, and creates the release. |

The workflows also use `${{ github.token }}` to download artifacts and read check runs and commit
statuses. GitHub creates this token for each job; you do not create or store it. The workflow grants
it `actions: read`, `checks: read`, `statuses: read`, and `contents: read` only.

## Create the fine-grained PAT

Open GitHub **Settings → Developer settings → Personal access tokens → Fine-grained tokens**, then:

1. Choose the account that owns the target repository as the resource owner and set an expiration.
2. Under **Repository access**, select **Only select repositories** and choose the target repository.
3. Under **Repository permissions**, grant **Contents: Read and write** and **Pull requests: Read and
   write**. Add **Workflows: Read and write** only if a planned change may modify `.github/workflows/`.
4. Generate the token, copy the `github_pat_...` value, and save it as the environment secret below.

The PAT does not need a **Checks** or **Commit statuses** selection. Release Automator v0.3 reads
those through the separate built-in job token.

## Create the approval boundary

In the target repository, open **Settings → Environments → New environment**, name it `release`, and
add `RELEASE_AUTOMATOR_GITHUB_TOKEN` under **Environment secrets**. Add required reviewers when your
GitHub plan supports them. A solo maintainer should not enable **Prevent self-review** unless another
reviewer can approve the job.

GitHub withholds the write PAT until the execute or resume job receives environment approval.
Reviewers should compare the planning run's full plan ID and manifest before approving it.

## Install the consumer workflows

Copy the contents of `examples/consumer-workflows/` into the target repository:

```text
.github/workflows/release-automator-plan.yml
.github/workflows/release-automator-execute.yml
.github/workflows/release-automator-resume.yml
release-automator.toml
```

The templates call `jonhowe/Release-Automator@v0.3.0`. For production, replace the tag with the full
commit SHA behind that release after verifying it. Adjust `release-automator.toml`, especially
`checks.required`: each entry must exactly match a check-run name shown on the target repository's
pull requests. Add project-specific validation commands as argument arrays.

The workflows committed under this repository's own `.github/workflows/` release
Release-Automator itself. The consumer templates omit that self-checkout and operate on the
repository in which they are installed.

## Plan, approve, and recover

Run **Plan a release** from the target repository's Actions tab with a full source commit SHA and
newline-delimited paths. Planning reconstructs that diff on the default branch, validates it, prints
the frozen plan, and uploads a 30-day artifact. It does not write to the repository.

Example inputs for a first run:

| Input | Example | Notes |
| --- | --- | --- |
| `source_sha` | `0123456789abcdef0123456789abcdef01234567` | Full SHA of a pushed commit in the target repository. |
| `include_paths` | `src` and `README.md` on separate lines | Only changed files under these paths are included. |
| `config_path` | `release-automator.toml` | Path in the reconstructed working tree. |
| `no_release` | `false` | Set `true` to merge without creating a tag or release. |
| `no_latest` | `false` | Set `true` to create a stable release without marking it latest. |
| `version` | `v1.2.3` | Optional explicit SemVer tag; otherwise OpenAI proposes one. |

The planning run ID is the number in its URL: `.../actions/runs/RUN_ID`. The plan ID is the full
64-character value printed in the log and job summary. Both values are required by the execute
workflow; the execute workflow does not accept a shortened plan ID.

After review, run **Execute an approved release plan** with the planning run ID and exact full plan
ID. Approve the `release` environment gate. If execution fails after approval, run **Resume a release
plan** with the plan run ID, failed run ID, and the same plan ID.

Repository rules still apply. Required checks should be listed in `checks.required`. If the target
branch requires human PR approval, execution will normally stop after creating the PR because it
cannot approve its own new PR. Approve the generated PR, then use **Resume a release plan** so
completed work is not repeated.

Do not use `secrets: inherit`, trigger secret-bearing jobs with `pull_request_target`, or plan an
unreviewed commit. Configured validation commands execute proposed repository code. Portable bundles
contain included file bytes and must follow the target repository's confidentiality policy.

## Use the composite action directly

Advanced workflows can call the action directly. Pin a release or full commit SHA, pass settings
through `with`, and pass credentials only through secret-backed environment variables:

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

For execution, pass the write PAT as `GITHUB_TOKEN` and `${{ github.token }}` as
`GITHUB_CHECKS_TOKEN`. Prefer a GitHub App installation token instead of a PAT when short-lived or
organization-managed credentials are required.

## Troubleshooting

- **Workflows do not appear:** commit the workflow files to the target repository's default branch.
- **Source SHA is unknown:** push the source branch to the target repository and use
  `git rev-parse HEAD`. A commit that exists only in a fork may not be present in the workflow's
  checkout.
- **A required check never appears:** copy the exact check-run name from an existing pull request and
  confirm its workflow triggers on pull requests to the default branch.
- **A workflow-file push returns 403:** add **Workflows: Read and write** to the fine-grained PAT.
- **Execution is waiting:** open the workflow run and approve the job that references the `release`
  environment.
