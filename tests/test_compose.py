import subprocess

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


def test_ensure_docker(tmp_path):
    ensure_docker(Recorder())
    with pytest.raises(ComposeError):
        ensure_docker(Recorder({"version": (1, "", "no compose")}))

    def missing(*args, **kwargs):
        raise FileNotFoundError("docker")

    with pytest.raises(ComposeError, match="не найден docker"):
        ensure_docker(missing)
