"""Интеграционные тесты с настоящим docker. Запуск: uv run pytest -m docker -v"""

import re
import secrets
from contextlib import contextmanager

import pytest

from offpack.build import BuildOptions, run_build
from offpack.bundle import extract_bundle, load_manifest, verify_checksums
from offpack.collect import parse_egress_log
from offpack.compose import Compose, sandbox_assets
from offpack.platforms import parse_platforms

pytestmark = pytest.mark.docker

# Анонимный запрос к devpi: печатает HTTP-статус, код 0 — запрос отклонён.
_DEVPI_REQUEST = """
const [method, url, body] = process.argv.slice(1);
fetch(url, {method, body,
            headers: {'content-type': 'application/json', accept: 'application/json'}})
  .then(r => { console.log(r.status); process.exit(r.ok ? 1 : 0); },
        e => { console.log(String(e)); process.exit(2); });
"""

# Запрос через squid без учёта NO_PROXY: печатает HTTP-статус ответа прокси.
_VIA_SQUID = """
const http = require('http');
const [method, target] = process.argv.slice(1);
const req = http.request({host: 'squid', port: 3128, method, path: target, timeout: 30000});
const done = res => { console.log(res.statusCode); process.exit(0); };
req.on('connect', done);
req.on('response', done);
req.on('timeout', () => { console.log('timeout'); process.exit(1); });
req.on('error', e => { console.log(String(e)); process.exit(1); });
req.end();
"""


@contextmanager
def _stack():
    with sandbox_assets() as assets:
        compose = Compose(f"offpack-it-{secrets.token_hex(3)}", assets)
        try:
            compose.up()
            compose.wait_ready(timeout=180)
            yield compose
        finally:
            compose.down()


@pytest.fixture(scope="module")
def stack():
    with _stack() as compose:
        yield compose


def test_stack_isolation(stack):
    via_proxy = stack.exec("sandbox", ["npm", "ping"], timeout=120)
    assert via_proxy.returncode == 0, via_proxy.stderr
    direct = stack.exec(
        "sandbox",
        ["node", "-e", "fetch('https://registry.npmjs.org/').then("
         "() => process.exit(0), () => process.exit(1))"],
        timeout=60,
    )
    assert direct.returncode != 0, "у песочницы не должно быть прямого выхода в интернет"


@pytest.mark.parametrize(
    ("method", "url", "body"),
    [
        ("PUT", "http://devpi:3141/evil", '{"password": "x"}'),
        ("PUT", "http://devpi:3141/root/evil", '{"type": "stage"}'),
        ("POST", "http://devpi:3141/+login", '{"user": "root", "password": ""}'),
    ],
)
def test_devpi_rejects_anonymous_modification(stack, method, url, body):
    result = stack.exec("sandbox", ["node", "-e", _DEVPI_REQUEST, method, url, body], timeout=60)
    assert result.returncode == 0, f"devpi принял {method} {url}: {result.stdout}"


@pytest.mark.parametrize(
    ("method", "target", "status"),
    [
        ("CONNECT", "169.254.169.254:443", "403"),
        ("GET", "http://169.254.169.254/latest/meta-data/", "403"),
        ("CONNECT", "10.0.0.1:443", "403"),
        ("GET", "http://127.0.0.1:3128/", "403"),
        ("CONNECT", "example.org:80", "403"),
        ("CONNECT", "example.org:443", "200"),
    ],
)
def test_squid_blocks_private_destinations(stack, method, target, status):
    result = stack.exec("sandbox", ["node", "-e", _VIA_SQUID, method, target], timeout=60)
    assert result.stdout.strip() == status, (result.stdout, result.stderr)


def test_egress_is_logged(tmp_path):
    with _stack() as compose:
        # --default-index, а не --index-url: у uv переменная UV_DEFAULT_INDEX песочницы
        # важнее флага --index-url, и запрос ушёл бы в devpi
        attempt = compose.exec(
            "sandbox",
            ["uv", "pip", "install", "--target", "/tmp/offpack/x",
             "--default-index", "https://example.org/simple", "nonexistent-offpack-pkg"],
            timeout=180,
        )
        assert attempt.returncode != 0, attempt.stdout
        compose.stop()
        assert compose.copy_out("squid", "/var/log/squid/access.log", tmp_path / "access.log")
    log = (tmp_path / "access.log").read_text(encoding="utf-8", errors="replace")
    assert "example.org" in parse_egress_log(log), log


def _manifest(archive, tmp_path):
    root = extract_bundle(archive, tmp_path / "extract")
    return load_manifest(root, verify_checksums(root))


def test_build_npm_esbuild_for_both_platforms(tmp_path):
    lines = []
    archive = run_build(
        BuildOptions(
            command=("npx", "esbuild", "--version"),
            platforms=parse_platforms("linux-x64,win-x64"),
            pythons=("3.12",),
            out_dir=tmp_path / "dist",
            sign_key=None,
            timeout=1200,
        ),
        log=lines.append,
    )
    names = {f.name for f in _manifest(archive, tmp_path).files}
    assert {"esbuild", "@esbuild/linux-x64", "@esbuild/win32-x64"} <= names
    text = "\n".join(lines)
    # список загрузок строится по настоящему логу Verdaccio
    assert re.search(r"^        ↓ npm  @esbuild/win32-x64 \S+  [\d.]+ МБ$", text, re.M), text
    assert re.search(r"✓ npm: \+\d+ файлов, [\d.]+ МБ", text), text
    # установочные скрипты зависимостей выполняются в проходе pnpm
    (log_file,) = (tmp_path / "dist").glob("*.log")
    assert "esbuild postinstall: Done" in log_file.read_text(), log_file.read_text()


def test_build_pypi_ruff_for_both_platforms(tmp_path):
    lines = []
    archive = run_build(
        BuildOptions(
            command=("uv", "tool", "install", "ruff"),
            platforms=parse_platforms("linux-x64,win-x64"),
            pythons=("3.12",),
            out_dir=tmp_path / "dist",
            sign_key=None,
            timeout=1200,
        ),
        log=lines.append,
    )
    # список загрузок строится по настоящему логу devpi
    assert any(line.startswith("        ↓ pypi ruff-") for line in lines), lines
    wheels = [f.path for f in _manifest(archive, tmp_path).files if f.name == "ruff"]
    assert any("manylinux" in w and "x86_64" in w for w in wheels), wheels
    assert any("win_amd64" in w for w in wheels), wheels
