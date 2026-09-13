from offpack.bundle import Notice
from offpack.collect import collect_npm, collect_pypi, parse_egress_log, sdist_only_notices
from offpack.pkgmeta import PackageFile
from offpack.platforms import parse_platforms
from tests.helpers import make_file, make_npm_tgz


def test_collect_npm(tmp_path):
    storage = tmp_path / "npm"
    make_npm_tgz(storage / "cowsay", "cowsay", "1.5.0")
    make_npm_tgz(storage / "@esbuild" / "win32-x64", "@esbuild/win32-x64", "0.23.0")
    make_file(storage / "cowsay", "package.json", b"{}")
    files = collect_npm(storage)
    assert sorted((f.name, f.version, f.bundle_name) for f in files) == [
        ("@esbuild/win32-x64", "0.23.0", "esbuild__win32-x64-0.23.0.tgz"),
        ("cowsay", "1.5.0", "cowsay-1.5.0.tgz"),
    ]
    assert all(f.ecosystem == "npm" and f.source.is_file() for f in files)


def test_collect_missing_dirs(tmp_path):
    assert collect_npm(tmp_path / "nope") == []
    assert collect_pypi(tmp_path / "nope") == []


def test_collect_pypi(tmp_path):
    serverdir = tmp_path / "pypi"
    make_file(serverdir / "+files" / "root" / "pypi" / "+f" / "ab1", "ruff-0.6.0-py3-none-win_amd64.whl")
    make_file(serverdir / "+files" / "root" / "pypi" / "+f" / "cd2", "PyYAML-6.0.1.tar.gz")
    make_file(serverdir, ".sqlite")
    make_file(serverdir, ".serverversion")
    files = collect_pypi(serverdir)
    assert sorted((f.name, f.version, f.bundle_name) for f in files) == [
        ("pyyaml", "6.0.1", "PyYAML-6.0.1.tar.gz"),
        ("ruff", "0.6.0", "ruff-0.6.0-py3-none-win_amd64.whl"),
    ]


SQUID_LOG = """\
1726252800.123    456 172.18.0.3 TCP_TUNNEL/200 1234 CONNECT github.com:443 - HIER_DIRECT/140.82.112.3 -
1726252801.000     12 172.18.0.3 TCP_MISS/200 99 GET http://nodejs.org/dist/index.json - HIER_DIRECT/1.2.3.4 application/json
1726252802.000     12 172.18.0.3 TCP_TUNNEL/200 99 CONNECT GitHub.com:443 - HIER_DIRECT/140.82.112.3 -
1726252803.000     12 172.18.0.3 TCP_MISS/200 99 GET http://verdaccio:4873/x - HIER_DIRECT/1.1.1.1 -
garbage line
"""


def test_parse_egress_log():
    assert parse_egress_log(SQUID_LOG) == ["github.com", "nodejs.org"]


def _pypi(tmp_path, filename, name="pkg", version="1.0"):
    return PackageFile("pypi", name, version, tmp_path / filename, filename)


def test_sdist_only_notices(tmp_path):
    platforms = parse_platforms("linux-x64,win-x64")
    files = [
        _pypi(tmp_path, "pkg-1.0.tar.gz"),
        _pypi(tmp_path, "pkg-1.0-cp312-cp312-manylinux_2_17_x86_64.whl"),
        _pypi(tmp_path, "solo-2.0.tar.gz", "solo", "2.0"),
        _pypi(tmp_path, "pure-1.0.tar.gz", "pure"),
        _pypi(tmp_path, "pure-1.0-py3-none-any.whl", "pure"),
        _pypi(tmp_path, "wheels-1.0-py3-none-win_amd64.whl", "wheels"),
    ]
    assert sdist_only_notices(files, platforms) == [
        Notice("sdist_only", "pkg==1.0 (win-x64)"),
        Notice("sdist_only", "solo==2.0 (linux-x64)"),
        Notice("sdist_only", "solo==2.0 (win-x64)"),
    ]
