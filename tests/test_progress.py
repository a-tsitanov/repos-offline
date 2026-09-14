import pytest

from offpack.progress import (
    DownloadEvent,
    Progress,
    format_download,
    format_duration,
    format_size,
    format_summary,
    parse_devpi_line,
    parse_verdaccio_line,
    sanitize,
    sanitize_text,
)

# Настоящие строки Verdaccio 6.10.3 (log: {format: pretty, level: http}).
# Запрос логируется дважды: по событию close запроса (bytes 0/0) и по res.end
# (итоговый размер ответа).
VERDACCIO_TGZ = (
    "http <-- 200, user: null(172.19.0.5), req: 'GET /esbuild/-/esbuild-0.28.2.tgz', bytes: 0/34207"
)
VERDACCIO_TGZ_EARLY = (
    "http <-- 200, user: null(172.19.0.5), req: 'GET /esbuild/-/esbuild-0.28.2.tgz', bytes: 0/0"
)
VERDACCIO_SCOPED_TGZ = (
    "http <-- 200, user: null(172.19.0.5), req: "
    "'GET /@esbuild/win32-x64/-/win32-x64-0.28.2.tgz', bytes: 0/4829680"
)
VERDACCIO_IGNORED = [
    VERDACCIO_TGZ_EARLY,
    "info <-- 172.19.0.5 requested 'GET /esbuild/-/esbuild-0.28.2.tgz'",
    "info --- making request: 'GET https://registry.npmjs.org/esbuild/-/esbuild-0.28.2.tgz'",
    "http <-- 200, user: null(172.19.0.5), req: 'GET /esbuild', bytes: 0/337391",
    "http <-- 200, user: null(172.19.0.5), req: 'GET /@esbuild%2Faix-ppc64', bytes: 0/53834",
    "http --- 200, req: 'GET https://registry.npmjs.org/@esbuild%2Faix-ppc64', bytes: 0/67797",
    "http --- 200, req: 'GET https://registry.npmjs.org/esbuild' (streaming)",
    "http <-- 404, user: null(172.19.0.5), req: 'GET /nope/-/nope-1.0.0.tgz', bytes: 0/40",
    "warn --- http address - http://0.0.0.0:4873/ - verdaccio/6.10.3",
]

# Настоящие строки devpi-server 6.20.3 (уровень INFO по умолчанию), как их отдаёт
# docker compose logs --no-log-prefix devpi. Размер файла в логе не пишется.
DEVPI_WHL = (
    "2026-09-14 08:49:44,127 INFO  [req10] GET "
    "/root/pypi/+f/815/e7be7a7806d54/idna-3.19-py3-none-any.whl"
)
DEVPI_IGNORED = [
    "2026-09-14 08:49:39,315 INFO  [req2] HEAD /root/pypi/+f/4c1/96c968874fc80/"
    "ruff-0.16.7-py3-none-manylinux_2_17_aarch64.manylinux2014_aarch64.whl",
    "2026-09-14 08:49:34,093 INFO  [req0] GET /root/pypi/+simple/ruff/",
    "2026-09-14 08:49:39,776 INFO  [req2] [Rtx1] reading remote: URL('https://files."
    "pythonhosted.org/packages/eb/2d/db16/ruff-0.16.7-py3-none-any.whl'), target root/pypi/+f/4c1/"
    "96c968874fc80/ruff-0.16.7-py3-none-any.whl",
    "2026-09-14 08:49:43,737 INFO  Client disconnected while serving /root/pypi/+f/4c1/"
    "96c968874fc80/ruff-0.16.7-py3-none-any.whl",
    "2026-09-14 08:49:44,125 INFO  [Wtx3] fswriter4: committed at 4",
    # строка Verdaccio с путём devpi внутри не должна становиться PyPI-событием
    "info <-- 172.19.0.5 requested 'GET /x] GET /root/pypi/+f/a/b/fake.whl'",
    "http <-- 404, user: null(172.19.0.5), req: 'GET /x] GET /root/pypi/+f/a/b/fake.whl', bytes: 0/9",
    # чужой индекс и хвост после пути
    "2026-09-14 08:49:44,127 INFO  [req10] GET /evil/idx/+f/815/e7/x-1-py3-none-any.whl",
    "2026-09-14 08:49:44,127 INFO  [req10] GET /root/pypi/+f/815/e7/x.whl trailing",
]


def test_verdaccio_tarball():
    assert parse_verdaccio_line(VERDACCIO_TGZ) == DownloadEvent("npm", "esbuild 0.28.2", 34207)


def test_verdaccio_scoped_tarball():
    assert parse_verdaccio_line(VERDACCIO_SCOPED_TGZ) == DownloadEvent("npm", "@esbuild/win32-x64 0.28.2", 4829680)


def test_verdaccio_encoded_scope():
    line = (
        "http <-- 200, user: null(172.19.0.5), "
        "req: 'GET /@types%2fnode/-/node-22.1.0.tgz?x=1', bytes: 0/100"
    )
    assert parse_verdaccio_line(line) == DownloadEvent("npm", "@types/node 22.1.0", 100)


@pytest.mark.parametrize(
    "line",
    [
        # поддельный хвост внутри URL: строка должна совпадать с форматом целиком
        "http <-- 200, user: null(1.2.3.4), req: 'GET /a/-/a-1.tgz', bytes: 0/5' x', bytes: 0/9",
        "xx http <-- 200, user: null(1.2.3.4), req: 'GET /a/-/a-1.tgz', bytes: 0/5",
        "2026-09-14 08:49:44,127 INFO  [req10] GET /root/pypi/+f/815/e7/a/-/a-1.tgz",
    ],
)
def test_verdaccio_requires_full_line_format(line):
    assert parse_verdaccio_line(line) is None


@pytest.mark.parametrize("line", VERDACCIO_IGNORED)
def test_verdaccio_ignores_non_downloads(line):
    assert parse_verdaccio_line(line) is None


def test_devpi_file_get():
    assert parse_devpi_line(DEVPI_WHL) == DownloadEvent("pypi", "idna-3.19-py3-none-any.whl", None)


@pytest.mark.parametrize("line", DEVPI_IGNORED)
def test_devpi_ignores_non_downloads(line):
    assert parse_devpi_line(line) is None


def test_devpi_encoded_control_characters_are_decoded_then_escaped_on_output():
    line = "2026-09-14 08:49:44,127 INFO  [req11] GET /root/pypi/+f/0/0/%1b%5b8m"
    event = parse_devpi_line(line)
    assert event == DownloadEvent("pypi", "\x1b[8m", None)
    progress, lines, _ = _progress()
    progress.download(event)
    assert lines == ["        ↓ pypi \\x1b[8m"]
    assert "\x1b" not in lines[0]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("обычный текст ✓ ↓ · │", "обычный текст ✓ ↓ · │"),
        ("\x1b[2J\x07", "\\x1b[2J\\x07"),
        ("a\rb\tc\x00", "a\\rb\\tc\\x00"),
        ("\x7f\x9b31m", "\\x7f\\x9b31m"),
        ("bidi\u202eoverride", "bidi\\u202eoverride"),
    ],
)
def test_sanitize_escapes_non_printable(text, expected):
    assert sanitize(text) == expected


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0s"), (4.4, "4s"), (59.6, "1m00s"), (62, "1m02s"), (3725, "1h02m05s")],
)
def test_format_duration(seconds, text):
    assert format_duration(seconds) == text


@pytest.mark.parametrize(
    ("size", "text"),
    [(0, "0 Б"), (512, "512 Б"), (2048, "2.0 КБ"), (10_066_329, "9.6 МБ"), (3 * 1024**3, "3.0 ГБ")],
)
def test_format_size(size, text):
    assert format_size(size) == text


def test_format_download():
    assert format_download(parse_verdaccio_line(VERDACCIO_SCOPED_TGZ)) == (
        "        ↓ npm  @esbuild/win32-x64 0.28.2  4.6 МБ"
    )
    assert format_download(parse_devpi_line(DEVPI_WHL)) == (
        "        ↓ pypi idna-3.19-py3-none-any.whl"
    )


def test_format_summary():
    assert format_summary({"npm": (507, 10_066_329)}, 2) == (
        "npm 507 файлов 9.6 МБ · pypi 0 файлов 0 Б · предупреждений 2"
    )


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


def _progress():
    lines = []
    clock = Clock()
    return Progress(lines.append, clock=clock), lines, clock


def test_stage_prints_elapsed_and_duration():
    progress, lines, clock = _progress()
    clock.now = 105
    with progress.stage("поднимаю песочницу", done="песочница поднята"):
        clock.now = 170
    assert lines == ["[00:05] поднимаю песочницу…", "[01:10] ✓ песочница поднята (1m05s)"]


def test_stage_failure_is_marked_and_reraised():
    progress, lines, clock = _progress()
    with pytest.raises(RuntimeError), progress.stage("жду сервисы"):
        clock.now = 103
        raise RuntimeError("boom")
    assert lines[-1] == "[00:03] ✗ жду сервисы (3s)"


def test_pass_summary_counts_new_downloads_only():
    progress, lines, clock = _progress()
    progress.pass_started("npm", "pnpm add esbuild")
    progress.download(parse_verdaccio_line(VERDACCIO_TGZ))
    progress.download(parse_verdaccio_line(VERDACCIO_TGZ))
    progress.download(parse_verdaccio_line(VERDACCIO_SCOPED_TGZ))
    clock.now = 162
    progress.pass_finished("npm")
    assert lines == [
        "[00:00] проход npm: pnpm add esbuild",
        "        ↓ npm  esbuild 0.28.2  33.4 КБ",
        "        ↓ npm  @esbuild/win32-x64 0.28.2  4.6 МБ",
        "[01:02] ✓ npm: +2 файлов, 4.6 МБ (1m02s)",
    ]
    progress.pass_started("again", "x")
    progress.download(parse_verdaccio_line(VERDACCIO_TGZ))
    progress.pass_finished("again")
    assert lines[-1] == "[01:02] ✓ again: +0 файлов (0s)"


def test_pass_summary_without_sizes():
    progress, lines, _ = _progress()
    progress.pass_started("linux-x64-py3.12", "uv pip install idna")
    progress.download(parse_devpi_line(DEVPI_WHL))
    progress.pass_finished("linux-x64-py3.12")
    assert lines[-1] == "[00:00] ✓ linux-x64-py3.12: +1 файлов (0s)"


def test_pass_failed():
    progress, lines, clock = _progress()
    progress.pass_started("npm", "pnpm add x")
    clock.now = 105
    progress.pass_failed("npm", "код 1", "dist/x.log")
    assert lines[-1] == "[00:05] ✗ npm: код 1 (5s), лог: dist/x.log"


def test_output_line_is_indented():
    progress, lines, _ = _progress()
    progress.output("Progress: resolved 1, reused 0, downloaded 0, added 0")
    progress.output("")
    assert lines == ["          Progress: resolved 1, reused 0, downloaded 0, added 0", ""]


def test_verbose_output_escapes_raw_escape_sequences():
    progress, lines, _ = _progress()
    progress.output("postinstall: \x1b]0;owned\x07\x1b[31mred")
    assert lines == ["          postinstall: \\x1b]0;owned\\x07\\x1b[31mred"]


def test_all_terminal_lines_are_escaped():
    progress, lines, _ = _progress()
    with progress.stage("этап \x1b[1m", done="готово \x1b[1m"):
        pass
    progress.pass_started("npm", "pnpm add \x1b[5m")
    progress.pass_finished("npm")
    progress.pass_failed("npm", "код 1", "dist/\x1bx.log")
    progress.message("итог \x9b")
    progress.block("отчёт\n  pkg\x1b[8m==1\n")
    assert not any(ch in "".join(lines) for ch in "\x1b\x9b")
    assert lines[-1] == "отчёт\n  pkg\\x1b[8m==1\n"


def test_message_has_timer():
    progress, lines, clock = _progress()
    clock.now = 3700
    progress.message("npm 1 файлов 1 Б")
    assert lines == ["[60:00] npm 1 файлов 1 Б"]


def test_sanitize_text_keeps_newlines():
    assert sanitize_text("отчёт\n  pkg\x1b[8m==1\r\n\n") == "отчёт\n  pkg\\x1b[8m==1\\r\n\n"
    assert sanitize_text("") == ""
