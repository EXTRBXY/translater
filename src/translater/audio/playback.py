"""Playback to a WASAPI output device in its native format; interruptible."""

from __future__ import annotations

import logging
import threading

import numpy as np

from translater.audio.resample import resample_clip

log = logging.getLogger(__name__)

_CHUNK_S = 0.02


class PlaybackError(Exception):
    pass


class DevicePlayer:
    """Plays mono audio on one output device: the default one, or the first whose name contains name_part."""

    def __init__(self, name_part: str | None = None) -> None:
        import pyaudiowpatch as pyaudio

        self._pyaudio = pyaudio
        self._audio = pyaudio.PyAudio()
        try:
            self._device = self._find_device(name_part)
        except LookupError:
            self._audio.terminate()
            raise
        self.name = str(self._device["name"])
        self._rate = int(self._device["defaultSampleRate"])
        self._channels = int(self._device["maxOutputChannels"])  # WASAPI shared mode: the mix format channel count
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def play(self, samples: np.ndarray, rate: int, on_complete=None) -> None:
        """Replaces whatever is playing. on_complete runs only if the clip played to the end."""
        audio = resample_clip(samples, rate, self._rate)
        frames = np.repeat(audio[:, None], self._channels, axis=1).astype(np.float32)
        with self._lock:
            self._stop_locked()
            self._stop = threading.Event()
            self._thread = threading.Thread(
                target=self._run, args=(frames, self._stop, on_complete), name="playback", daemon=True
            )
            self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()

    def close(self) -> None:
        self.stop()
        self._audio.terminate()

    def _stop_locked(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join()
        self._thread = None

    def _run(self, frames: np.ndarray, stop: threading.Event, on_complete) -> None:
        chunk = max(1, int(self._rate * _CHUNK_S))
        stream = self._audio.open(
            format=self._pyaudio.paFloat32,
            channels=self._channels,
            rate=self._rate,
            output=True,
            output_device_index=int(self._device["index"]),
            frames_per_buffer=chunk,
        )
        try:
            for start in range(0, len(frames), chunk):
                if stop.is_set():
                    return
                stream.write(frames[start : start + chunk].tobytes())
        except OSError:
            log.exception("Playback failed on %s", self.name)
            return
        finally:
            stream.stop_stream()
            stream.close()
        if on_complete is not None and not stop.is_set():
            on_complete()

    def _find_device(self, name_part: str | None) -> dict:
        wasapi = self._audio.get_host_api_info_by_type(self._pyaudio.paWASAPI)
        if name_part is None:
            return self._audio.get_device_info_by_index(int(wasapi["defaultOutputDevice"]))
        for index in range(self._audio.get_device_count()):
            device = self._audio.get_device_info_by_index(index)
            if (
                device["hostApi"] == wasapi["index"]
                and device["maxOutputChannels"] > 0
                and not device.get("isLoopbackDevice", False)
                and name_part.lower() in str(device["name"]).lower()
            ):
                return device
        raise LookupError(name_part)


def beep(rate: int = 48000, frequency: float = 880.0, seconds: float = 0.12) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    envelope = np.minimum(1.0, np.minimum(t, seconds - t) / 0.01)
    return (0.25 * envelope * np.sin(2 * np.pi * frequency * t)).astype(np.float32)
