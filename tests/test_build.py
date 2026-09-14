import re
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


VERDACCIO_LINES = [
    "http <-- 200, user: null(172.18.0.5), req: 'GET /esbuild', bytes: 0/337391",
    "http <-- 200, user: null(172.18.0.5), req: 'GET /esbuild/-/esbuild-0.23.0.tgz', bytes: 0/0",
    "http <-- 200, user: null(172.18.0.5), req: 'GET /esbuild/-/esbuild-0.23.0.tgz', bytes: 0/34207",
    "http <-- 200, user: null(172.18.0.5), req: "
    "'GET /@esbuild/win32-x64/-/win32-x64-0.23.0.tgz', bytes: 0/4829680",
]


class FakeFollower:
    def __init__(self, compose):
        self.compose = compose

    def stop(self):
        self.compose.calls.append("follower_stop")


class FakeCompose:
    def __init__(self, project, assets, *, fixtures, fail_when=None, timeout_when=None, on_exec=None):
        self.project = project
        self.assets = assets
        self.fixtures = fixtures
        self.fail_when = fail_when
        self.timeout_when = timeout_when
        self.on_exec = on_exec
        self.proxy_handler = None
        self.calls = []

    def up(self):
        self.calls.append("up")

    def wait_ready(self, timeout, interval=2.0):
        self.calls.append("wait_ready")

    def follow_logs(self, services, on_line):
        self.calls.append(("follow_logs", tuple(services)))
        self.proxy_handler = on_line
        return FakeFollower(self)

    def exec_stream(self, service, argv, on_line, timeout=None):
        self.calls.append(("exec", tuple(argv)))
        if self.timeout_when and self.timeout_when in argv:
            raise subprocess.TimeoutExpired(list(argv), timeout)
        on_line("stdout")
        if self.proxy_handler is not None:
            for line in VERDACCIO_LINES:
                self.proxy_handler(line)
        if self.on_exec is not None:
            self.on_exec(argv)
        on_line("stderr")
        return 1 if self.fail_when and self.fail_when in argv else 0

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
    monkeypatch.setattr(build_module, "PROXY_LOG_SETTLE", 0)


def _opts(tmp_path, command=("npx", "esbuild", "--version"), sign_key=None, verbose=False):
    return BuildOptions(
        command=command,
        platforms=parse_platforms("linux-x64,win-x64"),
        pythons=("3.12",),
        out_dir=tmp_path / "dist",
        sign_key=sign_key,
        verbose=verbose,
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
    passes = [call[1] for call in compose.calls if call[0] == "exec"]
    assert len(passes) == 1
    assert passes[0][-3:] == ("--reporter=append-only", "--store-dir", "/tmp/offpack/pnpm-store")
    assert "pnpm" in passes[0] and "--os=win32" in passes[0]
    assert compose.calls[:3] == ["up", "wait_ready", ("follow_logs", ("verdaccio", "devpi"))]
    assert compose.calls[-3:] == ["follower_stop", "stop", "down"]
    root = extract_bundle(archive, tmp_path / "extract")
    assert (root / "SHA256SUMS.sig").is_file()
    manifest = load_manifest(root, verify_checksums(root))
    assert sorted(f.name for f in manifest.files) == ["@esbuild/win32-x64", "esbuild"]
    assert [(w.kind, w.detail) for w in manifest.warnings] == [("egress", "github.com")]
    assert (tmp_path / "dist" / "esbuild-20260913-214000.log").is_file()


def test_failed_pass_stops_stack_and_keeps_log(tmp_path, fixtures):
    created = []
    lines = []
    with pytest.raises(BuildError, match="проход npm"):
        run_build(
            _opts(tmp_path),
            compose_factory=_factory(created, fixtures=fixtures, fail_when="--os=win32"),
            now=NOW,
            log=lines.append,
        )
    assert created[0].calls[-2:] == ["follower_stop", "down"]
    log_path = tmp_path / "dist" / "esbuild-20260913-214000.log"
    log = log_path.read_text()
    assert re.search(r"^=== \[\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\] npm: pnpm add esbuild", log, re.M)
    assert "stdout\nstderr\n" in log
    assert any(re.fullmatch(rf"\[\d\d:\d\d\] ✗ npm: код 1 \(\d+s\), лог: {re.escape(str(log_path))}", line) for line in lines), lines
    assert not list((tmp_path / "dist").glob("*.tar.gz"))


def test_pass_timeout_is_build_error_and_stack_is_down(tmp_path, fixtures):
    created = []
    with pytest.raises(BuildError, match="превышен таймаут.*лог"):
        run_build(
            _opts(tmp_path),
            compose_factory=_factory(created, fixtures=fixtures, timeout_when="pnpm"),
            now=NOW,
            log=lambda _: None,
        )
    assert created[0].calls[-2:] == ["follower_stop", "down"]


def test_log_file_is_written_live(tmp_path, fixtures):
    log_path = tmp_path / "dist" / "esbuild-20260913-214000.log"
    seen = []
    run_build(
        _opts(tmp_path),
        compose_factory=_factory([], fixtures=fixtures, on_exec=lambda argv: seen.append(log_path.read_text())),
        now=NOW,
        log=lambda _: None,
    )
    assert "npm: pnpm add" in seen[0] and seen[0].endswith("stdout\n")


def test_progress_output(tmp_path, fixtures):
    lines = []
    archive = run_build(
        _opts(tmp_path), compose_factory=_factory([], fixtures=fixtures), now=NOW, log=lines.append
    )
    text = "\n".join(lines)
    stamp = r"\[\d\d:\d\d\] "
    for pattern in [
        rf"{stamp}✓ песочница поднята \(\d+s\)",
        rf"{stamp}✓ сервисы готовы \(\d+s\)",
        rf"{stamp}проход npm: pnpm add esbuild --os=current .*",
        r"        ↓ npm  esbuild 0\.23\.0  33\.4 КБ",
        r"        ↓ npm  @esbuild/win32-x64 0\.23\.0  4\.6 МБ",
        rf"{stamp}✓ npm: \+2 файлов, 4\.6 МБ \(\d+s\)",
        rf"{stamp}✓ файлы собраны \(\d+s\)",
        rf"{stamp}✓ архив упакован \(\d+s\)",
        rf"{stamp}npm 2 файлов \d+ Б · pypi 0 файлов 0 Б · предупреждений 1",
    ]:
        assert re.search(f"^{pattern}$", text, re.M), (pattern, text)
    assert text.count("↓ npm  esbuild 0.23.0") == 1
    assert "stdout" not in text
    assert lines[-1].startswith("offpack: архив создан")
    assert archive.is_file()


def test_verbose_streams_tool_output(tmp_path, fixtures):
    lines = []
    run_build(
        _opts(tmp_path, verbose=True),
        compose_factory=_factory([], fixtures=fixtures),
        now=NOW,
        log=lines.append,
    )
    assert "          stdout" in lines and "          stderr" in lines


def test_missing_egress_log_adds_notice(tmp_path, fixtures):
    (fixtures / "squid").unlink()
    archive = run_build(
        _opts(tmp_path),
        compose_factory=_factory([], fixtures=fixtures),
        now=NOW,
        log=lambda _: None,
    )
    root = extract_bundle(archive, tmp_path / "extract")
    manifest = load_manifest(root, verify_checksums(root))
    kinds = [w.kind for w in manifest.warnings]
    assert "egress_log_missing" in kinds
    assert "egress" not in kinds


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
    assert [c for c in created[0].calls if c[0] == "exec"] == [
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
