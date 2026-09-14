"""E2E: сборка → импорт в настоящий Nexus → установка из Nexus без интернета.

Запуск вручную: uv run pytest -m nexus -v   (старт Nexus занимает 2–5 минут)
Образ Nexus можно переопределить: OFFPACK_E2E_NEXUS_IMAGE=sonatype/nexus3:<tag>
"""

import base64
import json
import os
import secrets
import subprocess
import time
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from offpack.build import BuildOptions, run_build
from offpack.bundle import extract_bundle
from offpack.importer import ImportOptions, run_import
from offpack.nexus import NexusClient, NexusError
from offpack.platforms import parse_platforms

pytestmark = [pytest.mark.docker, pytest.mark.nexus]

NEXUS_IMAGE = os.environ.get("OFFPACK_E2E_NEXUS_IMAGE", "sonatype/nexus3:3.96.1-01")


def _docker(*args, check=True):
    result = subprocess.run(["docker", *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)}: {result.stderr}")
    return result


def _api(url, method, path, password, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url + path, method=method, data=data)
    request.add_header("Content-Type", "application/json")
    token = base64.b64encode(f"admin:{password}".encode()).decode()
    request.add_header("Authorization", f"Basic {token}")
    with urllib.request.urlopen(request, timeout=60) as response:
        assert response.status in (200, 201, 204)
        return response.read()


def _accept_eula(url, password):
    """Community Edition (3.96.1-01) без принятой EULA отвечает 403 на загрузку компонентов."""
    try:
        eula = json.loads(_api(url, "GET", "/service/rest/v1/system/eula", password))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:  # старые версии Nexus: EULA через API не требуется
            return
        raise
    if not eula["accepted"]:
        _api(url, "POST", "/service/rest/v1/system/eula", password, {**eula, "accepted": True})


def _wait(probe, timeout, what):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = probe()
        if value:
            return value
        time.sleep(5)
    raise TimeoutError(what)


@pytest.fixture(scope="module")
def nexus():
    name = f"offpack-e2e-{secrets.token_hex(3)}"
    network = f"{name}-internal"
    _docker("network", "create", "--internal", network)
    _docker("run", "-d", "--name", name, "-p", "127.0.0.1::8081", NEXUS_IMAGE)
    try:
        _docker("network", "connect", "--alias", "nexus", network, name)
        port = _docker("port", name, "8081/tcp").stdout.splitlines()[0].rsplit(":", 1)[1]
        url = f"http://127.0.0.1:{port}"

        def writable():
            try:
                with urllib.request.urlopen(url + "/service/rest/v1/status/writable", timeout=5) as r:
                    return r.status == 200
            except OSError:
                return False

        _wait(writable, 600, "Nexus не поднялся")
        password = _wait(
            lambda: _docker("exec", name, "cat", "/nexus-data/admin.password", check=False).stdout.strip(),
            120,
            "нет /nexus-data/admin.password",
        )
        _accept_eula(url, password)
        storage = {"blobStoreName": "default", "strictContentTypeValidation": True, "writePolicy": "allow_once"}
        for fmt, repo in (("npm", "npm-hosted"), ("pypi", "pypi-hosted")):
            _api(url, "POST", f"/service/rest/v1/repositories/{fmt}/hosted", password,
                 {"name": repo, "online": True, "storage": storage})
        _api(url, "PUT", "/service/rest/v1/security/anonymous", password,
             {"enabled": True, "userId": "anonymous", "realmName": "NexusAuthorizingRealm"})
        yield SimpleNamespace(url=url, password=password, network=network)
    finally:
        _docker("rm", "-f", name, check=False)
        _docker("network", "rm", network, check=False)


def _build(tmp_path, key, command):
    return run_build(
        BuildOptions(
            command=command,
            platforms=parse_platforms("linux-x64,win-x64"),
            pythons=("3.12",),
            out_dir=tmp_path / "dist",
            sign_key=key,
            timeout=1800,
        )
    )


def _offline(network, script):
    """Команда в образе песочницы, где сеть — только до Nexus."""
    return _docker("run", "--rm", "--network", network, "offpack-sandbox:local", "sh", "-c", script, check=False)


def test_npm_roundtrip(nexus, tmp_path, signing_key):
    key, allowed = signing_key
    archive = _build(tmp_path, key, ("npx", "esbuild", "--version"))
    client = NexusClient(nexus.url, "admin", nexus.password)
    first = run_import(ImportOptions(archive, allowed_signers=allowed), client)
    assert first.ok and first.uploaded, first.render(dry_run=False)
    second = run_import(ImportOptions(archive, allowed_signers=allowed), client)
    assert second.ok and not second.uploaded and second.skipped
    result = _offline(
        nexus.network,
        "npm install --no-save --no-audit --prefix /tmp/t esbuild "
        "--registry http://nexus:8081/repository/npm-hosted/ "
        "&& /tmp/t/node_modules/.bin/esbuild --version",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_npm_dist_tag_api(nexus):
    client = NexusClient(nexus.url, "admin", nexus.password)
    pack = client.npm_packument("npm-hosted", "esbuild")
    if pack is None:
        pytest.skip("сначала должен пройти test_npm_roundtrip")
    try:
        client.set_npm_latest("npm-hosted", "esbuild", pack["dist-tags"]["latest"])
    except NexusError as exc:
        pytest.xfail(f"dist-tag API в этой версии Nexus не поддерживается: {exc}")


def test_pypi_roundtrip(nexus, tmp_path, signing_key):
    key, allowed = signing_key
    archive = _build(tmp_path, key, ("uv", "tool", "install", "ruff"))
    client = NexusClient(nexus.url, "admin", nexus.password)
    report = run_import(ImportOptions(archive, allowed_signers=allowed), client)
    assert report.ok and report.uploaded, report.render(dry_run=False)
    wheel = next((extract_bundle(archive, tmp_path / "x") / "pypi").iterdir())
    assert client.upload("pypi-hosted", "pypi", wheel) is False, "ожидался 409 already exists"
    result = _offline(
        nexus.network,
        "uv pip install --target /tmp/t --index-url http://nexus:8081/repository/pypi-hosted/simple ruff "
        "&& /tmp/t/bin/ruff --version",
    )
    assert result.returncode == 0, result.stdout + result.stderr
