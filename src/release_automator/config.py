from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import ValidationError

from release_automator.errors import ConfigurationError
from release_automator.models import RepoConfig


def load_config(path: Path | None) -> RepoConfig:
    if path is None:
        return RepoConfig()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        return RepoConfig.model_validate(data)
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise ConfigurationError(f"invalid configuration {path}: {exc}") from exc
