"""Импорт архива в офлайн-Nexus."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from offpack.bundle import (
    SIGNATURE,
    SUMS,
    BundleError,
    extract_bundle,
    load_manifest,
    verify_checksums,
)
from offpack.nexus import NexusAuthError, NexusClient, NexusError
from offpack.signing import verify_file
from offpack.versions import latest_needs_fix


@dataclass
class ImportReport:
    uploaded: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failed

    def render(self, *, dry_run: bool) -> str:
        verb = "будет загружено" if dry_run else "загружено"
        lines = [f"{verb}: {len(self.uploaded)}"]
        lines += [f"  + {item}" for item in self.uploaded]
        lines.append(f"уже в Nexus: {len(self.skipped)}")
        lines += [f"  = {item}" for item in self.skipped]
        lines.append(f"ошибки: {len(self.failed)}")
        lines += [f"  ! {item}: {error}" for item, error in self.failed]
        lines += [f"внимание: {notice}" for notice in self.notices]
        return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class ImportOptions:
    archive: Path
    npm_repo: str = "npm-hosted"
    pypi_repo: str = "pypi-hosted"
    allowed_signers: Path | None = None
    allow_unsigned: bool = False
    dry_run: bool = False


class _Existing:
    """Кэш того, что уже лежит в Nexus: версии npm и имена файлов PyPI."""

    def __init__(self, client: NexusClient, npm_repo: str, pypi_repo: str) -> None:
        self._client = client
        self._npm_repo = npm_repo
        self._pypi_repo = pypi_repo
        self._npm: dict[str, set[str]] = {}
        self._pypi: dict[str, set[str]] = {}

    def contains(self, ecosystem: str, name: str, version: str, filename: str) -> bool:
        if ecosystem == "npm":
            if name not in self._npm:
                pack = self._client.npm_packument(self._npm_repo, name) or {}
                self._npm[name] = set(pack.get("versions", {}))
            return version in self._npm[name]
        if name not in self._pypi:
            self._pypi[name] = self._client.pypi_filenames(self._pypi_repo, name)
        return filename in self._pypi[name]


def run_import(opts: ImportOptions, client: NexusClient) -> ImportReport:
    report = ImportReport()
    with tempfile.TemporaryDirectory(prefix="offpack-import-") as tmp:
        bundle_dir = extract_bundle(opts.archive, Path(tmp))
        _verify_signature(bundle_dir, opts, report)
        manifest = load_manifest(bundle_dir, verify_checksums(bundle_dir))
        client.check()
        repos = {"npm": opts.npm_repo, "pypi": opts.pypi_repo}
        existing = _Existing(client, opts.npm_repo, opts.pypi_repo)
        uploaded_npm: set[str] = set()
        for entry in manifest.files:
            filename = PurePosixPath(entry.path).name
            label = f"{entry.ecosystem} {entry.name}=={entry.version} ({filename})"
            try:
                if existing.contains(entry.ecosystem, entry.name, entry.version, filename):
                    report.skipped.append(label)
                    continue
                if opts.dry_run:
                    report.uploaded.append(label)
                    continue
                created = client.upload(
                    repos[entry.ecosystem], entry.ecosystem, bundle_dir / entry.path
                )
            except NexusAuthError:
                raise
            except NexusError as exc:
                report.failed.append((label, str(exc)))
                continue
            (report.uploaded if created else report.skipped).append(label)
            if created and entry.ecosystem == "npm":
                uploaded_npm.add(entry.name)
        for name in sorted(uploaded_npm):
            _fix_latest(client, opts.npm_repo, name, report)
    return report


def _verify_signature(bundle_dir: Path, opts: ImportOptions, report: ImportReport) -> None:
    if not (bundle_dir / SUMS).is_file():
        raise BundleError(f"в архиве нет {SUMS}")
    if opts.allow_unsigned:
        report.notices.append("подпись не проверялась (--allow-unsigned)")
        return
    if opts.allowed_signers is None:
        raise BundleError("укажите --allowed-signers или явно --allow-unsigned")
    verify_file(bundle_dir / SUMS, bundle_dir / SIGNATURE, opts.allowed_signers)


def _fix_latest(client: NexusClient, repo: str, name: str, report: ImportReport) -> None:
    try:
        pack = client.npm_packument(repo, name)
        if pack is None:
            return
        target = latest_needs_fix(pack.get("dist-tags", {}).get("latest"), pack.get("versions", {}))
        if target is None:
            return
        client.set_npm_latest(repo, name, target)
        report.notices.append(f"{name}: dist-tag latest → {target}")
    except NexusError as exc:
        report.notices.append(f"{name}: не удалось выставить dist-tag latest: {exc}")
