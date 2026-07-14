from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from conftest import run_git

from release_automator.bundle import export_plan_bundle, import_plan_bundle
from release_automator.errors import AutomatorError
from release_automator.git_ops import GitRepo
from release_automator.models import ChangeClass, FrozenPlan, ModelProposal, RepoConfig
from release_automator.state import StateStore


def _portable_plan(repo: GitRepo, include_paths: list[str]) -> FrozenPlan:
    plan = FrozenPlan(
        repo_root=str(repo.root),
        repo_full_name="example/project",
        remote_url=repo.origin_url(),
        base_branch="main",
        base_sha=repo.head_sha(),
        branch_name="agent/portable-plan",
        include_paths=include_paths,
        excluded_paths=[],
        snapshot_hash=repo.snapshot_hash(include_paths, include_mode=True),
        portable=True,
        config=RepoConfig(),
        validation_results=[],
        releases=[],
        release_enabled=False,
        proposal=ModelProposal(
            branch_slug="portable-plan",
            commit_message="Apply portable plan",
            pr_title="Apply portable plan",
            pr_body="## Summary\n\n- Apply the portable plan.",
            change_class=ChangeClass.INTERNAL,
        ),
    )
    return StateStore(repo).save_plan(plan)


def _clone_base(source: Path, destination: Path) -> None:
    run_git(source.parent, "clone", str(source), str(destination))
    run_git(destination, "config", "user.name", "Release Automator Tests")
    run_git(destination, "config", "user.email", "tests@example.com")
    run_git(destination, "remote", "set-url", "origin", "git@github.com:example/project.git")


def test_bundle_rehydrates_exact_files_without_deleted_content(
    git_repository: Path, tmp_path: Path
) -> None:
    removed = git_repository / "removed.txt"
    removed.write_text("historical-secret-value\n", encoding="utf-8")
    run_git(git_repository, "add", removed.name)
    run_git(git_repository, "commit", "-m", "Add removal fixture")
    target = tmp_path / "target"
    _clone_base(git_repository, target)

    (git_repository / "README.md").write_text("updated\n", encoding="utf-8")
    removed.unlink()
    script = git_repository / "bin" / "tool"
    script.parent.mkdir()
    script.write_bytes(b"#!/bin/sh\necho ready\n")
    script.chmod(0o755)
    os.symlink("README.md", git_repository / "readme-link")
    include_paths = ["README.md", "bin/tool", "readme-link", "removed.txt"]
    plan = _portable_plan(GitRepo(git_repository), include_paths)
    bundle = export_plan_bundle(git_repository, plan, tmp_path / "plan.zip")

    with ZipFile(bundle) as archive:
        archived_content = b"".join(archive.read(name) for name in archive.namelist())
    assert b"historical-secret-value" not in archived_content

    imported = import_plan_bundle(target, bundle, expected_plan_id=plan.plan_id)
    assert imported.plan_id == plan.plan_id
    assert (target / "README.md").read_text(encoding="utf-8") == "updated\n"
    assert not (target / "removed.txt").exists()
    assert (target / "readme-link").is_symlink()
    assert os.readlink(target / "readme-link") == "README.md"
    assert stat.S_IMODE((target / "bin" / "tool").stat().st_mode) == 0o755
    assert GitRepo(target).snapshot_hash(include_paths, include_mode=True) == plan.snapshot_hash


def test_bundle_rejects_tampered_blob(git_repository: Path, tmp_path: Path) -> None:
    target = tmp_path / "target"
    _clone_base(git_repository, target)
    (git_repository / "feature.txt").write_text("expected\n", encoding="utf-8")
    plan = _portable_plan(GitRepo(git_repository), ["feature.txt"])
    original = export_plan_bundle(git_repository, plan, tmp_path / "plan.zip")
    tampered = tmp_path / "tampered.zip"

    with ZipFile(original) as source, ZipFile(
        tampered, "w", compression=ZIP_DEFLATED
    ) as destination:
        for name in source.namelist():
            data = b"tampered\n" if name == "blobs/0" else source.read(name)
            destination.writestr(name, data)

    with pytest.raises(AutomatorError, match="size mismatch|hash mismatch"):
        import_plan_bundle(target, tampered, expected_plan_id=plan.plan_id)
    assert GitRepo(target).changed_paths() == []


def test_bundle_rejects_wrong_plan_id_before_rehydration(
    git_repository: Path, tmp_path: Path
) -> None:
    target = tmp_path / "target"
    _clone_base(git_repository, target)
    (git_repository / "feature.txt").write_text("expected\n", encoding="utf-8")
    plan = _portable_plan(GitRepo(git_repository), ["feature.txt"])
    bundle = export_plan_bundle(git_repository, plan, tmp_path / "plan.zip")

    with pytest.raises(AutomatorError, match="requested plan ID"):
        import_plan_bundle(target, bundle, expected_plan_id="0" * 64)
    assert GitRepo(target).changed_paths() == []


def test_bundle_rejects_nonportable_plan(git_repository: Path, tmp_path: Path) -> None:
    (git_repository / "feature.txt").write_text("expected\n", encoding="utf-8")
    plan = _portable_plan(GitRepo(git_repository), ["feature.txt"])
    plan.portable = False
    plan.plan_id = StateStore.calculate_plan_id(plan)

    with pytest.raises(AutomatorError, match="only portable plans"):
        export_plan_bundle(git_repository, plan, tmp_path / "plan.zip")


def test_saved_plan_without_latest_setting_keeps_legacy_hash(git_repository: Path) -> None:
    repo = GitRepo(git_repository)
    plan = _portable_plan(repo, ["README.md"])
    plan.model_fields_set.discard("release_make_latest")
    plan.plan_id = StateStore.calculate_plan_id(plan)
    payload = plan.model_dump(mode="json")
    payload.pop("release_make_latest")
    store = StateStore(repo)
    legacy_path = store.plans / f"{plan.plan_id}.json"
    legacy_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    loaded = store.load_plan(plan.plan_id)

    assert loaded.plan_id == plan.plan_id
    assert loaded.release_make_latest is True
    assert "release_make_latest" not in loaded.model_fields_set


def test_bundle_without_latest_setting_keeps_legacy_hash(
    git_repository: Path, tmp_path: Path
) -> None:
    target = tmp_path / "legacy-target"
    _clone_base(git_repository, target)
    (git_repository / "feature.txt").write_text("expected\n", encoding="utf-8")
    plan = _portable_plan(GitRepo(git_repository), ["feature.txt"])
    current_bundle = export_plan_bundle(git_repository, plan, tmp_path / "current.zip")

    plan.model_fields_set.discard("release_make_latest")
    plan.plan_id = StateStore.calculate_plan_id(plan)
    plan_payload = plan.model_dump(mode="json")
    plan_payload.pop("release_make_latest")
    legacy_bundle = tmp_path / "legacy.zip"
    with ZipFile(current_bundle) as source, ZipFile(
        legacy_bundle, "w", compression=ZIP_DEFLATED
    ) as destination:
        for name in source.namelist():
            data = source.read(name)
            if name == "plan.json":
                data = json.dumps(plan_payload, indent=2).encode()
            elif name == "manifest.json":
                manifest = json.loads(data)
                manifest["plan_id"] = plan.plan_id
                data = json.dumps(manifest, indent=2).encode()
            destination.writestr(name, data)

    imported = import_plan_bundle(target, legacy_bundle, expected_plan_id=plan.plan_id)

    assert imported.plan_id == plan.plan_id
    assert imported.release_make_latest is True
    saved = StateStore(GitRepo(target)).plans / f"{plan.plan_id}.json"
    assert "release_make_latest" not in json.loads(saved.read_text(encoding="utf-8"))
