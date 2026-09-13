"""Сравнение semver-версий npm."""

from __future__ import annotations

import re
from collections.abc import Iterable

_SEMVER = re.compile(r"^(\d+)\.(\d+)\.(\d+)(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$")


def stable_key(version: str) -> tuple[int, int, int] | None:
    """Ключ сортировки стабильной версии; None для prerelease и мусора."""
    match = _SEMVER.match(version)
    if not match or match.group(4):
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def max_stable(versions: Iterable[str]) -> str | None:
    best: str | None = None
    best_key: tuple[int, int, int] | None = None
    for version in versions:
        key = stable_key(version)
        if key is not None and (best_key is None or key > best_key):
            best, best_key = version, key
    return best


def latest_needs_fix(
    current: str | None, versions: Iterable[str]
) -> str | None:
    """Версия для dist-tag latest или None, если менять не нужно."""
    target = max_stable(versions)
    if target is None or current == target:
        return None
    target_key = stable_key(target)
    current_key = stable_key(current) if current else None
    if current_key is not None and target_key is not None \
            and current_key >= target_key:
        return None
    return target
