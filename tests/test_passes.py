import pytest

from offpack.commands import InstallPlan
from offpack.errors import OffpackError
from offpack.passes import Pass, plan_passes
from offpack.platforms import PLATFORMS, parse_platforms, parse_python_versions

NPM_BASE = ("npm", "install", "--no-save", "--no-audit", "--no-fund")


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


def test_npm_passes():
    plan = InstallPlan("npm", ("esbuild", "@s/b@1"), ("npx", "esbuild"))
    passes = plan_passes(plan, parse_platforms("linux-x64,win-x64"), ("3.12",))
    assert passes == [
        Pass("discovery", (*NPM_BASE, "--prefix", "/tmp/offpack/discovery", "esbuild", "@s/b@1")),
        Pass(
            "linux-x64",
            (*NPM_BASE, "--ignore-scripts", "--os=linux", "--cpu=x64",
             "--prefix", "/tmp/offpack/linux-x64", "esbuild", "@s/b@1"),
        ),
        Pass(
            "win-x64",
            (*NPM_BASE, "--ignore-scripts", "--os=win32", "--cpu=x64",
             "--prefix", "/tmp/offpack/win-x64", "esbuild", "@s/b@1"),
        ),
    ]


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
