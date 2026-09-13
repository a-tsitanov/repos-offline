"""Разбор команды пользователя в план установки."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from offpack.errors import OffpackError

Ecosystem = Literal["npm", "pypi"]


class UnsupportedCommand(OffpackError):
    """Команда распознана, но такая форма не поддерживается в v1."""


@dataclass(frozen=True)
class InstallPlan:
    ecosystem: Ecosystem | None
    specs: tuple[str, ...]
    raw: tuple[str, ...]

    @property
    def mapped(self) -> bool:
        return self.ecosystem is not None


_NPM_VALUE_FLAGS = frozenset(
    {
        "--registry",
        "--cache",
        "--prefix",
        "--userconfig",
        "-w",
        "--workspace",
        "--omit",
        "--include",
        "--tag",
        "--install-strategy",
        "--before",
    }
)
_NPX_VALUE_FLAGS = _NPM_VALUE_FLAGS | {"-p", "--package", "-c", "--call"}
_NPM_INSTALL = frozenset(
    {
        "install",
        "i",
        "add",
        "in",
        "ins",
        "inst",
        "insta",
        "instal",
        "isnt",
        "isnta",
        "isntal",
        "isntall",
    }
)
_NPM_CI = frozenset({"ci", "clean-install", "ic", "install-clean", "isntall-clean"})
_UV_VALUE_FLAGS = frozenset(
    {
        "-p",
        "--python",
        "--index",
        "-i",
        "--index-url",
        "--default-index",
        "--extra-index-url",
        "-f",
        "--find-links",
        "--cache-dir",
        "--python-version",
        "--python-platform",
        "--target",
        "--prefix",
        "--index-strategy",
        "--keyring-provider",
        "--resolution",
        "--prerelease",
        "--exclude-newer",
        "--color",
        "--directory",
        "--project",
        "--config-file",
        "--link-mode",
        "--refresh-package",
        "--reinstall-package",
        "--upgrade-package",
        "-P",
        "--allow-insecure-host",
        "--no-binary-package",
        "--no-build-package",
    }
)
_UV_TOOL_VALUE_FLAGS = _UV_VALUE_FLAGS | {"--from", "--with", "-w"}
_UV_TOOL_FILE_FLAGS = frozenset(
    {
        "--with-requirements",
        "--with-editable",
        "-e",
        "--editable",
        "-c",
        "--constraints",
        "--overrides",
    }
)
_PIP_VALUE_FLAGS = frozenset(
    {
        "-i",
        "--index-url",
        "--extra-index-url",
        "-t",
        "--target",
        "--prefix",
        "--root",
        "-f",
        "--find-links",
        "--platform",
        "--python-version",
        "--implementation",
        "--abi",
        "--trusted-host",
        "--progress-bar",
        "--log",
        "--cache-dir",
        "--src",
        "--upgrade-strategy",
        "--no-binary",
        "--only-binary",
        "-C",
        "--config-settings",
        "--global-option",
        "--proxy",
        "--timeout",
        "--retries",
        "--exists-action",
        "--cert",
        "--client-cert",
        "--python",
    }
)
_PIP_FILE_FLAGS = frozenset(
    {
        "-r",
        "--requirement",
        "-e",
        "--editable",
        "-c",
        "--constraint",
        "--overrides",
        "--override",
        "-b",
        "--build-constraint",
        "--build-constraints",
    }
)
_UV_PIP_VALUE_FLAGS = _UV_VALUE_FLAGS | _PIP_VALUE_FLAGS
_NON_REGISTRY_PREFIXES = (
    ".",
    "/",
    "~",
    "file:",
    "git+",
    "git:",
    "github:",
    "http:",
    "https:",
    "link:",
    "workspace:",
)
_PIP_RE = re.compile(r"pip(\d+(\.\d+)?)?")
_PYTHON_RE = re.compile(r"python(\d+(\.\d+)?)?")

Options = list[tuple[str, str | None]]


def parse_command(argv: Sequence[str]) -> InstallPlan:
    raw = tuple(argv)
    if not raw:
        raise OffpackError("не указана команда")
    head = os.path.basename(raw[0])
    rest = list(raw[1:])
    ecosystem: Ecosystem | None = None
    specs: list[str] | None = None
    if head == "npx":
        ecosystem, specs = "npm", _parse_npx(rest)
    elif head == "npm":
        ecosystem, specs = "npm", _parse_npm(rest)
    elif head == "uvx":
        ecosystem, specs = "pypi", _parse_uvx(rest)
    elif head == "uv":
        ecosystem, specs = "pypi", _parse_uv(rest)
    elif _PIP_RE.fullmatch(head) and rest[:1] == ["install"]:
        ecosystem, specs = "pypi", _parse_pip_install(rest[1:], _PIP_VALUE_FLAGS)
    elif _PYTHON_RE.fullmatch(head) and rest[:3] == ["-m", "pip", "install"]:
        ecosystem, specs = "pypi", _parse_pip_install(rest[3:], _PIP_VALUE_FLAGS)
    if specs is None or ecosystem is None:
        return InstallPlan(ecosystem=None, specs=(), raw=raw)
    for spec in specs:
        _check_registry_spec(ecosystem, spec)
    return InstallPlan(ecosystem=ecosystem, specs=tuple(specs), raw=raw)


def _scan(
    tokens: Sequence[str],
    *,
    value_flags: frozenset[str],
    file_flags: frozenset[str] = frozenset(),
    stop_at_positional: bool = False,
) -> tuple[Options, list[str]]:
    """Разделить токены на опции и позиционные аргументы.

    stop_at_positional: всё после первого позиционного аргумента — аргументы
    запускаемой программы (npx, uvx), они не разбираются.
    """
    options: Options = []
    positionals: list[str] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--":
            positionals.extend(tokens[i + 1 :])
            break
        if token.startswith("-") and token != "-":
            name, eq, value = token.partition("=")
            short = name if token.startswith("--") else token[:2]
            if name in file_flags or short in file_flags:
                raise UnsupportedCommand(
                    f"флаг {short} ссылается на файлы проекта — установка по проекту"
                    " вне v1"
                )
            if eq:
                options.append((name, value))
            elif name in value_flags:
                if i + 1 >= len(tokens):
                    raise OffpackError(f"у флага {name} нет значения")
                options.append((name, tokens[i + 1]))
                i += 1
            else:
                options.append((name, None))
            i += 1
            continue
        positionals.append(token)
        if stop_at_positional:
            positionals.extend(tokens[i + 1 :])
            break
        i += 1
    return options, positionals


def _values(options: Options, *names: str) -> list[str]:
    return [value for name, value in options if name in names and value is not None]


def _parse_npx(tokens: Sequence[str]) -> list[str]:
    options, positionals = _scan(
        tokens, value_flags=_NPX_VALUE_FLAGS, stop_at_positional=True
    )
    packages = _values(options, "-p", "--package")
    if packages:
        return packages
    if positionals:
        return [positionals[0]]
    raise UnsupportedCommand("в команде npx не указан пакет")


def _parse_npm(tokens: list[str]) -> list[str] | None:
    if not tokens:
        return None
    sub, rest = tokens[0], tokens[1:]
    if sub in _NPM_CI:
        raise UnsupportedCommand("npm ci — установка по проекту, вне v1")
    if sub in ("exec", "x"):
        return _parse_npx(rest)
    if sub in _NPM_INSTALL:
        _, positionals = _scan(rest, value_flags=_NPM_VALUE_FLAGS)
        if not positionals:
            raise UnsupportedCommand(
                "npm install без пакетов — установка по проекту, вне v1"
            )
        return positionals
    return None


def _parse_uv(tokens: list[str]) -> list[str] | None:
    if tokens[:1] in (["sync"], ["add"], ["lock"]):
        raise UnsupportedCommand(f"uv {tokens[0]} — установка по проекту, вне v1")
    if tokens[:2] == ["tool", "install"]:
        return _parse_uv_tool_install(tokens[2:])
    if tokens[:2] == ["tool", "run"]:
        return _parse_uvx(tokens[2:])
    if tokens[:2] == ["pip", "install"]:
        return _parse_pip_install(tokens[2:], _UV_PIP_VALUE_FLAGS)
    return None


def _uvx_spec(command: str) -> str:
    name, at, version = command.partition("@")
    if not at or version == "latest":
        return name
    return f"{name}=={version}"


def _parse_uvx(tokens: Sequence[str]) -> list[str]:
    options, positionals = _scan(
        tokens,
        value_flags=_UV_TOOL_VALUE_FLAGS,
        file_flags=_UV_TOOL_FILE_FLAGS,
        stop_at_positional=True,
    )
    froms = _values(options, "--from")
    if froms:
        base = froms[-1]
    elif positionals:
        base = _uvx_spec(positionals[0])
    else:
        raise UnsupportedCommand("в команде uvx не указан пакет")
    return [base, *_values(options, "--with", "-w")]


def _parse_uv_tool_install(tokens: Sequence[str]) -> list[str]:
    options, positionals = _scan(
        tokens, value_flags=_UV_TOOL_VALUE_FLAGS, file_flags=_UV_TOOL_FILE_FLAGS
    )
    froms = _values(options, "--from")
    if froms:
        base = froms[-1]
    elif positionals:
        base = positionals[0]
    else:
        raise UnsupportedCommand("в команде uv tool install не указан пакет")
    return [base, *_values(options, "--with", "-w")]


def _parse_pip_install(
    tokens: Sequence[str], value_flags: frozenset[str]
) -> list[str]:
    _, positionals = _scan(tokens, value_flags=value_flags, file_flags=_PIP_FILE_FLAGS)
    if not positionals:
        raise UnsupportedCommand(
            "pip install без пакетов — установка по проекту, вне v1"
        )
    return positionals


def _check_registry_spec(ecosystem: Ecosystem, spec: str) -> None:
    s = spec.strip()
    bad = s.startswith(_NON_REGISTRY_PREFIXES) or "://" in s or "\\" in s
    if ecosystem == "npm":
        bad = bad or ("/" in s and not s.startswith("@")) or s.count("/") > 1
    else:
        bad = bad or "/" in s or " @ " in s or s.endswith((".whl", ".tar.gz", ".zip"))
    if bad:
        raise UnsupportedCommand(
            f"пакет {spec!r} не из реестра (путь, git или URL) — вне v1"
        )
