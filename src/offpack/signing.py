"""Подпись и проверка SHA256SUMS через ssh-keygen -Y."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from offpack.errors import OffpackError

NAMESPACE = "offpack"
PRINCIPAL = "offpack"


class SigningError(OffpackError):
    """Подпись не создана или не прошла проверку."""


def _ssh_keygen() -> str:
    path = shutil.which("ssh-keygen")
    if path is None:
        raise SigningError("не найден ssh-keygen (OpenSSH)")
    return path


def sign_file(path: Path, key: Path) -> Path:
    if not key.is_file():
        raise SigningError(
            f"нет ключа подписи {key}; создайте: ssh-keygen -t ed25519 -f {key}"
        )
    signature = path.with_name(path.name + ".sig")
    signature.unlink(missing_ok=True)
    result = subprocess.run(
        [_ssh_keygen(), "-Y", "sign", "-f", str(key), "-n", NAMESPACE, str(path)],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0 or not signature.is_file():
        raise SigningError(f"ssh-keygen не подписал {path.name}: {result.stderr.strip()}")
    return signature


def verify_file(path: Path, signature: Path, allowed_signers: Path) -> None:
    if not signature.is_file():
        raise SigningError(f"нет подписи {signature.name}")
    if not allowed_signers.is_file():
        raise SigningError(f"нет файла allowed_signers: {allowed_signers}")
    with path.open("rb") as data:
        result = subprocess.run(
            [
                _ssh_keygen(),
                "-Y",
                "verify",
                "-f",
                str(allowed_signers),
                "-I",
                PRINCIPAL,
                "-n",
                NAMESPACE,
                "-s",
                str(signature),
            ],
            stdin=data,
            capture_output=True,
        )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).decode("utf-8", errors="replace").strip()
        raise SigningError(f"подпись {path.name} не прошла проверку: {message}")
