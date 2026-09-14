import subprocess
import sys
from pathlib import Path

import pytest

from offpack import cli
from offpack.bundle import SUMS, pack_bundle, stage_bundle
from offpack.cli import main
from offpack.errors import OffpackError
from offpack.pkgmeta import PackageFile
from offpack.signing import sign_file
from tests.helpers import make_npm_tgz
from tests.test_importer import META, make_archive


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
    assert opts.verbose is False


@pytest.mark.parametrize("flag", ["-v", "--verbose"])
def test_build_verbose_flag(monkeypatch, flag):
    seen = {}
    monkeypatch.setattr(cli, "run_build", lambda opts: seen.setdefault("opts", opts))
    assert main(["build", "--no-sign", flag, "--", "npx", "cowsay"]) == 0
    assert seen["opts"].verbose is True


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


def test_import_command(tmp_path, signing_key, fake_nexus, monkeypatch, capsys):
    key, allowed = signing_key
    archive = make_archive(tmp_path, key)
    monkeypatch.setenv("NEXUS_USER", "admin")
    monkeypatch.setenv("NEXUS_PASSWORD", "secret")
    code = main(["import", str(archive), "--nexus", fake_nexus.url, "--allowed-signers", str(allowed)])
    assert code == 0
    assert "загружено: 2" in capsys.readouterr().out
    assert len(fake_nexus.uploads) == 2


@pytest.mark.parametrize("nexus", ["nexus:8081", "nexus", "ftp://nexus", "http://"])
def test_import_rejects_bad_nexus_url(tmp_path, monkeypatch, capsys, nexus):
    monkeypatch.setattr(cli, "run_import", lambda *a, **k: pytest.fail("импорт не должен начаться"))
    code = main(["import", str(tmp_path / "a.tar.gz"), "--nexus", nexus, "--allow-unsigned"])
    assert code == 2
    assert "--nexus" in capsys.readouterr().err


def test_os_error_is_reported_without_traceback(monkeypatch, capsys):
    def fail(opts):
        raise PermissionError(13, "Permission denied", "dist")

    monkeypatch.setattr(cli, "run_build", fail)
    assert main(["build", "--no-sign", "--", "npx", "cowsay"]) == 2
    err = capsys.readouterr().err
    assert "offpack: ошибка:" in err and "Permission denied" in err
    assert "Traceback" not in err


def test_keyboard_interrupt_exits_130(monkeypatch, capsys):
    def interrupt(opts):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "run_build", interrupt)
    assert main(["build", "--no-sign", "--", "npx", "cowsay"]) == 130
    assert "прервано" in capsys.readouterr().err


def test_import_requires_trust(tmp_path, signing_key, fake_nexus, monkeypatch, capsys):
    monkeypatch.delenv("OFFPACK_ALLOWED_SIGNERS", raising=False)
    key, _ = signing_key
    code = main(["import", str(make_archive(tmp_path, key)), "--nexus", fake_nexus.url])
    assert code == 2
    assert "--allowed-signers" in capsys.readouterr().err


def test_import_output_is_escaped(tmp_path, signing_key, fake_nexus, monkeypatch, capsys):
    """Имена из манифеста (подписанного, но собранного из чужих tarball) не управляют терминалом."""
    key, allowed = signing_key
    tgz = make_npm_tgz(tmp_path / "src", "demo", "1.0.0")
    evil = PackageFile("npm", "demo\x1b[8m", "1.0.0\x1b]0;x\x07", tgz, "demo-1.0.0.tgz")
    bundle_dir = stage_bundle(tmp_path / "stage", "evil-20260913-000000", [evil], META, [])
    sign_file(bundle_dir / SUMS, key)
    archive = pack_bundle(bundle_dir, tmp_path / "out")
    monkeypatch.setenv("NEXUS_USER", "admin")
    monkeypatch.setenv("NEXUS_PASSWORD", "secret")
    code = main(["import", str(archive), "--nexus", fake_nexus.url, "--allowed-signers", str(allowed)])
    out = capsys.readouterr().out
    assert code == 0
    assert "\x1b" not in out and "\x07" not in out
    assert "demo\\x1b[8m==1.0.0\\x1b]0;x\\x07" in out
    assert out.endswith("\n")


def test_error_message_is_escaped(monkeypatch, capsys):
    def fail(opts):
        raise OffpackError("docker: плохо \x1b[2J\nвторая строка \x9b31m")

    monkeypatch.setattr(cli, "run_build", fail)
    assert main(["build", "--no-sign", "--", "npx", "cowsay"]) == 2
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\x9b" not in err
    assert err == "offpack: ошибка: docker: плохо \\x1b[2J\nвторая строка \\x9b31m\n"


def test_os_error_message_is_escaped(monkeypatch, capsys):
    def fail(opts):
        raise PermissionError(13, "Permission denied \x1b[8m")

    monkeypatch.setattr(cli, "run_build", fail)
    assert main(["build", "--no-sign", "--", "npx", "cowsay"]) == 2
    err = capsys.readouterr().err
    assert "\x1b" not in err and "\\x1b" in err
