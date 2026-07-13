# Security Policy

## Supported Versions

Security fixes are provided for the latest published release. Older releases are not maintained
once a newer version is available.

## Reporting a Vulnerability

Use GitHub's private vulnerability reporting for this repository. Do not open a public issue with
exploit details, credentials, private repository content, or other sensitive information.

If private reporting is unavailable, open a minimal issue asking the maintainer to establish a
private contact channel. Include no vulnerability details in that issue.

When reporting privately, include the affected version, impact, reproduction steps, and any known
mitigations. You should receive an acknowledgment within seven days. Please allow time for a fix
and coordinated disclosure before publishing details.

## Credential Handling

Never attach real API keys, tokens, private keys, or credential files to an issue or pull request.
Revoke any credential immediately if it is accidentally disclosed.

For GitHub Actions, create credentials in the repository being released. Store `OPENAI_API_KEY` as
a repository secret and place the scoped GitHub write token in a protected `release` environment as
`RELEASE_AUTOMATOR_GITHUB_TOKEN`. Use the built-in job token as `GITHUB_CHECKS_TOKEN` for read-only
check/status access; do not broaden the write PAT for that purpose. Secrets must enter the composite
action through environment variables, never action inputs, repository variables, artifacts,
summaries, or committed configuration. Use required environment reviewers and avoid secret-bearing
`pull_request_target` workflows. Pin third-party actions to full commit SHAs.
