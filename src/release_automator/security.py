from __future__ import annotations

import re
from pathlib import PurePosixPath

from release_automator.errors import AutomatorError

BLOCKED_BASENAMES = {
    ".env",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
}
BLOCKED_SUFFIXES = {".key", ".p12", ".pfx", ".pem"}
ENV_TEMPLATE_SUFFIXES = (".example", ".sample")
SECRET_PATTERNS = {
    "OpenAI API key": re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    "GitHub token": re.compile(r"\b(?:gh[opusr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private key": re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
}


def redact_secrets(text: str) -> tuple[str, list[str]]:
    redacted = text
    detected: list[str] = []
    for label, pattern in SECRET_PATTERNS.items():
        redacted, count = pattern.subn(f"<REDACTED {label.upper()}>", redacted)
        if count:
            detected.append(label)
    return redacted, detected


def assert_safe_paths(paths: list[str]) -> None:
    for path_text in paths:
        path = PurePosixPath(path_text)
        name = path.name.lower()
        if name in BLOCKED_BASENAMES:
            raise AutomatorError(f"refusing to send sensitive file to OpenAI: {path_text}")
        if name.startswith(".env.") and not name.endswith(ENV_TEMPLATE_SUFFIXES):
            raise AutomatorError(f"refusing to send sensitive file to OpenAI: {path_text}")
        if path.suffix.lower() in BLOCKED_SUFFIXES:
            raise AutomatorError(f"refusing to send key/certificate file to OpenAI: {path_text}")


def assert_no_secrets(text: str) -> None:
    for label, pattern in SECRET_PATTERNS.items():
        if pattern.search(text):
            raise AutomatorError(f"possible {label} detected; model request was blocked")


def assert_payload_size(payload: bytes, maximum: int) -> None:
    if len(payload) > maximum:
        raise AutomatorError(
            f"model payload is {len(payload)} bytes; maximum is {maximum}. Reduce --include scope."
        )
