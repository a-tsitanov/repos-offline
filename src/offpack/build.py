"""Сборка архива: песочница, проходы установки, сбор файлов, упаковка."""

from __future__ import annotations

import secrets
import shlex
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from offpack.bundle import (
    REPORT,
    SUMS,
    BundleMeta,
    Notice,
    bundle_label,
    pack_bundle,
    stage_bundle,
)
from offpack.collect import collect_npm, collect_pypi, parse_egress_log, sdist_only_notices
from offpack.commands import parse_command
from offpack.compose import Compose, ensure_docker, sandbox_assets
from offpack.errors import OffpackError
from offpack.passes import Pass, plan_passes
from offpack.platforms import Platform
from offpack.signing import sign_file

READY_TIMEOUT = 180.0


class BuildError(OffpackError):
    """Сборка не удалась."""


@dataclass(frozen=True)
class BuildOptions:
    command: tuple[str, ...]
    platforms: tuple[Platform, ...]
    pythons: tuple[str, ...]
    out_dir: Path
    sign_key: Path | None
    timeout: float = 900
    keep: bool = False


def run_build(
    opts: BuildOptions,
    *,
    compose_factory: Callable[[str, Path], Compose] = Compose,
    now: datetime | None = None,
    log: Callable[[str], None] = print,
) -> Path:
    plan = parse_command(opts.command)
    passes = plan_passes(plan, opts.platforms, opts.pythons)
    if opts.sign_key is not None and not opts.sign_key.is_file():
        raise BuildError(
            f"нет ключа подписи {opts.sign_key}; создайте: ssh-keygen -t ed25519 -f "
            f"{opts.sign_key} или укажите --no-sign"
        )
    ensure_docker()
    started = now or datetime.now(UTC)
    stamp = started.strftime("%Y%m%d-%H%M%S")
    basename = f"{bundle_label(plan)}-{stamp}"
    opts.out_dir.mkdir(parents=True, exist_ok=True)
    log_path = opts.out_dir / f"{basename}.log"
    deadline = time.monotonic() + opts.timeout

    with sandbox_assets() as assets, tempfile.TemporaryDirectory(prefix="offpack-") as tmp:
        work = Path(tmp)
        compose = compose_factory(f"offpack-{stamp}-{secrets.token_hex(3)}", assets)
        try:
            log("поднимаю песочницу…")
            compose.up()
            compose.wait_ready(timeout=min(READY_TIMEOUT, _remaining(deadline)))
            _run_passes(compose, passes, deadline, log_path, log)
            compose.stop()
            compose.copy_out("verdaccio", "/verdaccio/storage/data", work / "npm")
            compose.copy_out("devpi", "/data", work / "pypi")
            has_egress_log = compose.copy_out(
                "squid", "/var/log/squid/access.log", work / "access.log"
            )
        finally:
            if opts.keep:
                log(f"стек оставлен, удалить: docker compose -p {compose.project} down -v")
            else:
                compose.down()

        files = collect_npm(work / "npm") + collect_pypi(work / "pypi")
        if not files:
            raise BuildError(
                f"не собрано ни одного файла — песочница не ходила через прокси? лог: {log_path}"
            )
        notices: list[Notice] = []
        if not plan.mapped:
            notices.append(
                Notice(
                    "unmapped_command",
                    "команда не распознана: собрано только под платформу песочницы",
                )
            )
        if has_egress_log:
            egress = (work / "access.log").read_text(encoding="utf-8", errors="replace")
            notices.extend(Notice("egress", host) for host in parse_egress_log(egress))
        notices.extend(sdist_only_notices(files, opts.platforms))
        meta = BundleMeta(
            created_at=started,
            command=plan.raw,
            platforms=[p.name for p in opts.platforms],
            python_versions=opts.pythons,
        )
        bundle_dir = stage_bundle(work / "stage", basename, files, meta, notices)
        if opts.sign_key is not None:
            sign_file(bundle_dir / SUMS, opts.sign_key)
        archive = pack_bundle(bundle_dir, opts.out_dir)
        log((bundle_dir / REPORT).read_text(encoding="utf-8"))
    return archive


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise BuildError("превышен таймаут сборки")
    return left


def _run_passes(
    compose: Compose,
    passes: Sequence[Pass],
    deadline: float,
    log_path: Path,
    log: Callable[[str], None],
) -> None:
    with log_path.open("w", encoding="utf-8") as out:
        for item in passes:
            command = shlex.join(item.argv)
            log(f"проход {item.name}: {command}")
            out.write(f"=== {item.name}: {command}\n")
            out.flush()
            try:
                result = compose.exec("sandbox", item.argv, timeout=_remaining(deadline))
            except subprocess.TimeoutExpired as exc:
                raise BuildError(f"проход {item.name}: превышен таймаут; лог: {log_path}") from exc
            out.write(result.stdout or "")
            out.write(result.stderr or "")
            if result.returncode != 0:
                raise BuildError(
                    f"проход {item.name} завершился с кодом {result.returncode}; лог: {log_path}"
                )
