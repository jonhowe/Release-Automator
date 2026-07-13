from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ValidationCommand(StrictModel):
    name: str = Field(min_length=1)
    argv: list[str] = Field(min_length=1)
    cwd: str = "."


class GitConfig(StrictModel):
    branch_prefix: str = "agent/"
    merge_method: Literal["squash", "merge", "rebase"] = "squash"
    delete_remote_branch: bool = True
    delete_local_branch: bool = True


class ChecksConfig(StrictModel):
    required: list[str] = Field(default_factory=list)
    accepted_conclusions: list[str] = Field(default_factory=lambda: ["success"])
    poll_seconds: int = Field(default=10, ge=1)
    discovery_timeout_seconds: int = Field(default=300, ge=1)
    completion_timeout_seconds: int = Field(default=3600, ge=1)
    allow_no_checks: bool = False


class ReleaseConfig(StrictModel):
    enabled_by_default: bool = True
    tag_prefix: str = "v"
    side_effect_notice: str = ""


class OpenAIConfig(StrictModel):
    model: str = "gpt-5.4-mini-2026-03-17"
    max_diff_bytes: int = Field(default=200_000, ge=1)


class RepoConfig(StrictModel):
    git: GitConfig = Field(default_factory=GitConfig)
    checks: ChecksConfig = Field(default_factory=ChecksConfig)
    release: ReleaseConfig = Field(default_factory=ReleaseConfig)
    openai: OpenAIConfig = Field(default_factory=OpenAIConfig)
    validation: list[ValidationCommand] = Field(default_factory=list)


class ReleaseChannel(StrEnum):
    STABLE = "stable"
    PRERELEASE = "prerelease"


class ChangeClass(StrEnum):
    BREAKING = "breaking"
    FEATURE = "feature"
    FIX = "fix"
    DOCS = "docs"
    CI = "ci"
    INTERNAL = "internal"


class ModelProposal(StrictModel):
    branch_slug: str = Field(min_length=1, max_length=80)
    commit_message: str = Field(min_length=1, max_length=72)
    pr_title: str = Field(min_length=1, max_length=120)
    pr_body: str = Field(min_length=1, max_length=20_000)
    change_class: ChangeClass
    suggested_version: str | None = None
    release_channel: ReleaseChannel | None = None
    version_rationale: str | None = Field(default=None, max_length=1_000)
    release_notes: str | None = Field(default=None, max_length=50_000)

    @field_validator("branch_slug")
    @classmethod
    def branch_slug_must_have_content(cls, value: str) -> str:
        if not any(character.isalnum() for character in value):
            raise ValueError("branch_slug must contain a letter or number")
        return value


class ValidationResult(StrictModel):
    name: str
    argv: list[str]
    cwd: str
    returncode: int
    output: str = ""


class ReleaseInfo(StrictModel):
    tag_name: str
    name: str = ""
    prerelease: bool = False
    draft: bool = False
    published_at: str | None = None
    url: str | None = None


class FrozenPlan(StrictModel):
    schema_version: int = 1
    plan_id: str = ""
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    repo_root: str
    repo_full_name: str
    remote_url: str
    base_branch: str
    base_sha: str
    branch_name: str
    include_paths: list[str]
    excluded_paths: list[str]
    snapshot_hash: str
    redacted_secret_types: list[str] = Field(default_factory=list)
    portable: bool = False
    config: RepoConfig
    validation_results: list[ValidationResult]
    releases: list[ReleaseInfo]
    release_enabled: bool
    proposal: ModelProposal


class Phase(StrEnum):
    PLANNED = "planned"
    COMMITTED = "committed"
    PUSHED = "pushed"
    PR_OPEN = "pr_open"
    CHECKS_PASSED = "checks_passed"
    MERGED = "merged"
    RELEASED = "released"


class RunState(StrictModel):
    plan_id: str
    phase: Phase = Phase.PLANNED
    approved_at: datetime
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    branch_name: str
    commit_sha: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    merge_sha: str | None = None
    release_url: str | None = None
    warning: str | None = None
    error: str | None = None
