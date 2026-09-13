import io
import json
import tarfile
from datetime import UTC, datetime

import pytest

from offpack.bundle import (
    MANIFEST,
    SUMS,
    BundleError,
    BundleMeta,
    Notice,
    bundle_label,
    extract_bundle,
    load_manifest,
    pack_bundle,
    stage_bundle,
    verify_checksums,
    write_checksums,
)
from offpack.commands import InstallPlan
from offpack.pkgmeta import PackageFile, npm_bundle_name
from tests.helpers import make_file, make_npm_tgz

META = BundleMeta(
    created_at=datetime(2026, 9, 13, 21, 40, tzinfo=UTC),
    command=["npx", "cowsay"],
    platforms=["linux-x64", "win-x64"],
    python_versions=["3.12"],
)


def _files(tmp_path):
    tgz = make_npm_tgz(tmp_path / "src", "@s/cowsay", "1.5.0")
    whl = make_file(tmp_path / "src", "six-1.16.0-py2.py3-none-any.whl", b"wheel")
    return [
        PackageFile("npm", "@s/cowsay", "1.5.0", tgz, npm_bundle_name("@s/cowsay", "1.5.0")),
        PackageFile("pypi", "six", "1.16.0", whl, whl.name),
    ]


def _stage(tmp_path, warnings=()):
    return stage_bundle(
        tmp_path / "stage", "cowsay-20260913-214000", _files(tmp_path), META, list(warnings)
    )


def test_stage_verify_load_roundtrip(tmp_path):
    bundle_dir = _stage(tmp_path, [Notice("egress", "github.com")])
    sums = verify_checksums(bundle_dir)
    manifest = load_manifest(bundle_dir, sums)
    assert manifest.created_at == "2026-09-13T21:40:00Z"
    assert [(f.ecosystem, f.name, f.path) for f in manifest.files] == [
        ("npm", "@s/cowsay", "npm/s__cowsay-1.5.0.tgz"),
        ("pypi", "six", "pypi/six-1.16.0-py2.py3-none-any.whl"),
    ]
    assert manifest.warnings == [Notice("egress", "github.com")]
    report = (bundle_dir / "report.txt").read_text(encoding="utf-8")
    assert "@s/cowsay==1.5.0" in report
    assert "[egress] github.com" in report
    assert set(sums) == {"manifest.json", "report.txt", "npm/s__cowsay-1.5.0.tgz",
                         "pypi/six-1.16.0-py2.py3-none-any.whl"}


def test_duplicates_same_content_are_merged(tmp_path):
    files = _files(tmp_path)
    copy = make_file(tmp_path / "other", files[1].bundle_name, b"wheel")
    files.append(PackageFile("pypi", "six", "1.16.0", copy, copy.name))
    bundle_dir = stage_bundle(tmp_path / "stage", "b", files, META, [])
    assert len(load_manifest(bundle_dir, verify_checksums(bundle_dir)).files) == 2


def test_duplicates_with_different_content_fail(tmp_path):
    files = _files(tmp_path)
    copy = make_file(tmp_path / "other", files[1].bundle_name, b"different")
    files.append(PackageFile("pypi", "six", "1.16.0", copy, copy.name))
    with pytest.raises(BundleError):
        stage_bundle(tmp_path / "stage", "b", files, META, [])


def test_tampered_file_fails(tmp_path):
    bundle_dir = _stage(tmp_path)
    (bundle_dir / "pypi" / "six-1.16.0-py2.py3-none-any.whl").write_bytes(b"evil")
    with pytest.raises(BundleError, match="контрольная сумма"):
        verify_checksums(bundle_dir)


def test_extra_and_missing_files_fail(tmp_path):
    bundle_dir = _stage(tmp_path)
    (bundle_dir / "npm" / "extra.tgz").write_bytes(b"x")
    with pytest.raises(BundleError, match="вне"):
        verify_checksums(bundle_dir)
    (bundle_dir / "npm" / "extra.tgz").unlink()
    (bundle_dir / "report.txt").unlink()
    with pytest.raises(BundleError, match="нет файлов"):
        verify_checksums(bundle_dir)


def test_manifest_mismatch_fails(tmp_path):
    bundle_dir = _stage(tmp_path)
    data = json.loads((bundle_dir / MANIFEST).read_text(encoding="utf-8"))
    data["files"][0]["sha256"] = "0" * 64
    (bundle_dir / MANIFEST).write_text(json.dumps(data), encoding="utf-8")
    write_checksums(bundle_dir)
    with pytest.raises(BundleError, match="manifest.json"):
        load_manifest(bundle_dir, verify_checksums(bundle_dir))


def test_pack_and_extract_roundtrip(tmp_path):
    archive = pack_bundle(_stage(tmp_path), tmp_path / "out")
    assert archive.name == "cowsay-20260913-214000.tar.gz"
    root = extract_bundle(archive, tmp_path / "extract")
    assert root.name == "cowsay-20260913-214000"
    verify_checksums(root)


def _tar_with(tmp_path, *members):
    path = tmp_path / "evil.tar.gz"
    with tarfile.open(path, "w:gz") as archive:
        for info in members:
            data = b"x" if info.isfile() else None
            if data is not None:
                info.size = len(data)
            archive.addfile(info, io.BytesIO(data) if data is not None else None)
    return path


def test_extract_rejects_traversal(tmp_path):
    archive = _tar_with(tmp_path, tarfile.TarInfo("b/../../evil"))
    with pytest.raises(BundleError):
        extract_bundle(archive, tmp_path / "extract")


def test_extract_rejects_symlink(tmp_path):
    link = tarfile.TarInfo("b/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    with pytest.raises(BundleError):
        extract_bundle(_tar_with(tmp_path, link), tmp_path / "extract")


def test_extract_rejects_multiple_roots(tmp_path):
    archive = _tar_with(tmp_path, tarfile.TarInfo("a/x"), tarfile.TarInfo("b/y"))
    with pytest.raises(BundleError):
        extract_bundle(archive, tmp_path / "extract")


@pytest.mark.parametrize(
    ("plan", "label"),
    [
        (InstallPlan("npm", ("@deepseek-ai/dsh",), ("npx",)), "dsh"),
        (InstallPlan("npm", ("cowsay@1.5.0",), ("npx",)), "cowsay"),
        (InstallPlan("pypi", ("graphify[x]==1.0",), ("uvx",)), "graphify"),
        (InstallPlan(None, (), ("/usr/bin/pnpm", "add")), "pnpm"),
    ],
)
def test_bundle_label(plan, label):
    assert bundle_label(plan) == label


def test_sums_file_is_sha256sum_compatible(tmp_path):
    line = (_stage(tmp_path) / SUMS).read_text(encoding="utf-8").splitlines()[0]
    digest, path = line.split("  ", 1)
    assert len(digest) == 64 and path == "manifest.json"
