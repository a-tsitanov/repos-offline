import tarfile
from datetime import UTC, datetime

import pytest

from offpack.bundle import SUMS, BundleError, BundleMeta, pack_bundle, stage_bundle
from offpack.importer import ImportOptions, run_import
from offpack.nexus import NexusAuthError, NexusClient
from offpack.pkgmeta import PackageFile, npm_bundle_name
from offpack.signing import SigningError, sign_file
from tests.conftest import create_signing_key
from tests.helpers import make_file, make_npm_tgz

META = BundleMeta(
    created_at=datetime(2026, 9, 13, tzinfo=UTC),
    command=["npx", "demo"],
    platforms=["linux-x64"],
    python_versions=["3.12"],
)


def make_archive(tmp_path, key=None, *, npm_versions=("1.0.0",), name="demo-20260913-000000"):
    src = tmp_path / "src"
    files = []
    for version in npm_versions:
        tgz = make_npm_tgz(src / "npm", "demo", version)
        files.append(PackageFile("npm", "demo", version, tgz, npm_bundle_name("demo", version)))
    whl = make_file(src / "pypi", "demo_py-1.0.0-py3-none-any.whl", b"wheel")
    files.append(PackageFile("pypi", "demo-py", "1.0.0", whl, whl.name))
    bundle_dir = stage_bundle(tmp_path / "stage", name, files, META, [])
    if key is not None:
        sign_file(bundle_dir / SUMS, key)
    return pack_bundle(bundle_dir, tmp_path / "out")


@pytest.fixture
def client(fake_nexus):
    return NexusClient(fake_nexus.url, "admin", "secret", timeout=5)


def test_import_uploads_everything(tmp_path, signing_key, fake_nexus, client):
    key, allowed = signing_key
    report = run_import(ImportOptions(make_archive(tmp_path, key), allowed_signers=allowed), client)
    assert report.ok
    assert len(report.uploaded) == 2
    assert sorted(fake_nexus.uploads) == [
        ("npm-hosted", "demo-1.0.0.tgz"),
        ("pypi-hosted", "demo_py-1.0.0-py3-none-any.whl"),
    ]


def test_second_import_skips(tmp_path, signing_key, fake_nexus, client):
    key, allowed = signing_key
    opts = ImportOptions(make_archive(tmp_path, key), allowed_signers=allowed)
    run_import(opts, client)
    again = run_import(opts, client)
    assert again.uploaded == []
    assert len(again.skipped) == 2
    assert len(fake_nexus.uploads) == 2


def test_dry_run_uploads_nothing(tmp_path, signing_key, fake_nexus, client):
    key, allowed = signing_key
    opts = ImportOptions(make_archive(tmp_path, key), allowed_signers=allowed, dry_run=True)
    report = run_import(opts, client)
    assert len(report.uploaded) == 2
    assert fake_nexus.uploads == []
    assert "будет загружено: 2" in report.render(dry_run=True)


def test_tampered_archive_rejected_before_upload(tmp_path, signing_key, fake_nexus, client):
    key, allowed = signing_key
    archive = make_archive(tmp_path, key)
    unpacked = tmp_path / "unpacked"
    with tarfile.open(archive, "r:gz") as tar:
        tar.extractall(unpacked, filter="data")
    root = unpacked / "demo-20260913-000000"
    (root / "npm" / "demo-1.0.0.tgz").write_bytes(b"evil")
    tampered = tmp_path / "tampered.tar.gz"
    with tarfile.open(tampered, "w:gz") as tar:
        tar.add(root, arcname=root.name)
    with pytest.raises(BundleError, match="контрольная сумма"):
        run_import(ImportOptions(tampered, allowed_signers=allowed), client)
    assert fake_nexus.uploads == []


def test_unsigned_archive(tmp_path, signing_key, fake_nexus, client):
    _, allowed = signing_key
    archive = make_archive(tmp_path)
    with pytest.raises(SigningError, match="нет подписи"):
        run_import(ImportOptions(archive, allowed_signers=allowed), client)
    report = run_import(ImportOptions(archive, allow_unsigned=True), client)
    assert report.ok
    assert any("подпись не проверялась" in n for n in report.notices)


def test_trust_option_required(tmp_path, signing_key, client):
    key, _ = signing_key
    with pytest.raises(BundleError, match="--allowed-signers"):
        run_import(ImportOptions(make_archive(tmp_path, key)), client)


def test_foreign_signer_rejected(tmp_path, signing_key, fake_nexus, client):
    key, _ = signing_key
    _, other_allowed = create_signing_key(tmp_path / "other")
    with pytest.raises(SigningError):
        run_import(ImportOptions(make_archive(tmp_path, key), allowed_signers=other_allowed), client)
    assert fake_nexus.uploads == []


def test_latest_restored_after_older_upload(tmp_path, signing_key, fake_nexus, client):
    key, allowed = signing_key
    fake_nexus.add_npm("demo", "2.0.0")
    report = run_import(ImportOptions(make_archive(tmp_path, key), allowed_signers=allowed), client)
    assert fake_nexus.dist_tag_calls == [("demo", "2.0.0")]
    assert fake_nexus.npm["demo"]["dist-tags"]["latest"] == "2.0.0"
    assert any("latest" in n for n in report.notices)


def test_dist_tag_failure_is_notice(tmp_path, signing_key, fake_nexus, client):
    key, allowed = signing_key
    fake_nexus.add_npm("demo", "2.0.0")
    fake_nexus.allow_dist_tags = False
    report = run_import(ImportOptions(make_archive(tmp_path, key), allowed_signers=allowed), client)
    assert report.ok
    assert any("не удалось" in n for n in report.notices)


def test_auth_error_aborts(tmp_path, signing_key, fake_nexus):
    key, allowed = signing_key
    bad = NexusClient(fake_nexus.url, "admin", "wrong", timeout=5)
    with pytest.raises(NexusAuthError):
        run_import(ImportOptions(make_archive(tmp_path, key), allowed_signers=allowed), bad)
