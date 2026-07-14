from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError

from release_automator.errors import AutomatorError
from release_automator.git_ops import GitRepo
from release_automator.models import FrozenPlan, RunState


class StateStore:
    COMPATIBILITY_FIELDS = {"redacted_secret_types", "portable", "release_make_latest"}

    def __init__(self, repo: GitRepo) -> None:
        self.root = repo.git_path("release-automator")
        self.plans = self.root / "plans"
        self.runs = self.root / "runs"

    @staticmethod
    def calculate_plan_id(plan: FrozenPlan) -> str:
        values = plan.model_dump(mode="json")
        values["plan_id"] = ""
        for optional_field in StateStore.COMPATIBILITY_FIELDS:
            if optional_field not in plan.model_fields_set:
                values.pop(optional_field, None)
        encoded = json.dumps(values, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()

    def save_plan(self, plan: FrozenPlan) -> FrozenPlan:
        if not plan.plan_id:
            plan.model_fields_set.update(self.COMPATIBILITY_FIELDS)
        excluded_fields = self.COMPATIBILITY_FIELDS - plan.model_fields_set
        plan.plan_id = self.calculate_plan_id(plan)
        self.plans.mkdir(parents=True, exist_ok=True)
        path = self.plans / f"{plan.plan_id}.json"
        path.write_text(
            plan.model_dump_json(indent=2, exclude=excluded_fields),
            encoding="utf-8",
        )
        return plan

    def _resolve_id(self, directory: Path, plan_id: str) -> Path:
        if len(plan_id) == 64:
            path = directory / f"{plan_id}.json"
            if path.exists():
                return path
        matches = sorted(directory.glob(f"{plan_id}*.json")) if directory.exists() else []
        if len(matches) != 1:
            raise AutomatorError(
                f"plan ID prefix must match exactly one saved item; found {len(matches)}"
            )
        return matches[0]

    def load_plan(self, plan_id: str) -> FrozenPlan:
        path = self._resolve_id(self.plans, plan_id)
        try:
            plan = FrozenPlan.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise AutomatorError(f"could not load frozen plan: {exc}") from exc
        if plan.plan_id != self.calculate_plan_id(plan):
            raise AutomatorError("frozen plan hash is invalid")
        return plan

    def save_run(self, state: RunState) -> None:
        state.updated_at = datetime.now(UTC)
        self.runs.mkdir(parents=True, exist_ok=True)
        path = self.runs / f"{state.plan_id}.json"
        temporary = path.with_suffix(".tmp")
        temporary.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        temporary.replace(path)

    def load_run(self, plan_id: str) -> RunState:
        path = self._resolve_id(self.runs, plan_id)
        try:
            return RunState.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise AutomatorError(f"could not load run state: {exc}") from exc

    def import_run(self, path: Path, plan_id: str) -> RunState:
        try:
            state = RunState.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise AutomatorError(f"could not import run state: {exc}") from exc
        if state.plan_id != plan_id:
            raise AutomatorError("run state does not match the frozen plan")
        self.save_run(state)
        return state

    def has_run(self, plan_id: str) -> bool:
        return (self.runs / f"{plan_id}.json").exists()
