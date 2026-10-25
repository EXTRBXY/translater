from __future__ import annotations

import hashlib
import http.server
import io
import threading
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from translater.components import Component, ComponentError, ComponentStore, DownloadCancelled, RemoteFile


class RangeHandler(http.server.BaseHTTPRequestHandler):
    files: dict[str, bytes] = {}
    requests: list[str | None] = []

    def do_GET(self) -> None:  # noqa: N802
        body = self.files.get(self.path)
        if body is None:
            self.send_error(404)
            return
        header = self.headers.get("Range")
        RangeHandler.requests.append(header)
        start = int(header.removeprefix("bytes=").split("-")[0]) if header else 0
        if start >= len(body):
            self.send_error(416)
            return
        self.send_response(206 if header else 200)
        self.send_header("Content-Length", str(len(body) - start))
        self.end_headers()
        self.wfile.write(body[start:])

    def log_message(self, *args) -> None:
        pass


@pytest.fixture
def server() -> Iterator[str]:
    RangeHandler.files = {}
    RangeHandler.requests = []
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RangeHandler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


def _remote(base: str, name: str, body: bytes, path: str | None = None) -> RemoteFile:
    RangeHandler.files[f"/{name}"] = body
    return RemoteFile(f"{base}/{name}", hashlib.sha256(body).hexdigest(), len(body), path or name)


def _wheel(members: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def test_model_files_install_and_marker(server: str, tmp_path: Path) -> None:
    component = Component(
        "model",
        "Model",
        (_remote(server, "a", b"weights" * 1000, "model.bin"), _remote(server, "b", b"{}", "sub/config.json")),
    )
    store = ComponentStore(tmp_path)
    progress: list[tuple[int, int]] = []

    assert not store.is_installed(component)
    store.install(component, lambda done, total: progress.append((done, total)), threading.Event())

    assert store.is_installed(component)
    assert (store.path(component) / "model.bin").read_bytes() == b"weights" * 1000
    assert (store.path(component) / "sub" / "config.json").read_bytes() == b"{}"
    assert progress[-1] == (component.size, component.size)
    assert not (tmp_path / ".model.download").exists()


def test_wheel_extracts_only_dlls_from_bin_dirs(server: str, tmp_path: Path) -> None:
    body = _wheel({"nvidia/cublas/bin/cublas64_12.dll": b"dll", "nvidia/cublas/include/x.h": b"h", "other/a.dll": b"x"})
    component = Component("cuda", "CUDA", (_remote(server, "w.whl", body),), wheel_bin_dirs=("nvidia/cublas/bin/",))
    store = ComponentStore(tmp_path)
    store.install(component, lambda *_: None, threading.Event())
    assert sorted(p.name for p in store.path(component).iterdir()) == ["cublas64_12.dll", "installed.json"]


def test_resumes_partial_download(server: str, tmp_path: Path) -> None:
    body = bytes(range(256)) * 100
    remote = _remote(server, "big", body)
    component = Component("big", "Big", (remote,))
    downloads = tmp_path / ".big.download"
    downloads.mkdir()
    (downloads / f"{remote.sha256[:16]}-big.part").write_bytes(body[:1000])

    ComponentStore(tmp_path).install(component, lambda *_: None, threading.Event())

    assert RangeHandler.requests == ["bytes=1000-"]
    assert (tmp_path / "big" / "big").read_bytes() == body


def test_corrupted_download_is_rejected(server: str, tmp_path: Path) -> None:
    remote = _remote(server, "file", b"real")
    tampered = RemoteFile(remote.url, hashlib.sha256(b"expected").hexdigest(), 4, "file")
    store = ComponentStore(tmp_path)
    with pytest.raises(ComponentError):
        store.install(Component("c", "C", (tampered,)), lambda *_: None, threading.Event())
    assert not store.is_installed(Component("c", "C", (tampered,)))


def test_cancel_stops_download(server: str, tmp_path: Path) -> None:
    component = Component("c", "C", (_remote(server, "file", b"x" * (3 << 20)),))
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(DownloadCancelled):
        ComponentStore(tmp_path).install(component, lambda *_: None, cancel)


def test_changed_manifest_requires_reinstall(server: str, tmp_path: Path) -> None:
    store = ComponentStore(tmp_path)
    store.install(Component("c", "C", (_remote(server, "v1", b"one"),)), lambda *_: None, threading.Event())
    assert not store.is_installed(Component("c", "C", (_remote(server, "v2", b"two"),)))
