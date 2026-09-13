"""Команды проходов установки внутри песочницы."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from offpack.commands import InstallPlan
from offpack.platforms import Platform

WORK_ROOT = "/tmp/offpack"
_NPM_BASE = ("npm", "install", "--no-save", "--no-audit", "--no-fund")


@dataclass(frozen=True)
class Pass:
    name: str
    argv: tuple[str, ...]


def plan_passes(
    plan: InstallPlan, platforms: Sequence[Platform], pythons: Sequence[str]
) -> list[Pass]:
    """Разведка (скрипты включены) + матрица платформ (npm-скрипты выключены)."""
    if not plan.mapped:
        return [Pass("discovery", plan.raw)]
    specs = plan.specs
    if plan.ecosystem == "npm":
        passes = [Pass("discovery", (*_NPM_BASE, "--prefix", f"{WORK_ROOT}/discovery", *specs))]
        for platform in platforms:
            passes.append(
                Pass(
                    platform.name,
                    (
                        *_NPM_BASE,
                        "--ignore-scripts",
                        f"--os={platform.npm_os}",
                        f"--cpu={platform.npm_cpu}",
                        "--prefix",
                        f"{WORK_ROOT}/{platform.name}",
                        *specs,
                    ),
                )
            )
        return passes
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
