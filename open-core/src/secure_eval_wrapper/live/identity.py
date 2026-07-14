"""Canonical public account and executing-repository identity derivation."""
from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from secure_eval_wrapper.data_collection.hashing import sha256_payload


_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_ACCOUNT_FINGERPRINT = re.compile(r"^[0-9a-f]{16}$")
BUILD_METADATA_SCHEMA_VERSION = 1
REPOSITORY_IDENTITY_RESOLVER_VERSION = "repository-identity-v1"
BUILD_METADATA_FILENAME = "_build_repository_identity.json"


class RepositoryIdentityError(PermissionError):
    """Raised when the executing source/build commit cannot be proven exactly."""


def derive_okx_account_fingerprint(uid: str) -> str:
    """Return the first 16 hex characters of the canonical OKX UID identity hash.

    The exact, non-normalized UID returned by ``GET /api/v5/account/config`` is encoded
    through canonical UTF-8 JSON as ``{"provider":"okx","account_uid":uid}``, hashed
    with SHA-256, and truncated to 16 lowercase hexadecimal characters.
    """

    if not isinstance(uid, str) or not uid or uid != uid.strip():
        raise ValueError("OKX account uid must be an exact non-empty string")
    return sha256_payload({"provider": "okx", "account_uid": uid})[:16]


def validate_okx_account_fingerprint(value: str, *, field_name: str = "account_fingerprint") -> str:
    if not isinstance(value, str) or _ACCOUNT_FINGERPRINT.fullmatch(value) is None:
        raise ValueError(f"{field_name} must be a 16-character lowercase OKX account fingerprint")
    if value == "0000000000000000":
        raise ValueError(f"{field_name} cannot use the retired universal placeholder")
    return value


def validate_git_commit_sha(value: str, *, field_name: str = "commit_sha") -> str:
    if not isinstance(value, str) or _GIT_SHA.fullmatch(value) is None:
        raise RepositoryIdentityError(f"{field_name} must be an exact 40-character lowercase Git SHA")
    return value


def build_repository_metadata_payload(commit_sha: str) -> dict[str, object]:
    """Build self-authenticating metadata for inclusion in a validated package/build."""

    core = {
        "schema_version": BUILD_METADATA_SCHEMA_VERSION,
        "commit_sha": validate_git_commit_sha(commit_sha),
        "generated_from": "git_checkout",
        "resolver_version": REPOSITORY_IDENTITY_RESOLVER_VERSION,
    }
    return {**core, "payload_sha256": sha256_payload(core)}


@dataclass(frozen=True)
class RuntimeRepositoryIdentity:
    observed_commit_sha: str
    identity_source: str
    resolver_version: str = REPOSITORY_IDENTITY_RESOLVER_VERSION

    def __post_init__(self) -> None:
        validate_git_commit_sha(self.observed_commit_sha, field_name="observed_commit_sha")
        if self.identity_source not in {"git_checkout", "build_metadata", "verified_ci"}:
            raise RepositoryIdentityError("repository identity source is not approved")
        if self.resolver_version != REPOSITORY_IDENTITY_RESOLVER_VERSION:
            raise RepositoryIdentityError("repository identity resolver version mismatch")


def _read_build_metadata(path: Path) -> str | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RepositoryIdentityError("repository build metadata is unreadable or invalid") from exc
    if not isinstance(payload, dict):
        raise RepositoryIdentityError("repository build metadata must be an object")
    core = {
        "schema_version": payload.get("schema_version"),
        "commit_sha": payload.get("commit_sha"),
        "generated_from": payload.get("generated_from"),
        "resolver_version": payload.get("resolver_version"),
    }
    if (
        core["schema_version"] != BUILD_METADATA_SCHEMA_VERSION
        or core["generated_from"] != "git_checkout"
        or core["resolver_version"] != REPOSITORY_IDENTITY_RESOLVER_VERSION
        or payload.get("payload_sha256") != sha256_payload(core)
    ):
        raise RepositoryIdentityError("repository build metadata failed integrity validation")
    return validate_git_commit_sha(core["commit_sha"], field_name="build metadata commit_sha")


def _git_checkout_head(source_root: Path) -> str | None:
    try:
        top_level = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "--show-toplevel"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    try:
        if Path(top_level).resolve() != source_root.resolve():
            raise RepositoryIdentityError("executing source is nested in an unexpected Git checkout")
        head = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "--verify", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RepositoryIdentityError("Git checkout HEAD could not be resolved") from exc
    return validate_git_commit_sha(head, field_name="Git checkout HEAD")

def collect_build_repository_metadata(*, source_root: Path | str | None = None) -> dict[str, object]:
    """Collect immutable package/build metadata from the checked-out Git HEAD only."""

    root = Path(source_root).resolve() if source_root is not None else Path(__file__).resolve().parents[4]
    git_sha = _git_checkout_head(root)
    if git_sha is None:
        raise RepositoryIdentityError("build metadata generation requires a verified Git checkout")
    return build_repository_metadata_payload(git_sha)



def _ci_sha(environment: Mapping[str, str]) -> str | None:
    present = {
        name: value
        for name in ("GITHUB_SHA", "CI_COMMIT_SHA")
        if (value := environment.get(name))
    }
    if not present:
        return None
    validated = {
        name: validate_git_commit_sha(value, field_name=name)
        for name, value in present.items()
    }
    if len(set(validated.values())) != 1:
        raise RepositoryIdentityError("CI commit identity variables disagree")
    return next(iter(validated.values()))


def resolve_runtime_repository_identity(
    *,
    source_root: Path | str | None = None,
    build_metadata_path: Path | str | None = None,
    environment: Mapping[str, str] | None = None,
) -> RuntimeRepositoryIdentity:
    """Resolve the actual executing source/build SHA and reject all disagreements.

    A validated build metadata file has priority.  When a source checkout is present,
    its exact top-level ``git rev-parse --verify HEAD`` must agree with that metadata.
    CI-provided SHAs are comparison evidence only and are rejected unless they exactly
    match the checked-out HEAD.  CI values alone can never establish authority.
    """

    root = Path(source_root).resolve() if source_root is not None else Path(__file__).resolve().parents[4]
    metadata_path = (
        Path(build_metadata_path).resolve()
        if build_metadata_path is not None
        else Path(__file__).with_name(BUILD_METADATA_FILENAME)
    )
    env = os.environ if environment is None else environment
    metadata_sha = _read_build_metadata(metadata_path)
    git_sha = _git_checkout_head(root)
    ci_sha = _ci_sha(env)

    if metadata_sha is not None and git_sha is not None and metadata_sha != git_sha:
        raise RepositoryIdentityError("build metadata and checked-out Git HEAD disagree")
    if ci_sha is not None:
        if git_sha is None:
            raise RepositoryIdentityError("CI commit SHA lacks an independently verified checked-out HEAD")
        if ci_sha != git_sha:
            raise RepositoryIdentityError("CI commit SHA does not match the checked-out HEAD")

    if metadata_sha is not None:
        return RuntimeRepositoryIdentity(metadata_sha, "build_metadata")
    if ci_sha is not None:
        return RuntimeRepositoryIdentity(ci_sha, "verified_ci")
    if git_sha is not None:
        return RuntimeRepositoryIdentity(git_sha, "git_checkout")
    raise RepositoryIdentityError("repository identity requires build metadata or a verified Git checkout")


__all__ = [
    "BUILD_METADATA_FILENAME",
    "REPOSITORY_IDENTITY_RESOLVER_VERSION",
    "RepositoryIdentityError",
    "collect_build_repository_metadata",
    "RuntimeRepositoryIdentity",
    "build_repository_metadata_payload",
    "derive_okx_account_fingerprint",
    "resolve_runtime_repository_identity",
    "validate_git_commit_sha",
    "validate_okx_account_fingerprint",
]
