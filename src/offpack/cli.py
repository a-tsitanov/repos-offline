"""Точка входа CLI."""

from __future__ import annotations

import argparse
import sys

from offpack import __version__
from offpack.errors import OffpackError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="offpack",
        description="Перенос npm/PyPI-пакетов в офлайн-Nexus через песочницу.",
    )
    parser.add_argument("--version", action="version", version=f"offpack {__version__}")
    parser.add_subparsers(dest="cmd", metavar="COMMAND")
    return parser


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
