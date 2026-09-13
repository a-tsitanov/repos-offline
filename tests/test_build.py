import shutil
import subprocess
from datetime import UTC, datetime

import pytest

from offpack import build as build_module
from offpack.build import BuildError, BuildOptions, run_build
from offpack.bundle import extract_bundle, load_manifest, verify_checksums
from offpack.platforms import parse_platforms
from tests.helpers import make_file, make_npm_tgz

SQUID_LINE = "1726252800.1 1 172.18.0.3 TCP_TUNNEL/200 1 CONNECT github.com:443 - HIER_DIRECT/1.1.1.1 -\n"


class FakeCompose:
    def __init__(self, project, assets, *, fixtures, fail_when=None):
        self.project = project
        self.assets = assets
        self.fixtures = fixtures
        self.fail_when = fail_when
        self.calls = []

    def up(self):
        self.calls.append("up")

    def wait_ready(self, timeout, interval=2.0):
        self.calls.append("wait_ready")

    def exec(self, service, argv, timeout=None):
        self.calls.append(("exec", tuple(argv)))
        code = 1 if self.fail_when and self.fail_when in argv else 0
        return subprocess.CompletedProcess(list(argv), code, "stdout\n", "stderr\n")

    def stop(self):
        self.calls.append("stop")

    def copy_out(self, service, src, dest):
        source = self.fixtures / service
        if not source.exists():
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, dest)
        else:
            shutil.copyfile(source, dest)
        return True

    def down(self):
        self.calls.append("down")


@pytest.fixture
def fixtures(tmp_path):
    root = tmp_path / "fixtures"
    make_npm_tgz(root / "verdaccio" / "esbuild", "esbuild", "0.23.0")
    make_npm_tgz(root / "verdaccio" / "@esbuild" / "win32-x64", "@esbuild/win32-x64", "0.23.0")
    make_file(root, "squid", SQUID_LINE.encode())
    return root


@pytest.fixture(autouse=True)
def no_docker_check(monkeypatch):
    monkeypatch.setattr(build_module, "ensure_docker", lambda: None)


def _opts(tmp_path, command=("npx", "esbuild", "--version"), sign_key=None):
    return BuildOptions(
        command=command,
        platforms=parse_platforms("linux-x64,win-x64"),
        pythons=("3.12",),
        out_dir=tmp_path / "dist",
        sign_key=sign_key,
    )


def _factory(created, **kwargs):
    def factory(project, assets):
        compose = FakeCompose(project, assets, **kwargs)
        created.append(compose)
        return compose

    return factory


NOW = datetime(2026, 9, 13, 21, 40, tzinfo=UTC)


def test_build_creates_signed_bundle(tmp_path, fixtures, signing_key):
    key, _ = signing_key
    created = []
    archive = run_build(
        _opts(tmp_path, sign_key=key),
        compose_factory=_factory(created, fixtures=fixtures),
        now=NOW,
        log=lambda _: None,
    )
    assert archive == tmp_path / "dist" / "esbuild-20260913-214000.tar.gz"
    compose = created[0]
    passes = [call[1] for call in compose.calls if isinstance(call, tuple)]
    assert len(passes) == 3
    assert compose.calls[:2] == ["up", "wait_ready"]
    assert compose.calls[-2:] == ["stop", "down"]
    root = extract_bundle(archive, tmp_path / "extract")
    assert (root / "SHA256SUMS.sig").is_file()
    manifest = load_manifest(root, verify_checksums(root))
    assert sorted(f.name for f in manifest.files) == ["@esbuild/win32-x64", "esbuild"]
    assert [(w.kind, w.detail) for w in manifest.warnings] == [("egress", "github.com")]
    assert (tmp_path / "dist" / "esbuild-20260913-214000.log").is_file()


def test_failed_pass_stops_stack_and_keeps_log(tmp_path, fixtures):
    created = []
    with pytest.raises(BuildError, match="проход win-x64"):
        run_build(
            _opts(tmp_path),
            compose_factory=_factory(created, fixtures=fixtures, fail_when="--os=win32"),
            now=NOW,
            log=lambda _: None,
        )
    assert created[0].calls[-1] == "down"
    log = (tmp_path / "dist" / "esbuild-20260913-214000.log").read_text()
    assert "=== win-x64" in log and "stderr" in log
    assert not list((tmp_path / "dist").glob("*.tar.gz"))


def test_no_files_is_error(tmp_path):
    with pytest.raises(BuildError, match="не собрано ни одного файла"):
        run_build(
            _opts(tmp_path),
            compose_factory=_factory([], fixtures=tmp_path / "empty"),
            now=NOW,
            log=lambda _: None,
        )


def test_unmapped_command_adds_notice(tmp_path, fixtures):
    created = []
    archive = run_build(
        _opts(tmp_path, command=("pnpm", "add", "esbuild")),
        compose_factory=_factory(created, fixtures=fixtures),
        now=NOW,
        log=lambda _: None,
    )
    root = extract_bundle(archive, tmp_path / "extract")
    manifest = load_manifest(root, verify_checksums(root))
    assert "unmapped_command" in [w.kind for w in manifest.warnings]
    assert [c for c in created[0].calls if isinstance(c, tuple)] == [
        ("exec", ("pnpm", "add", "esbuild"))
    ]


def test_missing_sign_key_fails_before_docker(tmp_path, fixtures):
    created = []
    with pytest.raises(BuildError, match="--no-sign"):
        run_build(
            _opts(tmp_path, sign_key=tmp_path / "nope"),
            compose_factory=_factory(created, fixtures=fixtures),
            now=NOW,
            log=lambda _: None,
        )
    assert created == []
