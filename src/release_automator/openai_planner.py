from __future__ import annotations

import json
import os
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from release_automator.errors import ExternalServiceError
from release_automator.models import ModelProposal, ReleaseInfo, RepoConfig, ValidationResult

SYSTEM_PROMPT = """You prepare publication metadata for a deterministic GitHub workflow.
Return only the requested structured object. Base every statement on the supplied diff and facts.

Rules:
- branch_slug is lowercase kebab-case without a prefix, slash, issue number, or filler words;
- commit_message is imperative, concise, and at most 72 characters;
- pr_title summarizes the complete included diff;
- pr_body is Markdown with Summary, Why, Validation, and Scope sections;
- never claim a validation command ran unless it appears in validation_results;
- change_class is one of breaking, feature, fix, docs, ci, or internal;
- when release_enabled is true, inspect releases and propose one exact strictly newer SemVer tag;
- preserve the repository's established tag prefix and prerelease naming style;
- release_notes describe changes since the most relevant previous release;
- release_notes include no invented work;
- when release_enabled is false, release fields must be null.
"""


def propose_metadata(
    *,
    config: RepoConfig,
    repo_full_name: str,
    base_branch: str,
    include_paths: list[str],
    excluded_paths: list[str],
    diff: str,
    validations: list[ValidationResult],
    releases: list[ReleaseInfo],
    release_enabled: bool,
    client: OpenAI | None = None,
) -> ModelProposal:
    if not os.environ.get("OPENAI_API_KEY") and client is None:
        raise ExternalServiceError("OPENAI_API_KEY is required to create a publication plan")
    payload: dict[str, Any] = {
        "repository": repo_full_name,
        "base_branch": base_branch,
        "include_paths": include_paths,
        "excluded_paths": excluded_paths,
        "validation_results": [item.model_dump(mode="json") for item in validations],
        "releases": [item.model_dump(mode="json") for item in releases],
        "release_enabled": release_enabled,
        "release_side_effect_notice": config.release.side_effect_notice,
        "diff": diff,
    }
    try:
        api = client or OpenAI()
        response = api.responses.parse(
            model=config.openai.model,
            store=False,
            reasoning={"effort": "low"},
            input=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, sort_keys=True, separators=(",", ":")),
                },
            ],
            text_format=ModelProposal,
        )
        proposal = response.output_parsed
    except Exception as exc:
        raise ExternalServiceError(f"OpenAI planning request failed: {exc}") from exc
    if proposal is None:
        raise ExternalServiceError("OpenAI returned no parsed publication proposal")
    try:
        return ModelProposal.model_validate(proposal)
    except ValidationError as exc:
        raise ExternalServiceError(f"OpenAI returned invalid publication metadata: {exc}") from exc


def apply_overrides(proposal: ModelProposal, overrides: dict[str, Any]) -> ModelProposal:
    values = proposal.model_dump(mode="json")
    updates = {key: value for key, value in overrides.items() if value is not None}
    if "suggested_version" in updates and "version_rationale" not in updates:
        version = updates["suggested_version"]
        updates["version_rationale"] = (
            f"Release version explicitly overridden to {version} during planning."
        )
    values.update(updates)
    try:
        return ModelProposal.model_validate(values)
    except ValidationError as exc:
        raise ExternalServiceError(f"metadata override is invalid: {exc}") from exc
