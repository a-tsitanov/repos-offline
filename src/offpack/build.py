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
from functools import partial
from pathlib import Path

from offpack.bundle import (
    MANIFEST,
    REPORT,
    SUMS,
    BundleMeta,
    Manifest,
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
from offpack.progress import Progress, format_duration, format_summary, parse_proxy_line
from offpack.signing import sign_file

READY_TIMEOUT = 180.0
PROXY_SERVICES = ("verdaccio", "devpi")
# Пауза перед итогом прохода: последние строки логов прокси доходят через docker с задержкой.
PROXY_LOG_SETTLE = 0.3


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
    verbose: bool = False


def _print(text: str) -> None:
    print(text, flush=True)


def run_build(
    opts: BuildOptions,
    *,
    compose_factory: Callable[[str, Path], Compose] = Compose,
    now: datetime | None = None,
    log: Callable[[str], None] = _print,
) -> Path:
    plan = parse_command(opts.command)
    passes = plan_passes(plan, opts.platforms, opts.pythons)
    if opts.sign_key is not None and not opts.sign_key.is_file():
        raise BuildError(
            f"нет ключа подписи {opts.sign_key}; создайте: ssh-keygen -t ed25519 -f "
            f"{opts.sign_key} или укажите --no-sign"
        )
    ensure_docker()
    progress = Progress(log)
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
            with progress.stage(
                "поднимаю песочницу (docker compose up --build)", done="песочница поднята"
            ):
                compose.up()
            with progress.stage("жду готовности verdaccio, devpi, squid", done="сервисы готовы"):
                compose.wait_ready(timeout=min(READY_TIMEOUT, _remaining(deadline)))
            follower = compose.follow_logs(PROXY_SERVICES, partial(_on_proxy_line, progress))
            try:
                _run_passes(compose, passes, deadline, log_path, progress, opts.verbose)
            finally:
                follower.stop()
            with progress.stage("копирую файлы из хранилищ прокси", done="файлы собраны"):
                compose.stop()
                compose.copy_out("verdaccio", "/verdaccio/storage/data", work / "npm")
                compose.copy_out("devpi", "/data", work / "pypi")
                has_egress_log = compose.copy_out(
                    "squid", "/var/log/squid/access.log", work / "access.log"
                )
        finally:
            if opts.keep:
                progress.message(
                    f"стек оставлен, удалить: docker compose -p {compose.project} down -v"
                )
            else:
                with progress.stage("удаляю стек песочницы", done="стек удалён"):
                    compose.down()

        with progress.stage("формирую манифест и контрольные суммы", done="манифест готов"):
            files = collect_npm(work / "npm") + collect_pypi(work / "pypi")
            if not files:
                raise BuildError(
                    "не собрано ни одного файла — песочница не ходила через прокси?"
                    f" лог: {log_path}"
                )
            notices = _notices(plan.mapped, has_egress_log, work / "access.log")
            notices.extend(sdist_only_notices(files, opts.platforms))
            meta = BundleMeta(
                created_at=started,
                command=plan.raw,
                platforms=[p.name for p in opts.platforms],
                python_versions=opts.pythons,
            )
            bundle_dir = stage_bundle(work / "stage", basename, files, meta, notices)
        if opts.sign_key is not None:
            with progress.stage("подписываю SHA256SUMS", done="подписано"):
                sign_file(bundle_dir / SUMS, opts.sign_key)
        with progress.stage("упаковываю архив", done="архив упакован"):
            archive = pack_bundle(bundle_dir, opts.out_dir)
        manifest = Manifest.from_json((bundle_dir / MANIFEST).read_text(encoding="utf-8"))
        progress.message(format_summary(_stats(manifest), len(manifest.warnings)))
        log((bundle_dir / REPORT).read_text(encoding="utf-8"))
    return archive


def _notices(mapped: bool, has_egress_log: bool, access_log: Path) -> list[Notice]:
    notices: list[Notice] = []
    if not mapped:
        notices.append(
            Notice(
                "unmapped_command",
                "команда не распознана: собрано только под платформу песочницы",
            )
        )
    if has_egress_log:
        egress = access_log.read_text(encoding="utf-8", errors="replace")
        notices.extend(Notice("egress", host) for host in parse_egress_log(egress))
    else:
        notices.append(
            Notice(
                "egress_log_missing",
                "не удалось получить access.log squid: внешние загрузки не проверены",
            )
        )
    return notices


def _stats(manifest: Manifest) -> dict[str, tuple[int, int]]:
    stats: dict[str, tuple[int, int]] = {}
    for entry in manifest.files:
        count, size = stats.get(entry.ecosystem, (0, 0))
        stats[entry.ecosystem] = (count + 1, size + entry.size)
    return stats


def _on_proxy_line(progress: Progress, line: str) -> None:
    event = parse_proxy_line(line)
    if event is not None:
        progress.download(event)


def _remaining(deadline: float) -> float:
    left = deadline - time.monotonic()
    if left <= 0:
        raise BuildError("превышен таймаут сборки")
    return left


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _run_passes(
    compose: Compose,
    passes: Sequence[Pass],
    deadline: float,
    log_path: Path,
    progress: Progress,
    verbose: bool,
) -> None:
    """Проходы по очереди; вывод пишется в лог построчно по мере поступления."""
    with log_path.open("w", encoding="utf-8") as out:

        def on_line(line: str) -> None:
            out.write(line + "\n")
            out.flush()
            if verbose:
                progress.output(line)

        for item in passes:
            command = shlex.join(item.argv)
            progress.pass_started(item.name, command)
            where = f" (каталог {item.workdir})" if item.workdir else ""
            out.write(f"=== [{_now()}] {item.name}: {command}{where}\n")
            out.flush()
            started = time.monotonic()
            try:
                code = compose.exec_stream(
                    "sandbox", item.command, on_line, timeout=_remaining(deadline)
                )
            except subprocess.TimeoutExpired as exc:
                out.write(f"=== [{_now()}] {item.name}: превышен таймаут\n")
                progress.pass_failed(item.name, "превышен таймаут", log_path)
                raise BuildError(f"проход {item.name}: превышен таймаут; лог: {log_path}") from exc
            duration = format_duration(time.monotonic() - started)
            out.write(f"=== [{_now()}] {item.name}: код {code} ({duration})\n")
            out.flush()
            time.sleep(PROXY_LOG_SETTLE)
            if code != 0:
                progress.pass_failed(item.name, f"код {code}", log_path)
                raise BuildError(
                    f"проход {item.name} завершился с кодом {code}; лог: {log_path}"
                )
            progress.pass_finished(item.name)
