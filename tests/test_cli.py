import subprocess
import sys
from pathlib import Path

import pytest

from offpack import cli
from offpack.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "offpack 0.1.0" in capsys.readouterr().out


def test_no_command_prints_help(capsys):
    assert main([]) == 2
    assert "usage: offpack" in capsys.readouterr().out


def test_module_entrypoint():
    result = subprocess.run(
        [sys.executable, "-m", "offpack", "--version"], capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "offpack 0.1.0" in result.stdout


def test_build_parses_options(monkeypatch, tmp_path):
    seen = {}

    def fake_run_build(opts):
        seen["opts"] = opts
        return tmp_path / "x.tar.gz"

    monkeypatch.setattr(cli, "run_build", fake_run_build)
    code = main(
        ["build", "--no-sign", "--platform", "win-x64", "--python", "3.11,3.12",
         "--out", str(tmp_path), "--", "npx", "-y", "cowsay"]
    )
    assert code == 0
    opts = seen["opts"]
    assert opts.command == ("npx", "-y", "cowsay")
    assert opts.sign_key is None
    assert [p.name for p in opts.platforms] == ["win-x64"]
    assert opts.pythons == ("3.11", "3.12")
    assert opts.out_dir == tmp_path


def test_build_default_sign_key_from_env(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setenv("OFFPACK_SIGN_KEY", str(tmp_path / "k"))
    monkeypatch.setattr(cli, "run_build", lambda opts: seen.setdefault("opts", opts))
    assert main(["build", "uvx", "ruff"]) == 0
    assert seen["opts"].sign_key == tmp_path / "k"
    assert seen["opts"].command == ("uvx", "ruff")


def test_build_default_sign_key_path(monkeypatch):
    monkeypatch.delenv("OFFPACK_SIGN_KEY", raising=False)
    assert cli.default_sign_key() == Path.home() / ".config" / "offpack" / "signing_key"


def test_build_without_command(capsys):
    assert main(["build", "--no-sign"]) == 2
    assert "не указана команда" in capsys.readouterr().err
