from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering

from release_automator.errors import AutomatorError
from release_automator.models import ReleaseChannel

SEMVER_PATTERN = re.compile(
    r"^(?P<prefix>[^0-9]*)"
    r"(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)


@total_ordering
@dataclass(frozen=True)
class SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...] = ()

    def _compare_prerelease(self, other: SemVer) -> int:
        if not self.prerelease and not other.prerelease:
            return 0
        if not self.prerelease:
            return 1
        if not other.prerelease:
            return -1
        for left, right in zip(self.prerelease, other.prerelease, strict=False):
            if left == right:
                continue
            left_numeric = left.isdigit()
            right_numeric = right.isdigit()
            if left_numeric and right_numeric:
                return -1 if int(left) < int(right) else 1
            if left_numeric != right_numeric:
                return -1 if left_numeric else 1
            left_legacy = re.fullmatch(r"([A-Za-z-]+)([0-9]+)", left)
            right_legacy = re.fullmatch(r"([A-Za-z-]+)([0-9]+)", right)
            if left_legacy and right_legacy and left_legacy.group(1) == right_legacy.group(1):
                return -1 if int(left_legacy.group(2)) < int(right_legacy.group(2)) else 1
            return -1 if left < right else 1
        if len(self.prerelease) == len(other.prerelease):
            return 0
        return -1 if len(self.prerelease) < len(other.prerelease) else 1

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return NotImplemented
        core = (self.major, self.minor, self.patch)
        other_core = (other.major, other.minor, other.patch)
        if core != other_core:
            return core < other_core
        return self._compare_prerelease(other) < 0

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SemVer):
            return False
        return (
            self.major,
            self.minor,
            self.patch,
            self.prerelease,
        ) == (
            other.major,
            other.minor,
            other.patch,
            other.prerelease,
        )


def parse_semver(tag: str, tag_prefix: str = "v") -> SemVer:
    match = SEMVER_PATTERN.fullmatch(tag)
    if not match or match.group("prefix") != tag_prefix:
        raise ValueError(f"not a strict {tag_prefix!r}-prefixed semantic version: {tag}")
    prerelease = tuple((match.group("prerelease") or "").split("."))
    if prerelease == ("",):
        prerelease = ()
    return SemVer(
        major=int(match.group("major")),
        minor=int(match.group("minor")),
        patch=int(match.group("patch")),
        prerelease=prerelease,
    )


def validate_suggested_version(
    proposed: str,
    channel: ReleaseChannel,
    existing_tags: list[str],
    tag_prefix: str,
) -> list[str]:
    if proposed in existing_tags:
        raise AutomatorError(f"release tag already exists: {proposed}")
    try:
        candidate = parse_semver(proposed, tag_prefix)
    except ValueError as exc:
        raise AutomatorError(str(exc)) from exc
    if channel is ReleaseChannel.STABLE and candidate.prerelease:
        raise AutomatorError("stable release proposal contains a prerelease suffix")
    if channel is ReleaseChannel.PRERELEASE and not candidate.prerelease:
        raise AutomatorError("prerelease proposal does not contain a prerelease suffix")

    parsed: list[tuple[SemVer, str]] = []
    warnings: list[str] = []
    for tag in existing_tags:
        try:
            parsed.append((parse_semver(tag, tag_prefix), tag))
        except ValueError:
            warnings.append(f"ignored non-SemVer release tag: {tag}")
    if parsed:
        latest, latest_tag = max(parsed, key=lambda item: item[0])
        if candidate <= latest:
            raise AutomatorError(f"proposed version {proposed} must be newer than {latest_tag}")
    return warnings
