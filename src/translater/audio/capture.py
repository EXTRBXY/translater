"""WASAPI capture. Process loopback follows Microsoft's ApplicationLoopback sample (Windows build 20348+)."""

from __future__ import annotations

import ctypes
import functools
import logging
import threading
from ctypes import wintypes

import numpy as np

from translater.audio import AudioCallback
from translater.audio.resample import MonoResampler

log = logging.getLogger(__name__)


class CaptureError(Exception):
    pass


class GameAudioSource:
    """The game's own audio when it is running, otherwise everything on the default output device."""

    def __init__(self, exe: str, pid: int | None) -> None:
        self._exe = exe
        self._pid = pid
        self._inner: ProcessLoopbackSource | DeviceLoopbackSource | None = None
        self.description = DeviceLoopbackSource.description

    def start(self, on_audio: AudioCallback) -> None:
        if self._pid is not None:
            source = ProcessLoopbackSource(self._pid, self._exe)
            try:
                source.start(on_audio)
            except CaptureError:
                log.warning("Process loopback unavailable, falling back to device loopback", exc_info=True)
            else:
                self._inner, self.description = source, source.description
                return
        device = DeviceLoopbackSource()
        device.start(on_audio)
        self._inner = device
        if self._exe:
            self.description = f"весь звук системы ({self._exe} не найдена)"

    def stop(self) -> None:
        if self._inner is not None:
            self._inner.stop()
            self._inner = None


class DeviceLoopbackSource:
    """Everything played on the default output device (PyAudioWPatch WASAPI loopback)."""

    description = "весь звук системы"

    def __init__(self) -> None:
        self._audio = None
        self._stream = None

    def start(self, on_audio: AudioCallback) -> None:
        import pyaudiowpatch as pyaudio

        self._audio = pyaudio.PyAudio()
        try:
            device = self._audio.get_default_wasapi_loopback()
        except (OSError, LookupError) as exc:
            self.stop()
            raise CaptureError("Не найдено устройство вывода звука для захвата.") from exc
        channels = int(device["maxInputChannels"])
        rate = int(device["defaultSampleRate"])
        resample = MonoResampler(rate, channels)

        def callback(data, frame_count, time_info, status):
            on_audio(resample(np.frombuffer(data, dtype=np.float32)))
            return None, pyaudio.paContinue

        self._stream = self._audio.open(
            format=pyaudio.paFloat32,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=int(device["index"]),
            frames_per_buffer=rate // 50,
            stream_callback=callback,
        )
        log.info("Device loopback started: %s Hz, %s ch", rate, channels)

    def stop(self) -> None:
        stream, audio = self._stream, self._audio
        self._stream = self._audio = None
        if stream is not None:
            stream.stop_stream()
            stream.close()
        if audio is not None:
            audio.terminate()


class ProcessLoopbackSource:
    """Only the audio rendered by one process tree (the game)."""

    _RATE = 44100
    _CHANNELS = 2

    def __init__(self, pid: int, exe_name: str) -> None:
        self.description = f"звук {exe_name}"
        self._pid = pid
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._error: BaseException | None = None
        self._thread: threading.Thread | None = None

    def start(self, on_audio: AudioCallback) -> None:
        self._stop.clear()
        self._ready.clear()
        self._error = None
        self._thread = threading.Thread(target=self._run, args=(on_audio,), name="process-loopback", daemon=True)
        self._thread.start()
        self._ready.wait()
        if self._error is not None:
            self._thread.join()
            raise CaptureError(f"Не удалось захватить звук процесса: {self._error}") from self._error

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _run(self, on_audio: AudioCallback) -> None:
        import comtypes

        comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        event = _kernel32.CreateEventW(None, False, False, None)
        try:
            self._capture(on_audio, event)
        except Exception:
            log.exception("Process loopback failed")
        finally:
            _kernel32.CloseHandle(event)
            comtypes.CoUninitialize()

    def _capture(self, on_audio: AudioCallback, event: int) -> None:
        try:
            client = _activate_process_loopback(self._pid)
            capture = _start_capture(client, event, self._RATE, self._CHANNELS)
        except BaseException as exc:
            self._error = exc
            return
        finally:
            self._ready.set()
        log.info("Process loopback started for pid %s", self._pid)
        resample = MonoResampler(self._RATE, self._CHANNELS)
        block = self._CHANNELS * 2
        try:
            while not self._stop.is_set():
                if _kernel32.WaitForSingleObject(event, 100) != _WAIT_OBJECT_0:
                    continue
                while capture.GetNextPacketSize() > 0:
                    data, frames, flags, _, _ = capture.GetBuffer()
                    if flags & _AUDCLNT_BUFFERFLAGS_SILENT or not data:
                        pcm = np.zeros(frames * self._CHANNELS, dtype=np.int16)
                    else:
                        pcm = np.frombuffer(ctypes.string_at(data, frames * block), dtype=np.int16)
                    capture.ReleaseBuffer(frames)
                    on_audio(resample(pcm))
        finally:
            client.Stop()


# --- Win32 / COM declarations (mmdeviceapi.h, audioclient.h, audioclientactivationparams.h) ---

_WAIT_OBJECT_0 = 0
_AUDCLNT_SHAREMODE_SHARED = 0
_AUDCLNT_STREAMFLAGS_LOOPBACK = 0x00020000
_AUDCLNT_STREAMFLAGS_EVENTCALLBACK = 0x00040000
_AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM = 0x80000000
_AUDCLNT_BUFFERFLAGS_SILENT = 0x2
_AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK = 1
_PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE = 0
_VT_BLOB = 65
_WAVE_FORMAT_PCM = 1
_BUFFER_DURATION_100NS = 200_000
_ACTIVATION_TIMEOUT_S = 5.0
_VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK = "VAD\\Process_Loopback"

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_kernel32.CreateEventW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
_kernel32.CreateEventW.restype = wintypes.HANDLE
_kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
_kernel32.WaitForSingleObject.restype = wintypes.DWORD
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL


class _ProcessLoopbackParams(ctypes.Structure):
    _fields_ = [("TargetProcessId", wintypes.DWORD), ("ProcessLoopbackMode", ctypes.c_int)]


class _ActivationParams(ctypes.Structure):
    _fields_ = [("ActivationType", ctypes.c_int), ("ProcessLoopbackParams", _ProcessLoopbackParams)]


class _PropVariantBlob(ctypes.Structure):
    _fields_ = [
        ("vt", ctypes.c_ushort),
        ("wReserved1", ctypes.c_ushort),
        ("wReserved2", ctypes.c_ushort),
        ("wReserved3", ctypes.c_ushort),
        ("cbSize", wintypes.ULONG),
        ("pBlobData", ctypes.c_void_p),
    ]


class _WaveFormatEx(ctypes.Structure):
    _fields_ = [
        ("wFormatTag", wintypes.WORD),
        ("nChannels", wintypes.WORD),
        ("nSamplesPerSec", wintypes.DWORD),
        ("nAvgBytesPerSec", wintypes.DWORD),
        ("nBlockAlign", wintypes.WORD),
        ("wBitsPerSample", wintypes.WORD),
        ("cbSize", wintypes.WORD),
    ]


@functools.cache
def _com_types():
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class IAudioCaptureClient(IUnknown):
        _iid_ = GUID("{C8ADBD64-E71E-48A0-A4DE-185C395CD317}")
        _methods_ = [
            COMMETHOD(
                [],
                HRESULT,
                "GetBuffer",
                (["out"], ctypes.POINTER(ctypes.c_void_p), "ppData"),
                (["out"], ctypes.POINTER(ctypes.c_uint32), "pNumFramesToRead"),
                (["out"], ctypes.POINTER(wintypes.DWORD), "pdwFlags"),
                (["out"], ctypes.POINTER(ctypes.c_uint64), "pu64DevicePosition"),
                (["out"], ctypes.POINTER(ctypes.c_uint64), "pu64QPCPosition"),
            ),
            COMMETHOD([], HRESULT, "ReleaseBuffer", (["in"], ctypes.c_uint32, "NumFramesRead")),
            COMMETHOD([], HRESULT, "GetNextPacketSize", (["out"], ctypes.POINTER(ctypes.c_uint32), "pNumFrames")),
        ]

    class IAudioClient(IUnknown):
        _iid_ = GUID("{1CB9AD4C-DBFA-4C32-B178-C2F568A703B2}")
        _methods_ = [
            COMMETHOD(
                [],
                HRESULT,
                "Initialize",
                (["in"], ctypes.c_int, "ShareMode"),
                (["in"], wintypes.DWORD, "StreamFlags"),
                (["in"], ctypes.c_int64, "hnsBufferDuration"),
                (["in"], ctypes.c_int64, "hnsPeriodicity"),
                (["in"], ctypes.POINTER(_WaveFormatEx), "pFormat"),
                (["in"], ctypes.POINTER(GUID), "AudioSessionGuid"),
            ),
            COMMETHOD([], HRESULT, "GetBufferSize", (["out"], ctypes.POINTER(ctypes.c_uint32), "pNumBufferFrames")),
            COMMETHOD([], HRESULT, "GetStreamLatency", (["out"], ctypes.POINTER(ctypes.c_int64), "phnsLatency")),
            COMMETHOD([], HRESULT, "GetCurrentPadding", (["out"], ctypes.POINTER(ctypes.c_uint32), "pNumPadding")),
            COMMETHOD(
                [],
                HRESULT,
                "IsFormatSupported",
                (["in"], ctypes.c_int, "ShareMode"),
                (["in"], ctypes.POINTER(_WaveFormatEx), "pFormat"),
                (["out"], ctypes.POINTER(ctypes.POINTER(_WaveFormatEx)), "ppClosestMatch"),
            ),
            COMMETHOD(
                [], HRESULT, "GetMixFormat", (["out"], ctypes.POINTER(ctypes.POINTER(_WaveFormatEx)), "ppFormat")
            ),
            COMMETHOD(
                [],
                HRESULT,
                "GetDevicePeriod",
                (["out"], ctypes.POINTER(ctypes.c_int64), "phnsDefault"),
                (["out"], ctypes.POINTER(ctypes.c_int64), "phnsMinimum"),
            ),
            COMMETHOD([], HRESULT, "Start"),
            COMMETHOD([], HRESULT, "Stop"),
            COMMETHOD([], HRESULT, "Reset"),
            COMMETHOD([], HRESULT, "SetEventHandle", (["in"], wintypes.HANDLE, "eventHandle")),
            COMMETHOD(
                [],
                HRESULT,
                "GetService",
                (["in"], ctypes.POINTER(GUID), "riid"),
                (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "ppv"),
            ),
        ]

    class IActivateAudioInterfaceAsyncOperation(IUnknown):
        _iid_ = GUID("{72A22D78-CDE4-431D-B8CC-843A71199B6D}")
        _methods_ = [
            COMMETHOD(
                [],
                HRESULT,
                "GetActivateResult",
                (["out"], ctypes.POINTER(HRESULT), "activateResult"),
                (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "activatedInterface"),
            ),
        ]

    class IActivateAudioInterfaceCompletionHandler(IUnknown):
        _iid_ = GUID("{41D949AB-9862-444A-80F6-C261334DA5EB}")
        _methods_ = [
            COMMETHOD(
                [],
                HRESULT,
                "ActivateCompleted",
                (["in"], ctypes.POINTER(IActivateAudioInterfaceAsyncOperation), "activateOperation"),
            ),
        ]

    class IAgileObject(IUnknown):
        _iid_ = GUID("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")
        _methods_ = []

    return (
        IAudioClient,
        IAudioCaptureClient,
        IActivateAudioInterfaceAsyncOperation,
        (
            IActivateAudioInterfaceCompletionHandler,
            IAgileObject,
        ),
    )


def _activate_process_loopback(pid: int):
    import comtypes
    from comtypes import COMObject

    IAudioClient, _, IActivateAudioInterfaceAsyncOperation, handler_interfaces = _com_types()
    done = threading.Event()

    class CompletionHandler(COMObject):
        _com_interfaces_ = list(handler_interfaces)

        def ActivateCompleted(self, this, operation):  # called on an MTA worker thread
            done.set()
            return 0

    params = _ActivationParams(
        _AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK,
        _ProcessLoopbackParams(pid, _PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE),
    )
    variant = _PropVariantBlob(vt=_VT_BLOB, cbSize=ctypes.sizeof(params), pBlobData=ctypes.addressof(params))
    handler = CompletionHandler()
    operation = ctypes.POINTER(IActivateAudioInterfaceAsyncOperation)()

    activate = ctypes.WinDLL("Mmdevapi").ActivateAudioInterfaceAsync
    activate.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(comtypes.GUID),
        ctypes.POINTER(_PropVariantBlob),
        ctypes.POINTER(handler_interfaces[0]),
        ctypes.POINTER(ctypes.POINTER(IActivateAudioInterfaceAsyncOperation)),
    ]
    activate.restype = comtypes.HRESULT
    activate(
        _VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,
        ctypes.byref(IAudioClient._iid_),
        ctypes.byref(variant),
        handler.QueryInterface(handler_interfaces[0]),
        ctypes.byref(operation),
    )
    if not done.wait(_ACTIVATION_TIMEOUT_S):
        raise CaptureError("Windows не ответила на запрос захвата звука процесса.")
    result, unknown = operation.GetActivateResult()
    if result < 0:
        raise CaptureError(f"Windows отказала в захвате звука процесса (HRESULT 0x{result & 0xFFFFFFFF:08X}).")
    return unknown.QueryInterface(IAudioClient)


def _start_capture(client, event: int, rate: int, channels: int):
    _, IAudioCaptureClient, _, _ = _com_types()
    block_align = channels * 2
    fmt = _WaveFormatEx(_WAVE_FORMAT_PCM, channels, rate, rate * block_align, block_align, 16, 0)
    client.Initialize(
        _AUDCLNT_SHAREMODE_SHARED,
        _AUDCLNT_STREAMFLAGS_LOOPBACK | _AUDCLNT_STREAMFLAGS_EVENTCALLBACK | _AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM,
        _BUFFER_DURATION_100NS,
        0,
        ctypes.byref(fmt),
        None,
    )
    client.SetEventHandle(event)
    capture = client.GetService(ctypes.byref(IAudioCaptureClient._iid_)).QueryInterface(IAudioCaptureClient)
    client.Start()
    return capture
