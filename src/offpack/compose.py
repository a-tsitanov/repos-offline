"""Обёртка над docker compose для стека песочницы."""

from __future__ import annotations

import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from importlib.resources import as_file, files
from pathlib import Path

from offpack.errors import OffpackError

Runner = Callable[..., subprocess.CompletedProcess]
Popen = Callable[..., subprocess.Popen]
LineHandler = Callable[[str], None]

_OK_PROBE = (
    "fetch(process.argv[1]).then(r => process.exit(r.ok ? 0 : 1), () => process.exit(1))"
)
_ANY_RESPONSE_PROBE = (
    "fetch(process.argv[1]).then(() => process.exit(0), () => process.exit(1))"
)
# сервис → (адрес, скрипт node); fetch в node не использует HTTP_PROXY
READY_PROBES = {
    "verdaccio": ("http://verdaccio:4873/-/ping", _OK_PROBE),
    "devpi": ("http://devpi:3141/+api", _OK_PROBE),
    # на запрос к себе squid отвечает ошибкой: готовность — любой HTTP-ответ
    "squid": ("http://squid:3128/", _ANY_RESPONSE_PROBE),
}
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


def _start_lines(popen: Popen, argv: list[str]) -> subprocess.Popen:
    """Процесс с объединёнными stdout и stderr, читаемыми построчно."""
    return popen(
        argv,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )


class LogFollower:
    """Фоновое чтение `docker compose logs -f`: каждая строка передаётся в on_line."""

    def __init__(self, process: subprocess.Popen, on_line: LineHandler) -> None:
        self._process = process
        self._on_line = on_line
        self._thread = threading.Thread(target=self._pump, name="offpack-logs", daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        assert self._process.stdout is not None
        for line in self._process.stdout:
            self._on_line(line.rstrip("\r\n"))

    def stop(self, timeout: float = 5.0) -> None:
        if self._process.poll() is None:
            self._process.terminate()
        try:
            self._process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()
        self._thread.join(timeout=timeout)
        if self._process.stdout is not None and not self._thread.is_alive():
            self._process.stdout.close()


class Compose:
    def __init__(
        self,
        project: str,
        assets: Path,
        runner: Runner = subprocess.run,
        popen: Popen = subprocess.Popen,
    ) -> None:
        self.project = project
        self.assets = assets
        self._run = runner
        self._popen = popen

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

    def exec_stream(
        self,
        service: str,
        argv: Sequence[str],
        on_line: LineHandler,
        timeout: float | None = None,
    ) -> int:
        """exec с построчной передачей вывода (stdout и stderr) в on_line.

        По истечении timeout процесс docker убивается и поднимается
        subprocess.TimeoutExpired. Возвращает код выхода.
        """
        process = _start_lines(self._popen, [*self._base(), "exec", "-T", service, *argv])
        expired = threading.Event()

        def kill() -> None:
            expired.set()
            process.kill()

        timer = threading.Timer(timeout, kill) if timeout is not None else None
        if timer is not None:
            timer.daemon = True
            timer.start()
        try:
            assert process.stdout is not None
            with process.stdout:
                for line in process.stdout:
                    on_line(line.rstrip("\r\n"))
            code = process.wait()
        except BaseException:
            process.kill()
            process.wait()
            raise
        finally:
            if timer is not None:
                timer.cancel()
        # процесс мог завершиться сам в момент срабатывания таймера: тогда код не от kill
        if expired.is_set() and code != 0:
            raise subprocess.TimeoutExpired(list(argv), timeout or 0)
        return code

    def follow_logs(self, services: Sequence[str], on_line: LineHandler) -> LogFollower:
        """Следить за логами сервисов в фоне; остановить — LogFollower.stop()."""
        process = _start_lines(
            self._popen,
            [*self._base(), "logs", "-f", "--no-color", "--no-log-prefix", *services],
        )
        return LogFollower(process, on_line)

    def wait_ready(self, timeout: float, interval: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        pending = dict(READY_PROBES)
        while True:
            for name, (url, script) in list(pending.items()):
                try:
                    probe = self.exec("sandbox", ["node", "-e", script, url], timeout=30)
                except subprocess.TimeoutExpired:
                    continue
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
