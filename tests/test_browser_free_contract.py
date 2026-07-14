from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[1]
PUBLIC_GUIDANCE = [
    REPOSITORY_ROOT / "README.md",
    REPOSITORY_ROOT / "SECURITY.md",
    *sorted((REPOSITORY_ROOT / "docs").glob("*.md")),
]
PYTHON_RUNTIME = [
    *sorted((REPOSITORY_ROOT / "src").rglob("*.py")),
    *sorted((REPOSITORY_ROOT / "scripts").rglob("*.py")),
]
FORBIDDEN_PACKAGES = {"playwright", "pyppeteer", "selenium"}
FORBIDDEN_IMPORTS = FORBIDDEN_PACKAGES | {"webbrowser"}


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module.split(".", 1)[0])
    return imports


def test_runtime_has_no_browser_automation_dependency_or_import() -> None:
    project = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = list(project["project"]["dependencies"])
    for group in project.get("dependency-groups", {}).values():
        dependencies.extend(group)
    dependency_names = {
        re.split(r"[<>=!~\[]", dependency, maxsplit=1)[0].lower() for dependency in dependencies
    }

    assert dependency_names.isdisjoint(FORBIDDEN_PACKAGES)
    for path in PYTHON_RUNTIME:
        assert _top_level_imports(path).isdisjoint(FORBIDDEN_IMPORTS), path


def test_public_guidance_has_no_browser_launch_or_ui_only_requirement() -> None:
    guidance = "\n".join(path.read_text(encoding="utf-8") for path in PUBLIC_GUIDANCE)

    assert re.search(r"(?m)^\s*gh auth login(?:\s|$)", guidance) is None
    assert re.search(r"(?m)^\s*gh\b.*(?:^|\s)--web(?:\s|$)", guidance) is None
    assert "Actions tab" not in guidance
    assert "Open GitHub **Settings" not in guidance


def test_headless_runbook_covers_the_complete_actions_lifecycle() -> None:
    runbook = (REPOSITORY_ROOT / "docs" / "github-actions.md").read_text(encoding="utf-8")

    required_fragments = {
        "repos/{owner}/{repo}/environments/release",
        "gh secret set OPENAI_API_KEY",
        "return_run_details=true",
        "--jq .workflow_run_id",
        "actions/runs/$PLAN_RUN_ID/artifacts",
        "Type the complete plan ID to approve",
        "pending_deployments",
        "gh pr review",
        "release-automator-resume.yml/dispatches",
    }
    missing = sorted(fragment for fragment in required_fragments if fragment not in runbook)

    assert missing == []
