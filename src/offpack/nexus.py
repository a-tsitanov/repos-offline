"""Клиент Nexus Repository: проверка наличия, загрузка, dist-tag."""

from __future__ import annotations

import base64
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

from offpack.errors import OffpackError
from offpack.pkgmeta import normalize_pypi_name


class NexusError(OffpackError):
    """Ошибка обращения к Nexus."""


class NexusAuthError(NexusError):
    """Nexus отклонил учётные данные или не хватает прав."""


class _LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self.hrefs.extend(value for key, value in attrs if key == "href" and value)


@dataclass
class NexusClient:
    base_url: str
    user: str | None = None
    password: str | None = None
    timeout: float = 120.0

    def check(self) -> None:
        status, _ = self._request("GET", "/service/rest/v1/status")
        if status != 200:
            raise NexusError(f"Nexus не готов: /service/rest/v1/status ответил {status}")

    def npm_packument(self, repo: str, name: str) -> dict | None:
        status, payload = self._request("GET", f"/repository/{repo}/{_npm_path(name)}")
        if status == 404:
            return None
        if status != 200:
            raise NexusError(f"packument {name}: HTTP {status}")
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise NexusError(f"packument {name}: ответ не JSON") from exc

    def pypi_filenames(self, repo: str, name: str) -> set[str]:
        path = f"/repository/{repo}/simple/{normalize_pypi_name(name)}/"
        status, payload = self._request("GET", path)
        if status == 404:
            return set()
        if status != 200:
            raise NexusError(f"simple-индекс {name}: HTTP {status}")
        parser = _LinkParser()
        parser.feed(payload.decode("utf-8", errors="replace"))
        return {
            urllib.parse.unquote(PurePosixPath(urllib.parse.urlsplit(href).path).name)
            for href in parser.hrefs
        }

    def upload(self, repo: str, ecosystem: str, path: Path) -> bool:
        """Загрузить файл: True — загружен, False — уже был в репозитории."""
        body, content_type = _multipart(f"{ecosystem}.asset", path)
        status, payload = self._request(
            "POST",
            f"/service/rest/v1/components?repository={urllib.parse.quote(repo)}",
            body=body,
            content_type=content_type,
        )
        if status in (200, 201, 204):
            return True
        text = payload.decode("utf-8", errors="replace")
        if status == 400 and "does not allow updating" in text:
            return False
        # Nexus 3.96.1: 409 «cannot be updated as asset already exists»
        if status == 409 and "already exists" in text:
            return False
        raise NexusError(f"загрузка {path.name}: HTTP {status}: {text[:500]}")

    def set_npm_latest(self, repo: str, name: str, version: str) -> None:
        status, payload = self._request(
            "PUT",
            f"/repository/{repo}/-/package/{_npm_path(name)}/dist-tags/latest",
            body=json.dumps(version).encode(),
            content_type="application/json",
        )
        if status not in (200, 201, 204):
            text = payload.decode("utf-8", errors="replace")[:300]
            raise NexusError(f"dist-tag latest для {name}: HTTP {status}: {text}")

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, bytes]:
        request = urllib.request.Request(self.base_url.rstrip("/") + path, data=body, method=method)
        if content_type:
            request.add_header("Content-Type", content_type)
        if self.user is not None:
            token = base64.b64encode(f"{self.user}:{self.password or ''}".encode()).decode()
            request.add_header("Authorization", f"Basic {token}")
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status, payload = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, payload = exc.code, exc.read()
        except OSError as exc:
            reason = getattr(exc, "reason", exc)
            raise NexusError(f"Nexus недоступен ({self.base_url}): {reason}") from exc
        if status in (401, 403):
            # причина от Nexus, например непринятая EULA в Community Edition
            reason = payload.decode("utf-8", errors="replace").strip()[:300]
            raise NexusAuthError(
                f"Nexus ответил {status} на {method} {path}: проверьте NEXUS_USER, "
                "NEXUS_PASSWORD и права пользователя" + (f" ({reason})" if reason else "")
            )
        return status, payload


def _npm_path(name: str) -> str:
    return urllib.parse.quote(name, safe="@")


def _multipart(field: str, path: Path) -> tuple[bytes, str]:
    boundary = f"offpack-{secrets.token_hex(16)}"
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    return head + path.read_bytes() + tail, f"multipart/form-data; boundary={boundary}"
