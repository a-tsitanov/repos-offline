"""Фабрики тестовых файлов пакетов."""

import io
import json
import tarfile
from pathlib import Path


def make_npm_tgz(directory: Path, name: str, version: str, *, root: str = "package") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"name": name, "version": version}).encode()
    path = directory / f"{name.removeprefix('@').replace('/', '-')}-{version}.tgz"
    with tarfile.open(path, "w:gz") as archive:
        info = tarfile.TarInfo(f"{root}/package.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return path


def make_file(directory: Path, filename: str, content: bytes = b"data") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_bytes(content)
    return path
