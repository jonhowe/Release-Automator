from __future__ import annotations

from pathlib import Path

from release_automator.config import load_config

TEMPLATE_ROOT = Path("examples/consumer-workflows")
ACTION_REFERENCE = "uses: jonhowe/Release-Automator@v0.3.0"


def test_consumer_workflows_pin_the_public_action() -> None:
    workflows = sorted(TEMPLATE_ROOT.glob("release-automator-*.yml"))

    assert [path.name for path in workflows] == [
        "release-automator-execute.yml",
        "release-automator-plan.yml",
        "release-automator-resume.yml",
    ]
    for workflow in workflows:
        text = workflow.read_text(encoding="utf-8")
        assert ACTION_REFERENCE in text
        assert "./.release-automator-action" not in text


def test_consumer_write_workflows_split_read_and_write_tokens() -> None:
    for mode in ("execute", "resume"):
        text = (TEMPLATE_ROOT / f"release-automator-{mode}.yml").read_text(encoding="utf-8")
        assert "environment: release" in text
        assert "checks: read" in text
        assert "statuses: read" in text
        assert "GITHUB_TOKEN: ${{ secrets.RELEASE_AUTOMATOR_GITHUB_TOKEN }}" in text
        assert "GITHUB_CHECKS_TOKEN: ${{ github.token }}" in text


def test_consumer_plan_uses_the_openai_repository_secret() -> None:
    text = (TEMPLATE_ROOT / "release-automator-plan.yml").read_text(encoding="utf-8")

    assert "OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}" in text
    assert "GITHUB_TOKEN: ${{ github.token }}" in text


def test_consumer_configuration_is_valid() -> None:
    config = load_config(TEMPLATE_ROOT / "release-automator.toml")

    assert config.checks.required == ["ci"]
    assert config.git.branch_prefix == "release-automator/"
