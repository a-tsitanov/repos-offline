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


def test_normalize_pypi_name():
    assert normalize_pypi_name("Foo__Bar.baz") == "foo-bar-baz"
