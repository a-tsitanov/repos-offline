import pytest

from offpack.commands import InstallPlan, UnsupportedCommand, parse_command
from offpack.errors import OffpackError


@pytest.mark.parametrize(
    ("argv", "ecosystem", "specs"),
    [
        (["npx", "@deepseek-ai/dsh", "web"], "npm", ("@deepseek-ai/dsh",)),
        (["npx", "-y", "cowsay@1.5.0", "hello"], "npm", ("cowsay@1.5.0",)),
        (["npx", "--yes", "--", "esbuild", "--version"], "npm", ("esbuild",)),
        (["npx", "-p", "a", "-p", "b@2", "cmd", "x"], "npm", ("a", "b@2")),
        (["npx", "--package=typescript", "tsc", "--init"], "npm", ("typescript",)),
        (["npx", "--registry", "http://x", "cowsay"], "npm", ("cowsay",)),
        (["/usr/local/bin/npx", "cowsay"], "npm", ("cowsay",)),
        (["npm", "exec", "--package=pkg", "--", "run"], "npm", ("pkg",)),
        (["npm", "install", "-g", "a", "@s/b@^1"], "npm", ("a", "@s/b@^1")),
        (["npm", "i", "--save-dev", "eslint"], "npm", ("eslint",)),
        (["uvx", "ruff", "check"], "pypi", ("ruff",)),
        (["uvx", "ruff@0.6.0", "--version"], "pypi", ("ruff==0.6.0",)),
        (["uvx", "ruff@latest"], "pypi", ("ruff",)),
        (
            ["uvx", "--from", "httpie==3.2.2", "--with", "rich", "http", "GET", "x"],
            "pypi",
            ("httpie==3.2.2", "rich"),
        ),
        (["uvx", "-p", "3.12", "black"], "pypi", ("black",)),
        (["uv", "tool", "run", "ruff"], "pypi", ("ruff",)),
        (["uv", "tool", "install", "graphify"], "pypi", ("graphify",)),
        (
            ["uv", "tool", "install", "--with", "pyyaml", "--python", "3.12", "graphify"],
            "pypi",
            ("graphify", "pyyaml"),
        ),
        (["uv", "pip", "install", "requests", "rich>=13"], "pypi", ("requests", "rich>=13")),
        (["pip", "install", "-i", "http://x/simple", "requests"], "pypi", ("requests",)),
        (["pip3.12", "install", "--only-binary", ":all:", "numpy"], "pypi", ("numpy",)),
        (["python3", "-m", "pip", "install", "--upgrade", "requests"], "pypi", ("requests",)),
        (["npm", "install", "--loglevel", "silly", "foo"], "npm", ("foo",)),
        (["npm", "i", "--os", "win32", "foo"], "npm", ("foo",)),
        (["npm", "i", "--cpu", "x64", "--libc", "glibc", "foo"], "npm", ("foo",)),
        (["npx", "--loglevel", "warn", "cowsay"], "npm", ("cowsay",)),
        (["uvx", "--python-preference", "only-managed", "ruff"], "pypi", ("ruff",)),
        (["uvx", "-C", "k=v", "ruff"], "pypi", ("ruff",)),
        (
            ["uv", "tool", "install", "--config-setting", "k=v", "--config-settings-package", "p:k=v", "x"],
            "pypi",
            ("x",),
        ),
        (["uv", "pip", "install", "--python-preference", "system", "requests"], "pypi", ("requests",)),
    ],
)
def test_mapped(argv, ecosystem, specs):
    plan = parse_command(argv)
    assert plan == InstallPlan(ecosystem=ecosystem, specs=specs, raw=tuple(argv))
    assert plan.mapped


@pytest.mark.parametrize(
    "argv",
    [
        ["pnpm", "add", "x"],
        ["npm", "run", "build"],
        ["cargo", "install", "ripgrep"],
        ["uv", "run", "script.py"],
    ],
)
def test_unmapped(argv):
    plan = parse_command(argv)
    assert plan.ecosystem is None
    assert plan.specs == ()
    assert plan.raw == tuple(argv)
    assert not plan.mapped


@pytest.mark.parametrize(
    "argv",
    [
        ["npm", "ci"],
        ["npm", "install"],
        ["uv", "sync"],
        ["uv", "add", "x"],
        ["pip", "install", "-r", "requirements.txt"],
        ["pip", "install", "-rrequirements.txt"],
        ["uv", "pip", "install", "-e", "."],
        ["uv", "tool", "install", "--with-requirements", "r.txt", "x"],
        ["pip", "install", "./local"],
        ["pip", "install", "git+https://github.com/a/b"],
        ["pip", "install", "pkg @ https://example.com/p.whl"],
        ["npm", "install", "user/repo"],
        ["npx", "github:user/repo"],
        ["npm", "i", "file:../x"],
        ["npx"],
        ["uvx"],
    ],
)
def test_unsupported(argv):
    with pytest.raises(UnsupportedCommand):
        parse_command(argv)


def test_empty_command():
    with pytest.raises(OffpackError):
        parse_command([])
