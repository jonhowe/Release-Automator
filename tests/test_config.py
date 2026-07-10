from __future__ import annotations

from pathlib import Path

import pytest

from release_automator.config import load_config
from release_automator.errors import ConfigurationError


def test_load_config_parses_validation_commands(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """[checks]
required = ["ci"]

[[validation]]
name = "tests"
cwd = "backend"
argv = ["python", "-m", "pytest"]
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.checks.required == ["ci"]
    assert config.validation[0].argv == ["python", "-m", "pytest"]


def test_load_config_rejects_unknown_fields(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text("unknown = true\n", encoding="utf-8")
    with pytest.raises(ConfigurationError):
        load_config(path)
