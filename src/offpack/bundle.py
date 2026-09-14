"""Формат архива: манифест, отчёт, контрольные суммы, упаковка и распаковка."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import tarfile
import zlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from offpack.commands import InstallPlan
from offpack.errors import OffpackError
from offpack.pkgmeta import PackageFile

SCHEMA = 1
MANIFEST = "manifest.json"
REPORT = "report.txt"
SUMS = "SHA256SUMS"
SIGNATURE = "SHA256SUMS.sig"
_SUM_LINE = re.compile(r"^([0-9a-f]{64})  (.+)$")
_ECOSYSTEMS = ("npm", "pypi")


class BundleError(OffpackError):
    """Архив повреждён или не соответствует формату."""


@dataclass(frozen=True)
class Notice:
    kind: str
    detail: str


@dataclass(frozen=True)
class FileEntry:
    ecosystem: str
    name: str
    version: str
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Manifest:
    created_at: str
    command: list[str]
    platforms: list[str]
    python_versions: list[str]
    files: list[FileEntry]
    warnings: list[Notice]
    schema: int = SCHEMA

    def to_json(self) -> str:
        data = {
            "schema": self.schema,
            "created_at": self.created_at,
            "command": self.command,
            "platforms": self.platforms,
            "python_versions": self.python_versions,
            "files": [vars(entry) for entry in self.files],
            "warnings": [vars(notice) for notice in self.warnings],
        }
        return json.dumps(data, ensure_ascii=False, indent=2) + "\n"

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        try:
            data = json.loads(text)
            if data.get("schema") != SCHEMA:
                raise BundleError(
                    f"неподдерживаемая версия манифеста: {data.get('schema')!r}"
                )
            files = [FileEntry(**item) for item in data["files"]]
            manifest = cls(
                created_at=data["created_at"],
                command=list(data["command"]),
                platforms=list(data["platforms"]),
                python_versions=list(data["python_versions"]),
                files=files,
                warnings=[Notice(**item) for item in data["warnings"]],
            )
        except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
            raise BundleError(f"повреждён {MANIFEST}: {exc}") from exc
        for entry in manifest.files:
            if entry.ecosystem not in _ECOSYSTEMS:
                raise BundleError(
                    f"{MANIFEST}: неизвестная экосистема {entry.ecosystem!r}"
                )
        return manifest


@dataclass(frozen=True)
class BundleMeta:
    created_at: datetime
    command: Sequence[str]
    platforms: Sequence[str]
    python_versions: Sequence[str]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bundle_label(plan: InstallPlan) -> str:
    if plan.specs:
        spec = plan.specs[0]
        if plan.ecosystem == "npm":
            bare = (
                spec.split("/", 1)[1]
                if spec.startswith("@") and "/" in spec
                else spec
            )
            label = bare.split("@", 1)[0]
        else:
            match = re.match(r"[A-Za-z0-9._-]*", spec)
            label = match.group(0) if match else ""
    else:
        label = PurePosixPath(plan.raw[0].replace("\\", "/")).name
    label = re.sub(r"[^A-Za-z0-9._-]+", "-", label).strip("-.")
    return label or "bundle"


def render_report(manifest: Manifest) -> str:
    lines = [
        f"offpack: архив создан {manifest.created_at}",
        f"команда: {shlex.join(manifest.command)}",
        f"платформы: {', '.join(manifest.platforms)}; Python:"
        f" {', '.join(manifest.python_versions)}",
        "",
    ]
    for ecosystem in _ECOSYSTEMS:
        entries = sorted(
            (f for f in manifest.files if f.ecosystem == ecosystem),
            key=lambda f: (f.name, f.version, f.path),
        )
        if not entries:
            continue
        size_mb = sum(f.size for f in entries) / 1_048_576
        lines.append(f"{ecosystem}: {len(entries)} файлов, {size_mb:.1f} МБ")
        lines.extend(
            f"  {f.name}=={f.version}  {PurePosixPath(f.path).name}"
            for f in entries
        )
        lines.append("")
    if manifest.warnings:
        lines.append("ПРЕДУПРЕЖДЕНИЯ:")
        lines.extend(f"  [{w.kind}] {w.detail}" for w in manifest.warnings)
    else:
        lines.append("Предупреждений нет.")
    return "\n".join(lines) + "\n"


def stage_bundle(
    stage_root: Path,
    basename: str,
    files: Sequence[PackageFile],
    meta: BundleMeta,
    warnings: Sequence[Notice],
) -> Path:
    bundle_dir = stage_root / basename
    if bundle_dir.exists():
        raise BundleError(f"каталог уже существует: {bundle_dir}")
    bundle_dir.mkdir(parents=True)
    entries: dict[str, FileEntry] = {}
    for package in sorted(files, key=lambda f: f.bundle_path):
        digest = sha256_file(package.source)
        existing = entries.get(package.bundle_path)
        if existing is not None:
            if existing.sha256 != digest:
                raise BundleError(
                    f"разные файлы с одинаковым именем: {package.bundle_path}"
                )
            continue
        target = bundle_dir / package.bundle_path
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(package.source, target)
        entries[package.bundle_path] = FileEntry(
            package.ecosystem,
            package.name,
            package.version,
            package.bundle_path,
            digest,
            target.stat().st_size,
        )
    manifest = Manifest(
        created_at=meta.created_at.isoformat(timespec="seconds").replace("+00:00", "Z"),
        command=list(meta.command),
        platforms=list(meta.platforms),
        python_versions=list(meta.python_versions),
        files=list(entries.values()),
        warnings=list(warnings),
    )
    (bundle_dir / MANIFEST).write_text(manifest.to_json(), encoding="utf-8")
    (bundle_dir / REPORT).write_text(render_report(manifest), encoding="utf-8")
    write_checksums(bundle_dir)
    return bundle_dir


def _content_files(bundle_dir: Path) -> list[str]:
    relpaths = (
        p.relative_to(bundle_dir).as_posix()
        for p in bundle_dir.rglob("*")
        if p.is_file()
    )
    return sorted(rel for rel in relpaths if rel not in (SUMS, SIGNATURE))


def write_checksums(bundle_dir: Path) -> Path:
    path = bundle_dir / SUMS
    lines = [
        f"{sha256_file(bundle_dir / rel)}  {rel}\n"
        for rel in _content_files(bundle_dir)
    ]
    path.write_text("".join(lines), encoding="utf-8")
    return path


def _read_checksums(bundle_dir: Path) -> dict[str, str]:
    path = bundle_dir / SUMS
    if not path.is_file():
        raise BundleError(f"в архиве нет {SUMS}")
    sums: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        match = _SUM_LINE.match(line)
        if not match:
            raise BundleError(f"{SUMS}: строка {number} не в формате sha256sum")
        digest, rel = match.groups()
        pure = PurePosixPath(rel)
        if pure.is_absolute() or ".." in pure.parts:
            raise BundleError(f"{SUMS}: недопустимый путь {rel}")
        sums[rel] = digest
    return sums


def verify_checksums(bundle_dir: Path) -> dict[str, str]:
    sums = _read_checksums(bundle_dir)
    actual = set(_content_files(bundle_dir))
    missing = sorted(set(sums) - actual)
    if missing:
        raise BundleError(f"нет файлов из {SUMS}: {', '.join(missing)}")
    extra = sorted(actual - set(sums))
    if extra:
        raise BundleError(f"файлы вне {SUMS}: {', '.join(extra)}")
    for rel, digest in sums.items():
        if sha256_file(bundle_dir / rel) != digest:
            raise BundleError(f"контрольная сумма не совпала: {rel}")
    return sums


def load_manifest(bundle_dir: Path, sums: dict[str, str]) -> Manifest:
    path = bundle_dir / MANIFEST
    if not path.is_file():
        raise BundleError(f"в архиве нет {MANIFEST}")
    manifest = Manifest.from_json(path.read_text(encoding="utf-8"))
    expected = {entry.path for entry in manifest.files} | {MANIFEST, REPORT}
    if expected != set(sums):
        raise BundleError(f"список файлов в {MANIFEST} не совпадает с {SUMS}")
    for entry in manifest.files:
        if sums[entry.path] != entry.sha256:
            raise BundleError(f"sha256 в {MANIFEST} не совпадает с {SUMS}: {entry.path}")
    return manifest


def pack_bundle(bundle_dir: Path, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    archive = out_dir / f"{bundle_dir.name}.tar.gz"
    if archive.exists():
        raise BundleError(f"файл уже существует: {archive}")
    # пишем во временный файл: прерванная упаковка не оставит битый архив под итоговым именем
    part = archive.with_name(f"{archive.name}.part")
    try:
        with tarfile.open(part, "w:gz") as tar:
            tar.add(bundle_dir, arcname=bundle_dir.name)
        os.replace(part, archive)
    except BaseException:
        part.unlink(missing_ok=True)
        raise
    return archive


def extract_bundle(archive: Path, dest: Path) -> Path:
    """Распаковать с проверками: только файлы и каталоги, без .. и абсолютных путей."""
    roots: set[str] = set()
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = tar.getmembers()
            for member in members:
                if not (member.isfile() or member.isdir()):
                    raise BundleError(f"недопустимый элемент архива: {member.name}")
                pure = PurePosixPath(member.name)
                if not pure.parts or pure.is_absolute() or ".." in pure.parts:
                    raise BundleError(f"недопустимый путь в архиве: {member.name}")
                roots.add(pure.parts[0])
            if len(roots) != 1:
                raise BundleError("в архиве должен быть ровно один корневой каталог")
            tar.extractall(dest, members=members, filter="data")
    except (tarfile.TarError, OSError, EOFError, zlib.error) as exc:
        # EOFError и zlib.error — обрезанный или повреждённый gzip
        raise BundleError(f"не удалось распаковать {archive.name}: {exc}") from exc
    root = dest / roots.pop()
    if not root.is_dir():
        raise BundleError("корень архива — не каталог")
    return root
