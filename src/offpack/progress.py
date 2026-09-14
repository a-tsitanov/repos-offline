"""Прогресс сборки: разбор логов прокси в события загрузки и форматирование вывода."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from urllib.parse import unquote, urlsplit

INDENT = " " * 8
ECOSYSTEMS = ("npm", "pypi")

# Verdaccio 6, format pretty, level http. Каждый запрос логируется по событию close
# (обычно bytes 0/0) и по res.end (итоговый размер ответа): считаем только строки
# с ненулевым размером.
_VERDACCIO_REQUEST = re.compile(
    r"http <-- (?P<status>\d{3}), user: .*?, req: '(?P<method>[A-Z]+) (?P<url>[^' ]+)',"
    r" bytes: \d+/(?P<out>\d+)"
)
_NPM_TARBALL = re.compile(r"/(?P<name>@[^/]+/[^/]+|[^@/][^/]*)/-/(?P<file>[^/]+)\.tgz")
# devpi-server: «[reqN] GET /<user>/<index>/+f/<hash>/<hash>/<файл>» в начале запроса.
_DEVPI_FILE_GET = re.compile(r"\] GET (?P<path>/\S*/\+f/\S+)\s*$")


@dataclass(frozen=True)
class DownloadEvent:
    ecosystem: str
    label: str
    size: int | None


def parse_verdaccio_line(line: str) -> DownloadEvent | None:
    """Скачивание tarball через Verdaccio; метаданные (packument) не считаются."""
    match = _VERDACCIO_REQUEST.search(line)
    if not match or match["method"] != "GET" or match["status"] != "200":
        return None
    size = int(match["out"])
    if size == 0:
        return None
    tarball = _NPM_TARBALL.fullmatch(unquote(urlsplit(match["url"]).path))
    if not tarball:
        return None
    name, file = tarball["name"], tarball["file"]
    base = name.rsplit("/", 1)[-1]
    version = file.removeprefix(f"{base}-")
    return DownloadEvent("npm", f"{name} {version}", size)


def parse_devpi_line(line: str) -> DownloadEvent | None:
    """Скачивание файла зеркала devpi (+f); simple-страницы и HEAD не считаются."""
    match = _DEVPI_FILE_GET.search(line)
    if not match:
        return None
    filename = unquote(urlsplit(match["path"]).path).rsplit("/", 1)[-1]
    if not filename:
        return None
    return DownloadEvent("pypi", filename, None)


def parse_proxy_line(line: str) -> DownloadEvent | None:
    return parse_verdaccio_line(line) or parse_devpi_line(line)


def format_duration(seconds: float) -> str:
    total = round(max(seconds, 0))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}m{secs:02d}s"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} Б"
    value = float(size)
    for unit in ("КБ", "МБ", "ГБ"):
        value /= 1024
        if value < 1024 or unit == "ГБ":
            break
    return f"{value:.1f} {unit}"


def format_download(event: DownloadEvent) -> str:
    text = f"{INDENT}↓ {event.ecosystem:<4} {event.label}"
    if event.size is not None:
        text += f"  {format_size(event.size)}"
    return text


def format_summary(stats: Mapping[str, tuple[int, int]], warnings: int) -> str:
    """Итог сборки: число файлов и размер по экосистемам, число предупреждений."""
    parts = []
    for ecosystem in ECOSYSTEMS:
        count, size = stats.get(ecosystem, (0, 0))
        parts.append(f"{ecosystem} {count} файлов {format_size(size)}")
    parts.append(f"предупреждений {warnings}")
    return " · ".join(parts)


class Progress:
    """Вывод этапов сборки с таймером; потокобезопасен (загрузки идут из потока логов)."""

    def __init__(
        self, write: Callable[[str], None], clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._write = write
        self._clock = clock
        self._started = clock()
        self._lock = threading.RLock()
        self._seen: set[tuple[str, str]] = set()
        self._pass_started = self._started
        self._count = 0
        self._bytes = 0

    def _line(self, text: str) -> None:
        with self._lock:
            self._write(text)

    def _stamp(self) -> str:
        minutes, seconds = divmod(max(int(self._clock() - self._started), 0), 60)
        return f"[{minutes:02d}:{seconds:02d}]"

    def message(self, text: str) -> None:
        self._line(f"{self._stamp()} {text}")

    @contextmanager
    def stage(self, title: str, done: str | None = None) -> Iterator[None]:
        started = self._clock()
        self.message(f"{title}…")
        try:
            yield
        except BaseException:
            self.message(f"✗ {title} ({format_duration(self._clock() - started)})")
            raise
        self.message(f"✓ {done or title} ({format_duration(self._clock() - started)})")

    def pass_started(self, name: str, command: str) -> None:
        with self._lock:
            self._pass_started = self._clock()
            self.message(f"проход {name}: {command}")

    def download(self, event: DownloadEvent) -> None:
        with self._lock:
            key = (event.ecosystem, event.label)
            if key in self._seen:
                return
            self._seen.add(key)
            self._count += 1
            self._bytes += event.size or 0
            self._write(format_download(event))

    def _take_pass(self) -> tuple[int, int, str]:
        count, size = self._count, self._bytes
        self._count = self._bytes = 0
        return count, size, format_duration(self._clock() - self._pass_started)

    def pass_finished(self, name: str) -> None:
        with self._lock:
            count, size, duration = self._take_pass()
            summary = f"+{count} файлов" + (f", {format_size(size)}" if size else "")
            self.message(f"✓ {name}: {summary} ({duration})")

    def pass_failed(self, name: str, reason: str, log_path: object) -> None:
        with self._lock:
            _, _, duration = self._take_pass()
            self.message(f"✗ {name}: {reason} ({duration}), лог: {log_path}")

    def output(self, line: str) -> None:
        """Строка вывода инструмента (режим -v)."""
        self._line(f"{INDENT}  {line}" if line.strip() else "")
