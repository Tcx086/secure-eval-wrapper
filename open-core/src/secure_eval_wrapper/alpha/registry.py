"""Deterministic in-memory registry for transparent public alpha implementations."""

from __future__ import annotations

import re

from secure_eval_wrapper.alpha.examples import PUBLIC_ALPHA_TYPES
from secure_eval_wrapper.alpha.interfaces import PublicAlpha
from secure_eval_wrapper.alpha.models import AlphaDefinition


class AlphaRegistryError(ValueError):
    pass


def _version_key(version: str):
    parts = re.split(r"[.-]", version)
    return tuple((0, int(part)) if part.isdigit() else (1, part) for part in parts)


class PublicAlphaRegistry:
    def __init__(self) -> None:
        self._implementations: dict[tuple[str, str], PublicAlpha] = {}
        self._ids: dict[object, tuple[str, str]] = {}

    def register(self, implementation: PublicAlpha) -> None:
        definition = implementation.definition
        if not isinstance(definition, AlphaDefinition):
            raise TypeError("public alpha implementations require an AlphaDefinition")
        key = (definition.name, definition.version)
        existing = self._implementations.get(key)
        if existing is not None:
            if existing.definition.implementation_code_sha256 != definition.implementation_code_sha256:
                raise AlphaRegistryError("implementation hash conflict for alpha name/version")
            raise AlphaRegistryError("duplicate alpha name/version registration")
        existing_key = self._ids.get(definition.alpha_id)
        if existing_key is not None and existing_key != key:
            raise AlphaRegistryError("alpha_id is already registered to another name/version")
        self._implementations[key] = implementation
        self._ids[definition.alpha_id] = key

    def resolve(self, name: str, version: str | None = None) -> PublicAlpha:
        if version is not None:
            try:
                return self._implementations[(name, version)]
            except KeyError as exc:
                raise AlphaRegistryError(f"unknown alpha {name!r} version {version!r}") from exc
        matches = [item for (candidate, _), item in self._implementations.items() if candidate == name]
        if not matches:
            raise AlphaRegistryError(f"unknown alpha {name!r}")
        return max(matches, key=lambda item: _version_key(item.definition.version))

    def definitions(self) -> tuple[AlphaDefinition, ...]:
        return tuple(
            item.definition
            for _, item in sorted(
                self._implementations.items(),
                key=lambda pair: (pair[0][0], _version_key(pair[0][1])),
            )
        )

    def categories(self) -> tuple[str, ...]:
        return tuple(sorted({item.category for item in self.definitions()}))

    def required_data_types(self) -> tuple[str, ...]:
        return tuple(sorted({data_type for item in self.definitions() for data_type in item.required_data_types}))


def build_public_alpha_registry() -> PublicAlphaRegistry:
    registry = PublicAlphaRegistry()
    for implementation_type in PUBLIC_ALPHA_TYPES:
        registry.register(implementation_type())
    return registry


__all__ = ["AlphaRegistryError", "PublicAlphaRegistry", "build_public_alpha_registry"]
