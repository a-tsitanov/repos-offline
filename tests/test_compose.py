import subprocess
import sys
import time

import pytest

from offpack.compose import Compose, ComposeError, ensure_docker, sandbox_assets


class Recorder:
    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        for marker, (code, out, err) in self.responses.items():
            if marker in argv:
                return subprocess.CompletedProcess(argv, code, out, err)
        return subprocess.CompletedProcess(argv, 0, "", "")


def test_sandbox_assets_contains_stack():
    with sandbox_assets() as path:
        for name in ("compose.yaml", "sandbox.Dockerfile", "devpi.Dockerfile",
                     "squid.Dockerfile", "verdaccio.yaml", "squid.conf"):
            assert (path / name).is_file(), name


def test_sandbox_stack_config_for_pnpm_and_progress():
    with sandbox_assets() as path:
        compose = (path / "compose.yaml").read_text()
        verdaccio = (path / "verdaccio.yaml").read_text()
        dockerfile = (path / "sandbox.Dockerfile").read_text()
    # pnpm 11 не читает npm_config_registry
    assert "pnpm_config_registry: http://verdaccio:4873/" in compose
    # на уровне http Verdaccio пишет запросы tarball: из них строится список загрузок
    assert "level: http" in verdaccio
    assert "pnpm@11.3.0" in dockerfile


def test_commands_use_project_and_file(tmp_path):
    runner = Recorder()
    compose = Compose("offpack-x", tmp_path, runner=runner)
    compose.up()
    compose.exec("sandbox", ["npm", "ping"])
    compose.down()
    base = ["docker", "compose", "-p", "offpack-x", "-f", str(tmp_path / "compose.yaml")]
    assert runner.calls == [
        [*base, "up", "-d", "--build"],
        [*base, "exec", "-T", "sandbox", "npm", "ping"],
        [*base, "down", "-v", "--remove-orphans"],
    ]


def test_up_failure_raises(tmp_path):
    runner = Recorder({"up": (1, "", "boom")})
    with pytest.raises(ComposeError, match="boom"):
        Compose("p", tmp_path, runner=runner).up()


def test_copy_out(tmp_path):
    runner = Recorder({"ps": (0, "cid123\n", "")})
    assert Compose("p", tmp_path, runner=runner).copy_out("devpi", "/data", tmp_path / "out")
    assert runner.calls[-1] == ["docker", "cp", "cid123:/data", str(tmp_path / "out")]


def test_copy_out_missing_path(tmp_path):
    runner = Recorder({
        "ps": (0, "cid123\n", ""),
        "cid123:/data": (1, "", "Error response from daemon: Could not find the file /data"),
    })
    assert Compose("p", tmp_path, runner=runner).copy_out("devpi", "/data", tmp_path / "o") is False


def test_wait_ready_times_out(tmp_path):
    runner = Recorder({"exec": (1, "", "")})
    with pytest.raises(ComposeError, match="не поднялись"):
        Compose("p", tmp_path, runner=runner).wait_ready(timeout=0, interval=0)


def test_wait_ready_waits_for_squid(tmp_path):
    squid = "http://squid:3128/"
    runner = Recorder({squid: (1, "", "")})
    with pytest.raises(ComposeError, match="не поднялись: squid$"):
        Compose("p", tmp_path, runner=runner).wait_ready(timeout=0, interval=0)
    probe = next(argv for argv in runner.calls if squid in argv)
    # squid отвечает на запрос к себе ошибкой: готовность — любой HTTP-ответ
    assert "r.ok" not in probe[-2]


def test_wait_ready_all_services_up(tmp_path):
    runner = Recorder()
    Compose("p", tmp_path, runner=runner).wait_ready(timeout=0, interval=0)
    probed = {argv[-1] for argv in runner.calls}
    assert probed == {"http://verdaccio:4873/-/ping", "http://devpi:3141/+api", "http://squid:3128/"}


def test_wait_ready_treats_probe_timeout_as_not_ready(tmp_path):
    def runner(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 30)

    with pytest.raises(ComposeError, match="не поднялись"):
        Compose("p", tmp_path, runner=runner).wait_ready(timeout=0, interval=0)


def _python_popen(script, seen):
    """popen, который вместо docker запускает python-скрипт; argv запоминается."""

    def popen(argv, **kwargs):
        seen.append((argv, kwargs))
        return subprocess.Popen([sys.executable, "-c", script], **kwargs)

    return popen


def test_exec_stream_passes_lines_and_returns_code(tmp_path):
    seen = []
    script = "import sys; print('one', flush=True); print('two', file=sys.stderr); sys.exit(3)"
    compose = Compose("p", tmp_path, popen=_python_popen(script, seen))
    lines = []
    assert compose.exec_stream("sandbox", ["pnpm", "add", "x"], lines.append, timeout=30) == 3
    assert lines == ["one", "two"]
    base = ["docker", "compose", "-p", "p", "-f", str(tmp_path / "compose.yaml")]
    assert seen[0][0] == [*base, "exec", "-T", "sandbox", "pnpm", "add", "x"]
    assert seen[0][1]["stderr"] == subprocess.STDOUT


def test_exec_stream_timeout_kills_process(tmp_path):
    script = "import time; print('started', flush=True); time.sleep(30)"
    compose = Compose("p", tmp_path, popen=_python_popen(script, []))
    lines = []
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        compose.exec_stream("sandbox", ["sleep"], lines.append, timeout=0.5)
    assert time.monotonic() - started < 10
    assert lines == ["started"]


def test_follow_logs_streams_until_stopped(tmp_path):
    seen = []
    script = (
        "import time\n"
        "print('line-a', flush=True)\n"
        "print('line-b', flush=True)\n"
        "time.sleep(30)\n"
    )
    compose = Compose("p", tmp_path, popen=_python_popen(script, seen))
    lines = []
    follower = compose.follow_logs(["verdaccio", "devpi"], lines.append)
    deadline = time.monotonic() + 10
    while len(lines) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)
    started = time.monotonic()
    follower.stop()
    assert time.monotonic() - started < 10
    assert lines == ["line-a", "line-b"]
    base = ["docker", "compose", "-p", "p", "-f", str(tmp_path / "compose.yaml")]
    assert seen[0][0] == [*base, "logs", "-f", "--no-color", "--no-log-prefix", "verdaccio", "devpi"]
    follower.stop()  # повторная остановка безопасна


def test_ensure_docker(tmp_path):
    ensure_docker(Recorder())
    with pytest.raises(ComposeError):
        ensure_docker(Recorder({"version": (1, "", "no compose")}))

    def missing(*args, **kwargs):
        raise FileNotFoundError("docker")

    with pytest.raises(ComposeError, match="не найден docker"):
        ensure_docker(missing)
