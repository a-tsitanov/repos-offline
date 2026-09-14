import subprocess

import pytest

from offpack.commands import InstallPlan
from offpack.errors import OffpackError
from offpack.passes import Pass, plan_passes
from offpack.platforms import PLATFORMS, parse_platforms, parse_python_versions


def test_parse_platforms_default_order_and_dedup():
    assert [p.name for p in parse_platforms("win-x64, linux-x64,win-x64")] == [
        "win-x64",
        "linux-x64",
    ]


@pytest.mark.parametrize("value", ["", "mac-arm64", "linux-x64,freebsd"])
def test_parse_platforms_rejects(value):
    with pytest.raises(OffpackError):
        parse_platforms(value)


def test_parse_python_versions():
    assert parse_python_versions("3.11,3.12") == ("3.11", "3.12")
    with pytest.raises(OffpackError):
        parse_python_versions("3")
    with pytest.raises(OffpackError):
        parse_python_versions("")


@pytest.mark.parametrize(
    ("platform", "tag", "expected"),
    [
        ("linux-x64", "manylinux_2_17_x86_64.manylinux2014_x86_64", True),
        ("linux-x64", "musllinux_1_2_x86_64", False),
        ("linux-x64", "manylinux_2_17_aarch64", False),
        ("linux-x64", "any", True),
        ("win-x64", "win_amd64", True),
        ("win-x64", "win32", False),
        ("win-x64", "manylinux_2_28_x86_64", False),
    ],
)
def test_wheel_matches(platform, tag, expected):
    assert PLATFORMS[platform].wheel_matches(tag) is expected


PNPM_FLAGS = (
    "--config.dangerously-allow-all-builds=true",
    "--config.minimum-release-age=0",
    "--reporter=append-only",
    "--store-dir",
    "/tmp/offpack/pnpm-store",
)


def test_npm_single_pnpm_pass():
    plan = InstallPlan("npm", ("esbuild", "@s/b@1"), ("npx", "esbuild"))
    passes = plan_passes(plan, parse_platforms("linux-x64,win-x64"), ("3.12",))
    assert passes == [
        Pass(
            "npm",
            ("pnpm", "add", "esbuild", "@s/b@1",
             "--os=current", "--os=linux", "--os=win32", "--cpu=current", "--cpu=x64",
             *PNPM_FLAGS),
            workdir="/tmp/offpack/npm",
        )
    ]


def test_npm_pass_single_platform():
    plan = InstallPlan("npm", ("cowsay",), ("npx", "cowsay"))
    (item,) = plan_passes(plan, parse_platforms("win-x64"), ("3.11", "3.12"))
    assert item.argv[:6] == (
        "pnpm", "add", "cowsay", "--os=current", "--os=win32", "--cpu=current"
    )
    assert item.argv[6] == "--cpu=x64"


def test_pass_command_without_workdir_is_argv():
    item = Pass("discovery", ("uv", "pip", "install", "ruff"))
    assert item.command == item.argv


def test_pass_command_runs_in_fresh_project_dir(tmp_path):
    project = tmp_path / "npm"
    item = Pass("npm", ("sh", "-c", "pwd; cat package.json", "it's"), workdir=str(project))
    assert item.command[-4:] == item.argv
    result = subprocess.run(item.command, capture_output=True, text=True, check=True)
    assert result.stdout.split() == [str(project), "{}"]


def test_pypi_passes():
    plan = InstallPlan("pypi", ("ruff",), ("uv", "tool", "install", "ruff"))
    passes = plan_passes(plan, parse_platforms("win-x64"), ("3.11", "3.12"))
    assert passes == [
        Pass("discovery", ("uv", "pip", "install", "--target", "/tmp/offpack/discovery", "ruff")),
        Pass(
            "win-x64-py3.11",
            ("uv", "pip", "install", "--target", "/tmp/offpack/win-x64-py3.11",
             "--python-platform", "x86_64-pc-windows-msvc", "--python-version", "3.11", "ruff"),
        ),
        Pass(
            "win-x64-py3.12",
            ("uv", "pip", "install", "--target", "/tmp/offpack/win-x64-py3.12",
             "--python-platform", "x86_64-pc-windows-msvc", "--python-version", "3.12", "ruff"),
        ),
    ]


def test_unmapped_runs_raw_command_once():
    plan = InstallPlan(None, (), ("pnpm", "add", "x"))
    assert plan_passes(plan, parse_platforms("linux-x64"), ("3.12",)) == [
        Pass("discovery", ("pnpm", "add", "x"))
    ]
