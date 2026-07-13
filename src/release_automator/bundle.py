from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Literal
from zipfile import ZIP_DEFLATED, BadZipFile, ZipFile

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from release_automator.errors import AutomatorError, PlanDriftError
from release_automator.git_ops import GitRepo, parse_github_repo
from release_automator.models import FrozenPlan
from release_automator.state import StateStore

BUNDLE_SCHEMA_VERSION = 1
MAX_METADATA_BYTES = 2_000_000
MAX_BUNDLE_CONTENT_BYTES = 500_000_000


class BundleEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    kind: Literal["file", "symlink", "deleted"]
    blob: str | None = None
    sha256: str | None = None
    size: int = Field(default=0, ge=0)
    mode: int | None = Field(default=None, ge=0, le=0o777)


class BundleManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = BUNDLE_SCHEMA_VERSION
    plan_id: str = Field(min_length=64, max_length=64)
    base_sha: str = Field(min_length=40, max_length=64)
    entries: list[BundleEntry]


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or path.parts[0] == ".git"
    ):
        raise AutomatorError(f"unsafe bundle path: {value!r}")
    return path


def _blob_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_zip_text(archive: ZipFile, name: str, value: str) -> None:
    archive.writestr(name, value.encode("utf-8"))


def export_plan_bundle(repo_path: Path, plan: FrozenPlan, destination: Path) -> Path:
    if not plan.portable:
        raise AutomatorError("only portable plans can be exported")
    if plan.plan_id != StateStore.calculate_plan_id(plan):
        raise AutomatorError("cannot export a plan with an invalid hash")

    repo = GitRepo(repo_path)
    if repo.snapshot_hash(plan.include_paths, include_mode=True) != plan.snapshot_hash:
        raise PlanDriftError("included files changed before bundle export")

    entries: list[BundleEntry] = []
    blobs: dict[str, bytes] = {}
    for index, path_text in enumerate(plan.include_paths):
        _safe_relative_path(path_text)
        path = repo.root / path_text
        if path.is_symlink():
            data = os.readlink(path).encode("utf-8")
            blob = f"blobs/{index}"
            entries.append(
                BundleEntry(
                    path=path_text,
                    kind="symlink",
                    blob=blob,
                    sha256=_blob_digest(data),
                    size=len(data),
                )
            )
            blobs[blob] = data
        elif path.is_file():
            data = path.read_bytes()
            blob = f"blobs/{index}"
            entries.append(
                BundleEntry(
                    path=path_text,
                    kind="file",
                    blob=blob,
                    sha256=_blob_digest(data),
                    size=len(data),
                    mode=stat.S_IMODE(path.stat().st_mode),
                )
            )
            blobs[blob] = data
        elif not path.exists():
            entries.append(BundleEntry(path=path_text, kind="deleted"))
        else:
            raise AutomatorError(f"unsupported bundle path: {path_text}")

    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    manifest = BundleManifest(
        plan_id=plan.plan_id,
        base_sha=plan.base_sha,
        entries=entries,
    )
    with ZipFile(destination, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        _write_zip_text(archive, "plan.json", plan.model_dump_json(indent=2))
        _write_zip_text(archive, "manifest.json", manifest.model_dump_json(indent=2))
        for name, data in blobs.items():
            archive.writestr(name, data)
    return destination


def _read_metadata(archive: ZipFile, name: str) -> bytes:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise AutomatorError(f"plan bundle is missing {name}") from exc
    if info.file_size > MAX_METADATA_BYTES:
        raise AutomatorError(f"plan bundle {name} is too large")
    return archive.read(info)


def _target_path(repo: GitRepo, path_text: str) -> Path:
    relative = _safe_relative_path(path_text)
    current = repo.root
    for part in relative.parts[:-1]:
        current /= part
        if current.is_symlink():
            raise AutomatorError(f"bundle target parent is a symlink: {path_text}")
    return repo.root.joinpath(*relative.parts)


def import_plan_bundle(
    repo_path: Path,
    bundle_path: Path,
    *,
    expected_plan_id: str | None = None,
) -> FrozenPlan:
    repo = GitRepo(repo_path)
    repo.assert_normal_state()
    if repo.changed_paths() or repo.staged_paths():
        raise PlanDriftError("bundle import requires a clean working tree")

    try:
        with ZipFile(bundle_path.expanduser().resolve(), "r") as archive:
            names = [item.filename for item in archive.infolist()]
            if len(names) != len(set(names)):
                raise AutomatorError("plan bundle contains duplicate members")
            plan_data = _read_metadata(archive, "plan.json")
            manifest_data = _read_metadata(archive, "manifest.json")
            try:
                plan = FrozenPlan.model_validate_json(plan_data)
                manifest = BundleManifest.model_validate_json(manifest_data)
            except ValidationError as exc:
                raise AutomatorError(f"invalid plan bundle metadata: {exc}") from exc

            if not plan.portable:
                raise AutomatorError("plan bundle is not marked portable")
            if plan.plan_id != StateStore.calculate_plan_id(plan):
                raise AutomatorError("frozen plan hash is invalid")
            if expected_plan_id is not None and not plan.plan_id.startswith(expected_plan_id):
                raise AutomatorError("requested plan ID does not match the imported bundle")
            if manifest.plan_id != plan.plan_id or manifest.base_sha != plan.base_sha:
                raise AutomatorError("plan bundle manifest does not match the frozen plan")
            if sorted(item.path for item in manifest.entries) != plan.include_paths:
                raise AutomatorError("plan bundle paths do not match the frozen scope")

            expected_names = {"plan.json", "manifest.json"}
            expected_names.update(item.blob for item in manifest.entries if item.blob)
            if set(names) != expected_names:
                raise AutomatorError("plan bundle contains unexpected or missing members")

            total_size = sum(item.size for item in manifest.entries)
            if total_size > MAX_BUNDLE_CONTENT_BYTES:
                raise AutomatorError("plan bundle content is too large")
            contents: dict[str, bytes] = {}
            for item in manifest.entries:
                _safe_relative_path(item.path)
                if item.kind == "deleted":
                    if any(value is not None for value in (item.blob, item.sha256, item.mode)):
                        raise AutomatorError(f"deleted bundle entry has content: {item.path}")
                    continue
                if not item.blob or not item.sha256:
                    raise AutomatorError(f"bundle entry is missing content metadata: {item.path}")
                info = archive.getinfo(item.blob)
                if info.file_size != item.size:
                    raise AutomatorError(f"bundle entry size mismatch: {item.path}")
                data = archive.read(info)
                if _blob_digest(data) != item.sha256:
                    raise AutomatorError(f"bundle entry hash mismatch: {item.path}")
                contents[item.path] = data
    except (BadZipFile, OSError) as exc:
        raise AutomatorError(f"could not read plan bundle: {exc}") from exc

    if repo.current_branch() != plan.base_branch:
        raise PlanDriftError(f"bundle import must start on {plan.base_branch!r}")
    if repo.head_sha() != plan.base_sha:
        raise PlanDriftError("local base HEAD does not match the bundled plan")
    if parse_github_repo(repo.origin_url()) != plan.repo_full_name:
        raise PlanDriftError("bundle belongs to a different GitHub repository")

    entries_by_path = {item.path: item for item in manifest.entries}
    for path_text in plan.include_paths:
        item = entries_by_path[path_text]
        target = _target_path(repo, path_text)
        if item.kind == "deleted":
            if target.is_dir() and not target.is_symlink():
                raise AutomatorError(f"refusing to delete directory from bundle: {path_text}")
            target.unlink(missing_ok=True)
            continue

        if target.is_dir() and not target.is_symlink():
            raise AutomatorError(f"refusing to replace directory from bundle: {path_text}")
        target.unlink(missing_ok=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        data = contents[path_text]
        if item.kind == "symlink":
            try:
                link_target = data.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise AutomatorError(f"invalid symlink target in bundle: {path_text}") from exc
            os.symlink(link_target, target)
        else:
            if item.mode is None:
                raise AutomatorError(f"file bundle entry is missing its mode: {path_text}")
            target.write_bytes(data)
            target.chmod(item.mode)

    if repo.changed_paths() != plan.include_paths:
        raise PlanDriftError("rehydrated working tree does not match the frozen scope")
    if repo.snapshot_hash(plan.include_paths, include_mode=True) != plan.snapshot_hash:
        raise PlanDriftError("rehydrated working tree does not match the frozen snapshot")
    imported = StateStore(repo).save_plan(plan)
    if imported.plan_id != manifest.plan_id:
        raise AutomatorError("import changed the frozen plan ID")
    return imported
