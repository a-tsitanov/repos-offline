import pytest

from offpack.nexus import NexusAuthError, NexusClient, NexusError
from tests.helpers import make_file, make_npm_tgz


@pytest.fixture
def client(fake_nexus):
    return NexusClient(fake_nexus.url, "admin", "secret", timeout=5)


def test_check(client):
    client.check()


def test_wrong_password(fake_nexus):
    with pytest.raises(NexusAuthError):
        NexusClient(fake_nexus.url, "admin", "wrong", timeout=5).check()


def test_unreachable():
    with pytest.raises(NexusError, match="недоступен"):
        NexusClient("http://127.0.0.1:9", timeout=2).check()


def test_npm_packument(client, fake_nexus):
    assert client.npm_packument("npm-hosted", "@s/pkg") is None
    fake_nexus.add_npm("@s/pkg", "1.0.0")
    pack = client.npm_packument("npm-hosted", "@s/pkg")
    assert set(pack["versions"]) == {"1.0.0"}


def test_pypi_filenames(client, fake_nexus):
    assert client.pypi_filenames("pypi-hosted", "Ruff") == set()
    fake_nexus.add_pypi("ruff-0.6.0-py3-none-win_amd64.whl")
    assert client.pypi_filenames("pypi-hosted", "Ruff") == {"ruff-0.6.0-py3-none-win_amd64.whl"}


def test_upload_npm_twice(client, fake_nexus, tmp_path):
    tgz = make_npm_tgz(tmp_path, "@s/pkg", "1.2.0")
    assert client.upload("npm-hosted", "npm", tgz) is True
    assert client.upload("npm-hosted", "npm", tgz) is False
    assert fake_nexus.npm["@s/pkg"]["dist-tags"]["latest"] == "1.2.0"


def test_upload_pypi(client, fake_nexus, tmp_path):
    whl = make_file(tmp_path, "six-1.16.0-py2.py3-none-any.whl", b"\x00\x01binary")
    assert client.upload("pypi-hosted", "pypi", whl) is True
    assert fake_nexus.pypi["six"] == {whl.name}
    assert fake_nexus.uploads == [("pypi-hosted", whl.name)]


def test_set_npm_latest(client, fake_nexus):
    fake_nexus.add_npm("@s/pkg", "2.0.0")
    fake_nexus.add_npm("@s/pkg", "1.0.0")
    client.set_npm_latest("npm-hosted", "@s/pkg", "2.0.0")
    assert fake_nexus.npm["@s/pkg"]["dist-tags"]["latest"] == "2.0.0"


def test_set_npm_latest_unsupported(fake_nexus):
    fake_nexus.allow_dist_tags = False
    fake_nexus.add_npm("pkg", "1.0.0")
    with pytest.raises(NexusError):
        NexusClient(fake_nexus.url, "admin", "secret", timeout=5).set_npm_latest(
            "npm-hosted", "pkg", "1.0.0"
        )
