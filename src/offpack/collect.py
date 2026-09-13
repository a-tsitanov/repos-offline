"""Сбор файлов пакетов из хранилищ прокси и разбор лога squid."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import urlsplit

from offpack.bundle import Notice
from offpack.pkgmeta import (
    PackageFile,
    npm_bundle_name,
    parse_pypi_filename,
    read_npm_tarball,
    wheel_platform_tag,
)
from offpack.platforms import Platform

PROXY_HOSTS = frozenset({"verdaccio", "devpi"})


def collect_npm(storage: Path) -> list[PackageFile]:
    """Все tarball из storage Verdaccio; имя и версия — из package.json внутри."""
    if not storage.is_dir():
        return []
    files = []
    for path in sorted(storage.rglob("*.tgz")):
        name, version = read_npm_tarball(path)
        files.append(PackageFile("npm", name, version, path, npm_bundle_name(name, version)))
    return files


def collect_pypi(serverdir: Path) -> list[PackageFile]:
    """Все wheel и sdist из serverdir devpi; служебные файлы игнорируются."""
    if not serverdir.is_dir():
        return []
    files = []
    for path in sorted(p for p in serverdir.rglob("*") if p.is_file()):
        parsed = parse_pypi_filename(path.name)
        if parsed is None:
            continue
        name, version = parsed
        files.append(PackageFile("pypi", name, version, path, path.name))
    return files


def parse_egress_log(text: str) -> list[str]:
    """Хосты из access.log squid (logformat squid): поле 6 — метод, поле 7 — адрес."""
    hosts: set[str] = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 7:
            continue
        method, target = parts[5], parts[6]
        if method == "CONNECT":
            host = target.rsplit(":", 1)[0]
        else:
            host = urlsplit(target).hostname or ""
        host = host.strip("[]").lower()
        if host and host not in PROXY_HOSTS:
            hosts.add(host)
    return sorted(hosts)


def sdist_only_notices(
    files: Sequence[PackageFile], platforms: Sequence[Platform]
) -> list[Notice]:
    """Пакеты с sdist, для которых под какую-то платформу нет подходящего wheel."""
    groups: dict[tuple[str, str], list[PackageFile]] = defaultdict(list)
    for file in files:
        if file.ecosystem == "pypi":
            groups[(file.name, file.version)].append(file)
    notices = []
    for (name, version), group in sorted(groups.items()):
        if all(f.bundle_name.endswith(".whl") for f in group):
            continue
        tags = [tag for f in group if (tag := wheel_platform_tag(f.bundle_name))]
        for platform in platforms:
            if not any(platform.wheel_matches(tag) for tag in tags):
                notices.append(Notice("sdist_only", f"{name}=={version} ({platform.name})"))
    return notices
