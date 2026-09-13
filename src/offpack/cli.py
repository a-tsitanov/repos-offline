"""Точка входа CLI."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from offpack import __version__
from offpack.build import BuildOptions, run_build
from offpack.errors import OffpackError
from offpack.platforms import (
    DEFAULT_PLATFORMS,
    DEFAULT_PYTHONS,
    parse_platforms,
    parse_python_versions,
)


def default_sign_key() -> Path:
    env = os.environ.get("OFFPACK_SIGN_KEY")
    return Path(env) if env else Path.home() / ".config" / "offpack" / "signing_key"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="offpack",
        description="Перенос npm/PyPI-пакетов в офлайн-Nexus через песочницу.",
    )
    parser.add_argument("--version", action="version", version=f"offpack {__version__}")
    sub = parser.add_subparsers(dest="cmd", metavar="COMMAND")
    _add_build(sub)
    return parser


def _add_build(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "build",
        help="собрать архив пакетов в песочнице (онлайн-машина)",
        description="Пример: offpack build -- npx @deepseek-ai/dsh web",
    )
    p.add_argument(
        "--platform",
        default=DEFAULT_PLATFORMS,
        help=f"целевые платформы через запятую (по умолчанию {DEFAULT_PLATFORMS})",
    )
    p.add_argument(
        "--python",
        default=DEFAULT_PYTHONS,
        help=f"версии Python через запятую (по умолчанию {DEFAULT_PYTHONS})",
    )
    p.add_argument("--out", type=Path, default=Path("dist"), help="каталог для архива")
    sign = p.add_mutually_exclusive_group()
    sign.add_argument(
        "--sign-key",
        type=Path,
        help="ключ ssh ed25519 (по умолчанию $OFFPACK_SIGN_KEY или ~/.config/offpack/signing_key)",
    )
    sign.add_argument("--no-sign", action="store_true", help="не подписывать архив")
    p.add_argument("--timeout", type=float, default=900, help="таймаут сборки, секунд")
    p.add_argument("--keep", action="store_true", help="не удалять стек после сборки")
    p.add_argument("command", nargs=argparse.REMAINDER, help="команда установки (после --)")
    p.set_defaults(func=_cmd_build)


def _cmd_build(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        raise OffpackError("не указана команда: offpack build -- npx cowsay")
    sign_key = None if args.no_sign else (args.sign_key or default_sign_key())
    archive = run_build(
        BuildOptions(
            command=tuple(command),
            platforms=parse_platforms(args.platform),
            pythons=parse_python_versions(args.python),
            out_dir=args.out,
            sign_key=sign_key,
            timeout=args.timeout,
            keep=args.keep,
        )
    )
    print(f"готово: {archive}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    try:
        return func(args)
    except OffpackError as exc:
        print(f"offpack: ошибка: {exc}", file=sys.stderr)
        return 2
