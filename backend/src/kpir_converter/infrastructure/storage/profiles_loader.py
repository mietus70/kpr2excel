"""Load and validate YAML profiles from disk (no code execution, safe_load only)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from ...domain.profiles import KpirProfile, ProfileError, ProfileRegistry, profile_from_dict

__all__ = ["load_profile_file", "load_registry", "get_registry", "reset_registry_cache"]


def load_profile_file(path: Path) -> KpirProfile:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(f"{path.name}: invalid YAML") from exc
    if raw is None:
        raise ProfileError(f"{path.name}: empty profile document")
    return profile_from_dict(raw)


def load_registry(directory: Path) -> ProfileRegistry:
    registry = ProfileRegistry()
    if not directory.exists():
        raise ProfileError(f"profiles directory not found: {directory}")
    files = sorted([*directory.glob("*.yaml"), *directory.glob("*.yml"), *directory.glob("*.json")])
    if not files:
        raise ProfileError(f"no profile files found in {directory}")
    for file in files:
        registry.add(load_profile_file(file))
    return registry


@lru_cache(maxsize=4)
def _cached_registry(directory: str) -> ProfileRegistry:
    return load_registry(Path(directory))


def get_registry(directory: Path) -> ProfileRegistry:
    return _cached_registry(str(directory))


def reset_registry_cache() -> None:
    _cached_registry.cache_clear()
