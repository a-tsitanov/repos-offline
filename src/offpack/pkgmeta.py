"""Метаданные файлов пакетов: имя и версия."""

from __future__ import annotations

import json
import re
import tarfile
from dataclasses import dataclass
from pathlib import Path

from offpack.commands import Ecosystem
from offpack.errors import OffpackError

_SDIST_EXTS = (".tar.gz", ".zip", ".tar.bz2")
_PACKAGE_JSON = re.compile(r"^[^/]+/package\.json$")


class PackageMetaError(OffpackError):
    """Не удалось прочитать метаданные пакета."""


@dataclass(frozen=True)
class PackageFile:
    ecosystem: Ecosystem
    name: str
    version: str
    source: Path
    bundle_name: str

    @property
    def bundle_path(self) -> str:
        return f"{self.ecosystem}/{self.bundle_name}"


def normalize_pypi_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def read_npm_tarball(path: Path) -> tuple[str, str]:
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = [
                m
                for m in archive.getmembers()
                if m.isfile() and _PACKAGE_JSON.match(m.name.removeprefix("./"))
            ]
            members.sort(key=lambda m: m.name.removeprefix("./") != "package/package.json")
            if not members:
                raise PackageMetaError(f"{path.name}: нет package.json в корне пакета")
            handle = archive.extractfile(members[0])
            if handle is None:
                raise PackageMetaError(f"{path.name}: package.json не читается")
            data = json.load(handle)
    except (tarfile.TarError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PackageMetaError(f"{path.name}: не удалось прочитать ({exc})") from exc
    if not isinstance(data, dict):
        raise PackageMetaError(f"{path.name}: package.json не объект")
    name, version = data.get("name"), data.get("version")
    if not isinstance(name, str) or not isinstance(version, str):
        raise PackageMetaError(f"{path.name}: в package.json нет name или version")
    return name, version


def npm_bundle_name(name: str, version: str) -> str:
    return f"{name.removeprefix('@').replace('/', '__')}-{version}.tgz"


def parse_pypi_filename(filename: str) -> tuple[str, str] | None:
    if filename.endswith(".whl"):
        parts = filename[: -len(".whl")].split("-")
        if len(parts) not in (5, 6):
            return None
        return normalize_pypi_name(parts[0]), parts[1]
    for ext in _SDIST_EXTS:
        if filename.endswith(ext):
            name, sep, version = filename[: -len(ext)].rpartition("-")
            if not sep or not name or not version or not version[0].isdigit():
                return None
            return normalize_pypi_name(name), version
    return None


def wheel_platform_tag(filename: str) -> str | None:
    if not filename.endswith(".whl"):
        return None
    parts = filename[: -len(".whl")].split("-")
    return parts[-1] if len(parts) in (5, 6) else None
