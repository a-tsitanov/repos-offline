"""Интеграционные тесты с настоящим docker. Запуск: uv run pytest -m docker -v"""

import secrets

import pytest

from offpack.build import BuildOptions, run_build
from offpack.bundle import extract_bundle, load_manifest, verify_checksums
from offpack.compose import Compose, sandbox_assets
from offpack.platforms import parse_platforms

pytestmark = pytest.mark.docker


def test_stack_isolation():
    with sandbox_assets() as assets:
        compose = Compose(f"offpack-it-{secrets.token_hex(3)}", assets)
        try:
            compose.up()
            compose.wait_ready(timeout=180)
            via_proxy = compose.exec("sandbox", ["npm", "ping"], timeout=120)
            assert via_proxy.returncode == 0, via_proxy.stderr
            direct = compose.exec(
                "sandbox",
                ["node", "-e", "fetch('https://registry.npmjs.org/').then("
                 "() => process.exit(0), () => process.exit(1))"],
                timeout=60,
            )
            assert direct.returncode != 0, "у песочницы не должно быть прямого выхода в интернет"
        finally:
            compose.down()


def _manifest(archive, tmp_path):
    root = extract_bundle(archive, tmp_path / "extract")
    return load_manifest(root, verify_checksums(root))


def test_build_npm_esbuild_for_both_platforms(tmp_path):
    archive = run_build(
        BuildOptions(
            command=("npx", "esbuild", "--version"),
            platforms=parse_platforms("linux-x64,win-x64"),
            pythons=("3.12",),
            out_dir=tmp_path / "dist",
            sign_key=None,
            timeout=1200,
        )
    )
    names = {f.name for f in _manifest(archive, tmp_path).files}
    assert {"esbuild", "@esbuild/linux-x64", "@esbuild/win32-x64"} <= names


def test_build_pypi_ruff_for_both_platforms(tmp_path):
    archive = run_build(
        BuildOptions(
            command=("uv", "tool", "install", "ruff"),
            platforms=parse_platforms("linux-x64,win-x64"),
            pythons=("3.12",),
            out_dir=tmp_path / "dist",
            sign_key=None,
            timeout=1200,
        )
    )
    wheels = [f.path for f in _manifest(archive, tmp_path).files if f.name == "ruff"]
    assert any("manylinux" in w and "x86_64" in w for w in wheels), wheels
    assert any("win_amd64" in w for w in wheels), wheels
