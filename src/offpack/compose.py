"""Обёртка над docker compose для стека песочницы."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path

from offpack.errors import OffpackError

Runner = Callable[..., subprocess.CompletedProcess]

READY_PROBES = {
    "verdaccio": "http://verdaccio:4873/-/ping",
    "devpi": "http://devpi:3141/+api",
}
_FETCH_PROBE = (
    "fetch(process.argv[1]).then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"
)
_MISSING_PATH = ("Could not find the file", "No such container:path")


class ComposeError(OffpackError):
    """docker compose завершился с ошибкой."""


@contextmanager
def sandbox_assets() -> Iterator[Path]:
    """Каталог со стеком песочницы из package data."""
    with as_file(files("offpack") / "sandbox") as path:
        yield Path(path)


def ensure_docker(runner: Runner = subprocess.run) -> None:
    try:
        result = runner(["docker", "compose", "version"], capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise ComposeError("не найден docker; нужен Docker с compose v2") from exc
    if result.returncode != 0:
        raise ComposeError(f"docker compose недоступен: {result.stderr.strip()}")


class Compose:
    def __init__(self, project: str, assets: Path, runner: Runner = subprocess.run) -> None:
        self.project = project
        self.assets = assets
        self._run = runner

    def _base(self) -> list[str]:
        return ["docker", "compose", "-p", self.project, "-f", str(self.assets / "compose.yaml")]

    def _check(self, *args: str) -> subprocess.CompletedProcess:
        result = self._run([*self._base(), *args], capture_output=True, text=True)
        if result.returncode != 0:
            raise ComposeError(
                f"docker compose {args[0]} завершился с кодом {result.returncode}: "
                f"{result.stderr.strip()[-2000:]}"
            )
        return result

    def up(self) -> None:
        self._check("up", "-d", "--build")

    def exec(
        self, service: str, argv: Sequence[str], timeout: float | None = None
    ) -> subprocess.CompletedProcess:
        return self._run(
            [*self._base(), "exec", "-T", service, *argv],
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def wait_ready(self, timeout: float, interval: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        pending = dict(READY_PROBES)
        while True:
            for name, url in list(pending.items()):
                probe = self.exec("sandbox", ["node", "-e", _FETCH_PROBE, url], timeout=30)
                if probe.returncode == 0:
                    del pending[name]
            if not pending:
                return
            if time.monotonic() >= deadline:
                raise ComposeError(f"сервисы не поднялись: {', '.join(pending)}")
            time.sleep(interval)

    def stop(self) -> None:
        self._check("stop")

    def copy_out(self, service: str, src: str, dest: Path) -> bool:
        """Скопировать путь из контейнера (в том числе остановленного)."""
        container = self._check("ps", "-a", "-q", service).stdout.strip()
        if not container:
            raise ComposeError(f"контейнер сервиса {service} не найден")
        dest.parent.mkdir(parents=True, exist_ok=True)
        result = self._run(
            ["docker", "cp", f"{container}:{src}", str(dest)], capture_output=True, text=True
        )
        if result.returncode == 0:
            return True
        if any(marker in result.stderr for marker in _MISSING_PATH):
            return False
        raise ComposeError(f"docker cp {service}:{src}: {result.stderr.strip()}")

    def down(self) -> None:
        self._run([*self._base(), "down", "-v", "--remove-orphans"], capture_output=True, text=True)
