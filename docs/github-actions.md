# GitHub Actions

Release Automator ships a composite action and copy-ready plan, execute, and resume workflows.
Planning prints the branch, commit, pull request, checks, merge, cleanup, and release manifest. The
write phase accepts only the exact 64-character frozen plan ID and waits at a protected environment.

## Configure the target repository

Configure everything below in the repository being released. For example, when releasing
`jonhowe/Media-Atlas`, create its secrets and environment in **Media-Atlas**, not in
Release-Automator. GitHub environments and their secrets are repository-specific.

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

After review, run **Execute an approved release plan** with the planning run ID and exact full plan
ID. Approve the `release` environment gate. If execution fails after approval, run **Resume a release
plan** with the plan run ID, failed run ID, and the same plan ID.

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
