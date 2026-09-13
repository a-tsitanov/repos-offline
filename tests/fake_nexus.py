"""Минимальный фейковый Nexus: status, packument, simple-индекс, загрузка, dist-tag."""

from __future__ import annotations

import base64
import io
import json
import tarfile
import threading
from email.parser import BytesParser
from email.policy import default as default_policy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from offpack.pkgmeta import parse_pypi_filename


class FakeNexus:
    def __init__(self, *, user="admin", password="secret", allow_dist_tags=True):
        self.user = user
        self.password = password
        self.allow_dist_tags = allow_dist_tags
        self.npm: dict[str, dict] = {}
        self.pypi: dict[str, set[str]] = {}
        self.uploads: list[tuple[str, str]] = []
        self.dist_tag_calls: list[tuple[str, str]] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._server.shutdown()
        self._server.server_close()

    def add_npm(self, name, version, *, latest=True):
        pack = self.npm.setdefault(name, {"name": name, "versions": {}, "dist-tags": {}})
        pack["versions"][version] = {"name": name, "version": version}
        if latest:
            pack["dist-tags"]["latest"] = version

    def add_pypi(self, filename):
        name, _ = parse_pypi_filename(filename)
        self.pypi.setdefault(name, set()).add(filename)

    def _handler(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def _send(self, status, body=b"", content_type="text/plain"):
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                header = self.headers.get("Authorization")
                token = base64.b64encode(f"{fake.user}:{fake.password}".encode()).decode()
                return header is None or header == f"Basic {token}"

            def _body(self):
                return self.rfile.read(int(self.headers.get("Content-Length", 0)))

            def do_GET(self):
                if not self._authorized():
                    return self._send(401)
                parts = urlsplit(self.path).path.split("/")
                if parts[1:] == ["service", "rest", "v1", "status"]:
                    return self._send(200)
                if len(parts) >= 5 and parts[1] == "repository" and parts[3] == "simple":
                    files = fake.pypi.get(parts[4])
                    if not files:
                        return self._send(404)
                    links = "".join(
                        f'<a href="../../packages/{parts[4]}/x/{f}#sha256=00">{f}</a>'
                        for f in sorted(files)
                    )
                    return self._send(
                        200, f"<html><body>{links}</body></html>".encode(), "text/html"
                    )
                if len(parts) == 4 and parts[1] == "repository":
                    pack = fake.npm.get(unquote(parts[3]))
                    if pack is None:
                        return self._send(404)
                    return self._send(200, json.dumps(pack).encode(), "application/json")
                return self._send(404)

            def do_POST(self):
                if not self._authorized():
                    return self._send(401)
                url = urlsplit(self.path)
                if url.path != "/service/rest/v1/components":
                    return self._send(404)
                repo = parse_qs(url.query)["repository"][0]
                raw = b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n"
                message = BytesParser(policy=default_policy).parsebytes(raw + self._body())
                part = next(message.iter_parts())
                field = part.get_param("name", header="content-disposition")
                filename = part.get_filename()
                data = part.get_payload(decode=True)
                if (repo, filename) in fake.uploads:
                    return self._send(
                        400,
                        f"Repository does not allow updating assets: {repo}".encode(),
                    )
                if field == "npm.asset":
                    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                        meta = json.load(archive.extractfile("package/package.json"))
                    fake.add_npm(meta["name"], meta["version"], latest=True)
                elif field == "pypi.asset":
                    fake.add_pypi(filename)
                else:
                    return self._send(400, b"unknown field")
                fake.uploads.append((repo, filename))
                return self._send(204)

            def do_PUT(self):
                if not self._authorized():
                    return self._send(401)
                parts = urlsplit(self.path).path.split("/")
                is_dist_tag = (
                    len(parts) == 8
                    and parts[1] == "repository"
                    and parts[3:5] == ["-", "package"]
                    and parts[6] == "dist-tags"
                )
                if not is_dist_tag:
                    return self._send(404)
                if not fake.allow_dist_tags:
                    return self._send(405)
                name, version = unquote(parts[5]), json.loads(self._body())
                fake.npm[name]["dist-tags"][parts[7]] = version
                fake.dist_tag_calls.append((name, version))
                return self._send(200)

        return Handler
