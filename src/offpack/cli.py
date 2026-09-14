"""Точка входа CLI."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

from offpack import __version__
from offpack.build import BuildOptions, run_build
from offpack.errors import OffpackError
from offpack.importer import ImportOptions, run_import
from offpack.nexus import NexusClient
from offpack.platforms import (
    DEFAULT_PLATFORMS,
    DEFAULT_PYTHONS,
    parse_platforms,
    parse_python_versions,
)
from offpack.progress import sanitize_text


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
    _add_import(sub)
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
    p.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="показывать вывод pnpm/uv по мере выполнения проходов",
    )
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
            verbose=args.verbose,
        )
    )
    print(f"готово: {archive}")
    return 0


def _add_import(sub: argparse._SubParsersAction) -> None:
    p = sub.add_parser(
        "import",
        help="импортировать архив в Nexus (офлайн)",
        description="Учётные данные: переменные NEXUS_USER и NEXUS_PASSWORD.",
    )
    p.add_argument("archive", type=Path, help="архив offpack .tar.gz")
    p.add_argument("--nexus", required=True, help="адрес Nexus, например http://nexus:8081")
    p.add_argument("--npm-repo", default="npm-hosted", help="hosted npm-репозиторий")
    p.add_argument("--pypi-repo", default="pypi-hosted", help="hosted PyPI-репозиторий")
    trust = p.add_mutually_exclusive_group()
    trust.add_argument(
        "--allowed-signers",
        type=Path,
        help="файл allowed_signers OpenSSH (по умолчанию $OFFPACK_ALLOWED_SIGNERS)",
    )
    trust.add_argument("--allow-unsigned", action="store_true", help="не проверять подпись")
    p.add_argument("--dry-run", action="store_true", help="только показать, что будет загружено")
    p.set_defaults(func=_cmd_import)


def _check_nexus_url(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise OffpackError(
            f"--nexus: нужен адрес вида http://nexus:8081 или https://nexus.example,"
            f" получено {url!r}"
        )


def _cmd_import(args: argparse.Namespace) -> int:
    _check_nexus_url(args.nexus)
    allowed = args.allowed_signers
    if allowed is None and not args.allow_unsigned and os.environ.get("OFFPACK_ALLOWED_SIGNERS"):
        allowed = Path(os.environ["OFFPACK_ALLOWED_SIGNERS"])
    client = NexusClient(
        args.nexus,
        user=os.environ.get("NEXUS_USER"),
        password=os.environ.get("NEXUS_PASSWORD"),
    )
    report = run_import(
        ImportOptions(
            archive=args.archive,
            npm_repo=args.npm_repo,
            pypi_repo=args.pypi_repo,
            allowed_signers=allowed,
            allow_unsigned=args.allow_unsigned,
            dry_run=args.dry_run,
        ),
        client,
    )
    # имена и версии из манифеста и ответы Nexus — чужие данные: экранируем
    print(sanitize_text(report.render(dry_run=args.dry_run)), end="")
    return 0 if report.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = getattr(args, "func", None)
    if func is None:
        parser.print_help()
        return 2
    try:
        return func(args)
    except (OffpackError, OSError) as exc:
        # в тексте бывают имена файлов, stderr docker и ответы Nexus
        print(f"offpack: ошибка: {sanitize_text(str(exc))}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("offpack: прервано", file=sys.stderr)
        return 130
