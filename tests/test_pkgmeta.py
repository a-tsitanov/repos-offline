import io
import json
import tarfile

import pytest

from offpack.pkgmeta import (
    PackageFile,
    PackageMetaError,
    normalize_pypi_name,
    npm_bundle_name,
    parse_pypi_filename,
    read_npm_tarball,
    wheel_platform_tag,
)
from tests.helpers import make_file, make_npm_tgz


def test_read_npm_tarball(tmp_path):
    assert read_npm_tarball(make_npm_tgz(tmp_path, "cowsay", "1.5.0")) == ("cowsay", "1.5.0")


def test_read_scoped_npm_tarball_with_custom_root(tmp_path):
    path = make_npm_tgz(tmp_path, "@esbuild/win32-x64", "0.23.0", root="node")
    assert read_npm_tarball(path) == ("@esbuild/win32-x64", "0.23.0")


def test_read_npm_tarball_errors(tmp_path):
    with pytest.raises(PackageMetaError):
        read_npm_tarball(make_file(tmp_path, "broken.tgz", b"not gzip"))


def test_npm_bundle_name():
    assert npm_bundle_name("@deepseek-ai/dsh", "1.2.3") == "deepseek-ai__dsh-1.2.3.tgz"
    assert npm_bundle_name("cowsay", "1.5.0") == "cowsay-1.5.0.tgz"


def test_package_file_bundle_path(tmp_path):
    file = PackageFile("npm", "cowsay", "1.5.0", tmp_path / "x.tgz", "cowsay-1.5.0.tgz")
    assert file.bundle_path == "npm/cowsay-1.5.0.tgz"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("ruff-0.6.0-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl", ("ruff", "0.6.0")),
        ("Django_Rest-3.0-1-py3-none-any.whl", ("django-rest", "3.0")),
        ("PyYAML-6.0.1.tar.gz", ("pyyaml", "6.0.1")),
        ("python-dateutil-2.9.0.tar.gz", ("python-dateutil", "2.9.0")),
        ("legacy_pkg-1.0.zip", ("legacy-pkg", "1.0")),
        ("README.txt", None),
        ("a-b-c.whl", None),
        ("noversion.tar.gz", None),
    ],
)
def test_parse_pypi_filename(filename, expected):
    assert parse_pypi_filename(filename) == expected


def test_wheel_platform_tag():
    assert wheel_platform_tag("ruff-0.6.0-py3-none-win_amd64.whl") == "win_amd64"
    assert wheel_platform_tag("six-1.16.0-py2.py3-none-any.whl") == "any"
    assert wheel_platform_tag("six-1.16.0.tar.gz") is None


def test_read_npm_tarball_with_non_dict_json(tmp_path):
    """Test that valid JSON that's not a dict raises PackageMetaError."""
    tmp_path.mkdir(exist_ok=True)
    path = tmp_path / "bad-object.tgz"
    payload = json.dumps([]).encode()  # Valid JSON but not a dict
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("package/package.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(PackageMetaError):
        read_npm_tarball(path)


def test_read_npm_tarball_with_invalid_utf8(tmp_path):
    """Test that invalid UTF-8 in package.json raises PackageMetaError."""
    tmp_path.mkdir(exist_ok=True)
    path = tmp_path / "bad-utf8.tgz"
    payload = b'\x80\x81\x82'  # Invalid UTF-8 bytes
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("package/package.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(PackageMetaError):
        read_npm_tarball(path)


def test_normalize_pypi_name():
    assert normalize_pypi_name("Foo__Bar.baz") == "foo-bar-baz"


@pytest.mark.parametrize(
    ("name", "version"),
    [
        ("cowsay", "1.5.0"),
        ("@esbuild/win32-x64", "0.23.0"),
        ("@deepseek-ai/dsh", "0.1.5-rc.1"),
        ("JSONStream", "1.0.0+build.5"),
        ("lodash.merge", "4.6.2"),
        ("@types/node~x", "22.0.0-beta.1+sha.abc"),
        ("a" * 214, "1"),
    ],
)
def test_read_npm_tarball_accepts_valid_name_and_version(tmp_path, name, version):
    path = make_npm_tgz(tmp_path, name, version)
    assert read_npm_tarball(path) == (name, version)


def _npm_tgz_with(tmp_path, name, version):
    """Tarball с произвольным package.json (make_npm_tgz строит имя файла из name)."""
    path = tmp_path / "pkg.tgz"
    payload = json.dumps({"name": name, "version": version}).encode()
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo("package/package.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return path


@pytest.mark.parametrize(
    "name",
    [
        "evil\x1b[8m",
        "@scope\x1b[2J/pkg",
        "@scope/pkg\x9b31m",
        "bidi\u202epkg",
        "",
        "has space",
        "tab\tname",
        "new\nline",
        ".hidden",
        "_under",
        "@scope/.hidden",
        "@scope/_under",
        "@scope",
        "@/pkg",
        "@scope/",
        "scope/pkg",
        "@a/b/c",
        "a" * 215,
        "имя",
    ],
)
def test_read_npm_tarball_rejects_invalid_name(tmp_path, name):
    with pytest.raises(PackageMetaError) as exc:
        read_npm_tarball(_npm_tgz_with(tmp_path, name, "1.0.0"))
    assert str(exc.value).isprintable()


@pytest.mark.parametrize(
    "version",
    ["", "1 0", "1.0.0\x1b[8m", "v1.0.0", "-1", ".1", "1.0.0\n", "1/0", "1" * 257],
)
def test_read_npm_tarball_rejects_invalid_version(tmp_path, version):
    with pytest.raises(PackageMetaError) as exc:
        read_npm_tarball(_npm_tgz_with(tmp_path, "demo", version))
    assert str(exc.value).isprintable()
