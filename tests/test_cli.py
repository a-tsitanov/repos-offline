import subprocess
import sys

import pytest

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
