"""Команды проходов установки внутри песочницы."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from offpack.commands import InstallPlan
from offpack.platforms import Platform

WORK_ROOT = "/tmp/offpack"
NPM_PROJECT = f"{WORK_ROOT}/npm"
PNPM_STORE = f"{WORK_ROOT}/pnpm-store"
# Создать каталог проекта с минимальным package.json и выполнить команду в нём.
# Пути и команда передаются позиционными аргументами, без подстановки в текст скрипта.
_IN_PROJECT = 'mkdir -p "$1" && cd "$1" && printf "{}\\n" > package.json && shift && exec "$@"'


@dataclass(frozen=True)
class Pass:
    name: str
    argv: tuple[str, ...]
    workdir: str | None = None

    @property
    def command(self) -> tuple[str, ...]:
        """Что выполнить в песочнице: argv, при workdir — внутри свежего каталога проекта."""
        if self.workdir is None:
            return self.argv
        return ("sh", "-c", _IN_PROJECT, "sh", self.workdir, *self.argv)


def plan_passes(
    plan: InstallPlan, platforms: Sequence[Platform], pythons: Sequence[str]
) -> list[Pass]:
    """npm: один проход pnpm под все платформы (скрипты включены).

    PyPI: разведка (скрипты включены) + матрица платформ и версий Python.
    """
    if not plan.mapped:
        return [Pass("discovery", plan.raw)]
    specs = plan.specs
    if plan.ecosystem == "npm":
        return [Pass("npm", _pnpm_add(specs, platforms), workdir=NPM_PROJECT)]
    passes = [
        Pass("discovery", ("uv", "pip", "install", "--target", f"{WORK_ROOT}/discovery", *specs))
    ]
    for platform in platforms:
        for python in pythons:
            name = f"{platform.name}-py{python}"
            passes.append(
                Pass(
                    name,
                    (
                        "uv",
                        "pip",
                        "install",
                        "--target",
                        f"{WORK_ROOT}/{name}",
                        "--python-platform",
                        platform.uv_platform,
                        "--python-version",
                        python,
                        *specs,
                    ),
                )
            )
    return passes


def _pnpm_add(specs: Sequence[str], platforms: Sequence[Platform]) -> tuple[str, ...]:
    """pnpm add под платформу песочницы (current) и все целевые платформы.

    pnpm ставит декартово произведение --os × --cpu: лишние сочетания безвредны.
    minimum-release-age=0: у pnpm 11 по умолчанию сутки, а npm на клиенте берёт свежие версии.
    """
    oses = dict.fromkeys(["current", *(p.npm_os for p in platforms)])
    cpus = dict.fromkeys(["current", *(p.npm_cpu for p in platforms)])
    return (
        "pnpm",
        "add",
        *specs,
        *(f"--os={value}" for value in oses),
        *(f"--cpu={value}" for value in cpus),
        "--config.dangerously-allow-all-builds=true",
        "--config.minimum-release-age=0",
        "--reporter=append-only",
        "--store-dir",
        PNPM_STORE,
    )
