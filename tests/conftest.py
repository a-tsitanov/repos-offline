import subprocess
from pathlib import Path

import pytest


def create_signing_key(directory: Path) -> tuple[Path, Path]:
    """Ключ ed25519 без пароля и allowed_signers с principal offpack."""
    directory.mkdir(parents=True, exist_ok=True)
    key = directory / "signing_key"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "offpack-test", "-f", str(key)],
        check=True,
    )
    key_type, key_body = (directory / "signing_key.pub").read_text().split()[:2]
    allowed = directory / "allowed_signers"
    allowed.write_text(f"offpack {key_type} {key_body}\n")
    return key, allowed


@pytest.fixture
def signing_key(tmp_path) -> tuple[Path, Path]:
    return create_signing_key(tmp_path / "keys")
