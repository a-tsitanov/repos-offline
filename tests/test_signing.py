import pytest

from offpack.signing import SigningError, sign_file, verify_file
from tests.conftest import create_signing_key


def test_sign_and_verify(tmp_path, signing_key):
    key, allowed = signing_key
    data = tmp_path / "SHA256SUMS"
    data.write_text("abc  file\n")
    signature = sign_file(data, key)
    assert signature == tmp_path / "SHA256SUMS.sig"
    verify_file(data, signature, allowed)


def test_resign_overwrites_signature(tmp_path, signing_key):
    key, allowed = signing_key
    data = tmp_path / "SHA256SUMS"
    data.write_text("one\n")
    sign_file(data, key)
    data.write_text("two\n")
    verify_file(data, sign_file(data, key), allowed)


def test_tampered_data_fails(tmp_path, signing_key):
    key, allowed = signing_key
    data = tmp_path / "SHA256SUMS"
    data.write_text("abc\n")
    signature = sign_file(data, key)
    data.write_text("evil\n")
    with pytest.raises(SigningError):
        verify_file(data, signature, allowed)


def test_foreign_key_fails(tmp_path, signing_key):
    key, _ = signing_key
    _, other_allowed = create_signing_key(tmp_path / "other")
    data = tmp_path / "SHA256SUMS"
    data.write_text("abc\n")
    with pytest.raises(SigningError):
        verify_file(data, sign_file(data, key), other_allowed)


def test_missing_key_has_hint(tmp_path):
    data = tmp_path / "SHA256SUMS"
    data.write_text("abc\n")
    with pytest.raises(SigningError, match="ssh-keygen -t ed25519"):
        sign_file(data, tmp_path / "nope")


def test_missing_signature(tmp_path, signing_key):
    _, allowed = signing_key
    data = tmp_path / "SHA256SUMS"
    data.write_text("abc\n")
    with pytest.raises(SigningError, match="нет подписи"):
        verify_file(data, tmp_path / "SHA256SUMS.sig", allowed)
