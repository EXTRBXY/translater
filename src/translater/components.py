"""Heavy runtime components downloaded on demand: NVIDIA libraries (PyPI wheels) and models (Hugging Face)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

log = logging.getLogger(__name__)

_CHUNK = 1 << 20
_TIMEOUT_S = 60
_MARKER = "installed.json"

Progress = Callable[[int, int], None]


class ComponentError(Exception):
    pass


class DownloadCancelled(Exception):
    pass


@dataclass(frozen=True)
class RemoteFile:
    url: str
    sha256: str
    size: int
    path: str


@dataclass(frozen=True)
class Component:
    name: str
    title: str
    files: tuple[RemoteFile, ...]
    wheel_bin_dirs: tuple[str, ...] = ()

    @property
    def size(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def fingerprint(self) -> list[str]:
        return sorted(f.sha256 for f in self.files)


def _pypi(url: str, sha256: str, size: int) -> RemoteFile:
    return RemoteFile(url, sha256, size, PurePosixPath(url).name)


def _hf(repo: str, commit: str, files: dict[str, tuple[int, str]]) -> tuple[RemoteFile, ...]:
    return tuple(
        RemoteFile(f"https://huggingface.co/{repo}/resolve/{commit}/{path}", sha256, size, path)
        for path, (size, sha256) in files.items()
    )


# cuDNN must match the cudnn64_9.dll shim bundled with ctranslate2 4.8.3 (9.10.2.21).
CUDA = Component(
    name="cuda12",
    title="Библиотеки NVIDIA CUDA",
    files=(
        _pypi(
            "https://files.pythonhosted.org/packages/45/a1/a17fade6567c57452cfc8f967a40d1035bb9301db52f27808167fbb2be2f/"
            "nvidia_cublas_cu12-12.9.1.4-py3-none-win_amd64.whl",
            "1e5fee10662e6e52bd71dec533fbbd4971bb70a5f24f3bc3793e5c2e9dc640bf",
            553_153_899,
        ),
        _pypi(
            "https://files.pythonhosted.org/packages/3d/90/0bd6e586701b3a890fd38aa71c387dab4883d619d6e5ad912ccbd05bfd67/"
            "nvidia_cudnn_cu12-9.10.2.21-py3-none-win_amd64.whl",
            "c6288de7d63e6cf62988f0923f96dc339cea362decb1bf5b3141883392a7d65e",
            692_992_268,
        ),
        _pypi(
            "https://files.pythonhosted.org/packages/52/de/823919be3b9d0ccbf1f784035423c5f18f4267fb0123558d58b813c6ec86/"
            "nvidia_cuda_nvrtc_cu12-12.9.86-py3-none-win_amd64.whl",
            "72972ebdcf504d69462d3bcd67e7b81edd25d0fb85a2c46d3ea3517666636349",
            76_408_187,
        ),
    ),
    wheel_bin_dirs=("nvidia/cublas/bin/", "nvidia/cudnn/bin/", "nvidia/cuda_nvrtc/bin/"),
)

WHISPER = Component(
    name="whisper-large-v3-turbo",
    title="Распознавание речи (Whisper large-v3-turbo)",
    files=_hf(
        "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
        "0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf",
        {
            "config.json": (2263, "b0253ea6c0d3bea6b1e19e91a02acfd3b53f4467362efcb5a3e6b16c9b3a9b7e"),
            "model.bin": (1617884929, "e76620f83d5f5b69efd3d87e3dc180c1bd21df9fbebacfd4335e5e1efcc018da"),
            "preprocessor_config.json": (340, "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711"),
            "tokenizer.json": (2710337, "297b13372ac43916285644fb9687add3cc62ee2a1adb60da3dc25cc94c1871fd"),
            "vocabulary.json": (1068114, "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1"),
        },
    ),
)

MT_EN_RU = Component(
    name="quickmt-en-ru",
    title="Перевод en→ru (quickmt)",
    files=_hf(
        "quickmt/quickmt-en-ru",
        "c1d88c45631270d507ec1a5bf6da1a0813f85cc1",
        {
            "config.json": (223, "66edc5d58ceb812a41b4b784497001f815c35400884f40984b19de29e1bc3d69"),
            "model.bin": (401699775, "50494f42dc8bfde6d5ac722c9a39421d12a0e25c30b993c6377d626759674935"),
            "source_vocabulary.json": (340412, "31879e77b23566883bd5bf97578debf459db708e92765d021d3e594d7de285cc"),
            "target_vocabulary.json": (994826, "3e909e7f2fbc25aed300a9b1b7e1bf4801e0e008ca9b6c927bc77685e5e681f7"),
            "src.spm.model": (592273, "a02eda49b9085bb516903b522f5123f22a730b2be29337202ff8280b786a680e"),
            "tgt.spm.model": (730304, "b428315194a0eebf6e69c4424ea2e78a1c03e20982eccd4e6eb446ca128124ec"),
        },
    ),
)

MT_RU_EN = Component(
    name="quickmt-ru-en",
    title="Перевод ru→en (quickmt)",
    files=_hf(
        "quickmt/quickmt-ru-en",
        "00eb1d00e3157e554c3e0571f0ebf8203befa04e",
        {
            "config.json": (223, "66edc5d58ceb812a41b4b784497001f815c35400884f40984b19de29e1bc3d69"),
            "model.bin": (409915789, "b5763ea697321bcf849602ce30734f3970f9848501b87d139e34558101e16be4"),
            "source_vocabulary.json": (1627059, "b90db48777538d9fe3f3775af670d8f4586fc9332e1ece76fe173312e86386cb"),
            "target_vocabulary.json": (545846, "cdaea8601928e29874fe39f9205d8903a41389fff171512d51024afd2719fec5"),
            "src.spm.model": (1037835, "48ee9d46612f7b3f98038c2adb193693bff996a2fa7ed38d3f37502148a2592a"),
            "tgt.spm.model": (805620, "d97bf2a98454f5f5ea8231376fba1c5172d56e5454e4d310f299f10410d21629"),
        },
    ),
)


class ComponentStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path(self, component: Component) -> Path:
        return self._root / component.name

    def is_installed(self, component: Component) -> bool:
        try:
            marker = json.loads((self.path(component) / _MARKER).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return False
        return isinstance(marker, dict) and marker.get("sha256") == component.fingerprint

    def missing(self, components: tuple[Component, ...]) -> tuple[Component, ...]:
        return tuple(c for c in components if not self.is_installed(c))

    def install(self, component: Component, progress: Progress, cancel: threading.Event) -> None:
        """Downloads (resuming partial files), verifies sha256 and atomically replaces the component directory."""
        downloads = self._root / f".{component.name}.download"
        staging = self._root / f".{component.name}.new"
        downloads.mkdir(parents=True, exist_ok=True)

        done = 0
        local: list[tuple[RemoteFile, Path]] = []
        for remote in component.files:
            target = downloads / f"{remote.sha256[:16]}-{PurePosixPath(remote.path).name}"
            _download(remote, target, lambda n, base=done: progress(base + n, component.size), cancel)
            done += remote.size
            local.append((remote, target))

        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        for remote, source in local:
            if component.wheel_bin_dirs:
                _extract_dlls(source, staging, component.wheel_bin_dirs)
            else:
                destination = staging / remote.path
                destination.parent.mkdir(parents=True, exist_ok=True)
                os.replace(source, destination)
        (staging / _MARKER).write_text(json.dumps({"sha256": component.fingerprint}), encoding="utf-8")

        final = self.path(component)
        shutil.rmtree(final, ignore_errors=True)
        os.replace(staging, final)
        shutil.rmtree(downloads, ignore_errors=True)
        log.info("Component %s installed", component.name)


def _download(remote: RemoteFile, target: Path, progress: Progress, cancel: threading.Event) -> None:
    if target.exists() and _sha256(target) == remote.sha256:
        progress(remote.size)
        return
    part = target.with_name(target.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    request = urllib.request.Request(remote.url, headers={"Range": f"bytes={offset}-"} if offset else {})
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT_S) as response:
            if offset and response.status != 206:
                offset = 0
            with part.open("ab" if offset else "wb") as out:
                received = offset
                while chunk := response.read(_CHUNK):
                    if cancel.is_set():
                        raise DownloadCancelled
                    out.write(chunk)
                    received += len(chunk)
                    progress(min(received, remote.size))
    except urllib.error.HTTPError as exc:
        if exc.code == 416:  # the .part file is already complete
            pass
        else:
            raise ComponentError(f"Сервер вернул ошибку {exc.code} для {remote.path}.") from exc
    except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
        raise ComponentError(f"Нет связи с сервером загрузки: {exc}. Загрузка продолжится с места остановки.") from exc

    if _sha256(part) != remote.sha256:
        part.unlink(missing_ok=True)
        raise ComponentError(f"Файл {remote.path} повреждён при загрузке. Повторите загрузку.")
    os.replace(part, target)


def _extract_dlls(wheel: Path, destination: Path, bin_dirs: tuple[str, ...]) -> None:
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.infolist():
            name = member.filename
            if not name.lower().endswith(".dll") or not name.startswith(bin_dirs):
                continue
            with archive.open(member) as source, (destination / PurePosixPath(name).name).open("wb") as out:
                shutil.copyfileobj(source, out, _CHUNK)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()
