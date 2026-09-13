"""Целевые платформы и их флаги для npm и uv."""

from __future__ import annotations

import re
from dataclasses import dataclass

from offpack.errors import OffpackError

DEFAULT_PLATFORMS = "linux-x64,win-x64"
DEFAULT_PYTHONS = "3.12"
_PYTHON_VERSION = re.compile(r"3\.\d{1,2}")


@dataclass(frozen=True)
class Platform:
    name: str
    npm_os: str
    npm_cpu: str
    uv_platform: str
    wheel_prefix: str
    wheel_suffix: str

    def wheel_matches(self, platform_tag: str) -> bool:
        """Подходит ли wheel с таким platform tag (составной тег — через точку)."""
        return any(
            tag == "any" or (tag.startswith(self.wheel_prefix) and tag.endswith(self.wheel_suffix))
            for tag in platform_tag.split(".")
        )


PLATFORMS: dict[str, Platform] = {
    "linux-x64": Platform(
        "linux-x64", "linux", "x64", "x86_64-manylinux_2_28", "manylinux", "_x86_64"
    ),
    "win-x64": Platform(
        "win-x64", "win32", "x64", "x86_64-pc-windows-msvc", "win_amd64", "win_amd64"
    ),
}


def parse_platforms(value: str) -> tuple[Platform, ...]:
    names = _split(value)
    if not names:
        raise OffpackError("не указана ни одна платформа")
    unknown = [name for name in names if name not in PLATFORMS]
    if unknown:
        raise OffpackError(
            f"неизвестная платформа: {', '.join(unknown)}; доступны: {', '.join(PLATFORMS)}"
        )
    return tuple(PLATFORMS[name] for name in names)


def parse_python_versions(value: str) -> tuple[str, ...]:
    versions = _split(value)
    if not versions:
        raise OffpackError("не указана ни одна версия Python")
    bad = [v for v in versions if not _PYTHON_VERSION.fullmatch(v)]
    if bad:
        raise OffpackError(f"неверная версия Python: {', '.join(bad)}; формат 3.X")
    return tuple(versions)


def _split(value: str) -> list[str]:
    items: list[str] = []
    for item in value.split(","):
        item = item.strip()
        if item and item not in items:
            items.append(item)
    return items
