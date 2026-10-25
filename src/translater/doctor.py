"""Environment report for stage 0: python -m translater.doctor"""

from __future__ import annotations

import importlib.metadata
import platform
import shutil
import subprocess
import sys

PROCESS_LOOPBACK_MIN_BUILD = 20348


def main() -> int:
    print(f"Python: {sys.version.split()[0]} ({platform.architecture()[0]})")
    if sys.platform != "win32":
        print("ОС: не Windows — программа работает только в Windows.")
        return 1

    build = sys.getwindowsversion().build
    loopback = "да" if build >= PROCESS_LOOPBACK_MIN_BUILD else "нет, будет захват всего звука"
    print(f"Windows: {platform.version()} (build {build})")
    print(f"Захват звука процесса игры: {loopback}")

    for package in ("PySide6", "ctranslate2", "faster-whisper"):
        try:
            print(f"{package}: {importlib.metadata.version(package)}")
        except importlib.metadata.PackageNotFoundError:
            print(f"{package}: не установлен")

    print(f"GPU: {_nvidia_smi()}")
    try:
        import ctranslate2

        print(f"CUDA-устройств для CTranslate2: {ctranslate2.get_cuda_device_count()}")
    except ImportError:
        pass
    return 0


def _nvidia_smi() -> str:
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return "nvidia-smi не найден"
    query = "--query-gpu=name,driver_version,memory.total,memory.used"
    try:
        result = subprocess.run(
            [exe, query, "--format=csv,noheader"], capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return f"ошибка nvidia-smi: {exc}"
    return result.stdout.strip()


if __name__ == "__main__":
    raise SystemExit(main())
