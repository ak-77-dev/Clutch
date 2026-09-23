"""Replay buffer, recording and screenshots.

Video: one long-running FFmpeg process grabs the desktop with the Desktop
Duplication API (``ddagrab``), encodes on the GPU (NVENC / AMF / Quick Sync in
H.264, HEVC or AV1; x264 / x265 on the CPU as a fallback) and writes 1-second
MPEG-TS segments into a spool folder.

Audio: WASAPI loopback (what you hear) and optionally the microphone are
written to the same spool as raw PCM, with silence filled in by wall clock:
Windows delivers no loopback packets while nothing is playing, and without the
fill a quiet stretch would pull every later sound out of sync. A watchdog
reopens the streams when the default device changes (Bluetooth headsets switch
endpoints whenever a mic turns on) or a stream stops delivering, because a
WASAPI stream on an invalidated device just goes quiet without an error.

A clip is "the last N seconds of segments + the matching PCM", stitched with a
stream copy (no video re-encode). Segments are hard-linked, not copied, so a
60 s clip of a fast game (hundreds of MB) saves as quickly as a short one.
Recording is the same thing with pruning paused from the moment you start.
"""

from __future__ import annotations

import array
import contextlib
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SEGMENT_S = 1.0
KEEP_MARGIN_S = 6.0
# A segment file appears after its first frame is captured and encoded, so its
# timestamp runs late by the pipeline delay. Measured with a flash + click test
# (NVENC, 60 fps): audio led video by 40-57 ms without this correction. Encoder
# options that buffer frames (lookahead, temporal AQ) would make that delay
# large and variable, so video_args never enables them.
AV_OFFSET_S = 0.045
# Stream-copied ADTS carries no priming info, so a decoded segment plays its input this
# much late: encoder delay + decoder overlap, 2048 samples (measured: 41.4 ms onset shift).
AAC_PRIMING_S = 2048 / 48_000
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
OUT_RATE, OUT_CHANNELS = 48_000, 2  # every clip's audio, whatever the device format

# vendor -> codec -> FFmpeg encoder. "x264" is the CPU path (the name predates HEVC support).
VENDOR_CODECS: dict[str, dict[str, str]] = {
    "nvenc": {"h264": "h264_nvenc", "hevc": "hevc_nvenc", "av1": "av1_nvenc"},
    "amf": {"h264": "h264_amf", "hevc": "hevc_amf", "av1": "av1_amf"},
    "qsv": {"h264": "h264_qsv", "hevc": "hevc_qsv", "av1": "av1_qsv"},
    "x264": {"h264": "libx264", "hevc": "libx265"},  # CPU AV1 can't keep up with 60 fps
}
GPU_FRAMES = {"nvenc": True, "amf": True, "qsv": False, "x264": False}  # take ddagrab's D3D11 frames directly
# Constant-quality targets (lower = better). "high" is visually lossless for most games.
QUALITY = {"low": 28, "medium": 24, "high": 20, "ultra": 17, "max": 14}
PRESETS = {"speed": "p3", "balanced": "p5", "quality": "p7"}  # NVENC effort; mapped per vendor below
RESOLUTIONS = {"native": None, "1440": 1440, "1080": 1080, "720": 720}


def ffmpeg_exe() -> str:
    if os.environ.get("CLUTCH_FFMPEG"):
        return os.environ["CLUTCH_FFMPEG"]
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg  # ships a static FFmpeg 7 build with ddagrab + hardware encoders

    return imageio_ffmpeg.get_ffmpeg_exe()


def run_ffmpeg(args: list[str], timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        creationflags=CREATE_NO_WINDOW,
    )


def _run_ffmpeg_fed(args: list[str], feed: list[Path], timeout: float = 300) -> subprocess.CompletedProcess:
    """Run FFmpeg with the given files streamed, back to back, into ``pipe:0``."""
    proc = subprocess.Popen(
        [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", "-y", *args],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=CREATE_NO_WINDOW,
    )

    def pump() -> None:
        try:
            for path in feed:
                with open(path, "rb") as fh:
                    shutil.copyfileobj(fh, proc.stdin, 1 << 20)
        except (OSError, ValueError):
            pass  # FFmpeg exited early; its stderr says why
        finally:
            with contextlib.suppress(OSError):
                proc.stdin.close()

    # (not communicate(): it closes stdin straight away, before the feeder writes anything)
    errors: list[bytes] = []
    drain = threading.Thread(target=lambda: errors.append(proc.stderr.read()), name="clutch-mux-err", daemon=True)
    feeder = threading.Thread(target=pump, name="clutch-mux-feed", daemon=True)
    drain.start()
    feeder.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
    feeder.join(timeout=5)
    drain.join(timeout=5)
    return subprocess.CompletedProcess(proc.args, proc.returncode, "", b"".join(errors).decode(errors="replace"))


class _KillOnCloseJob:  # pragma: no cover - Windows kernel objects
    """A Windows Job Object that kills its processes when Clutch's backend dies.

    FFmpeg records until told to stop. If the backend crashes or is killed, a
    plain child process would keep recording (and filling the disk) forever; a
    job with KILL_ON_JOB_CLOSE is torn down by Windows along with our handle.
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.CreateJobObjectW.restype = wintypes.HANDLE
        self._k32 = k32
        self.handle = k32.CreateJobObjectW(None, None)

        class Basic(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_uint64) for n in ("r", "w", "o", "rb", "wb", "ob")]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", Basic),
                ("IoInfo", Io),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        k32.SetInformationJobObject(self.handle, 9, ctypes.byref(info), ctypes.sizeof(info))  # 9 = ExtendedLimitInformation

    def add(self, proc: subprocess.Popen) -> None:
        self._k32.AssignProcessToJobObject(self.handle, int(proc._handle))  # type: ignore[attr-defined]


_job: _KillOnCloseJob | None = None


def _bind_to_backend(proc: subprocess.Popen) -> None:
    """Make Windows kill ``proc`` if this process dies without stopping it."""
    global _job
    if os.name != "nt":
        return
    try:  # pragma: no cover - Windows only
        _job = _job or _KillOnCloseJob()
        _job.add(proc)
    except Exception:
        pass  # best effort: stale-recorder cleanup at startup is the fallback


def kill_stale_recorders(spool_root: Path) -> int:
    """Stop FFmpeg processes still writing into Clutch's spool from a run that crashed."""
    try:
        import psutil
    except ImportError:
        return 0
    root = os.path.normcase(str(spool_root))
    killed = 0
    for proc in psutil.process_iter(["name", "cmdline"]):
        try:
            name = (proc.info.get("name") or "").lower()
            cmd = " ".join(proc.info.get("cmdline") or [])
            if name.startswith("ffmpeg") and root in os.path.normcase(cmd):
                proc.kill()
                killed += 1
        except (psutil.Error, OSError):
            continue
    return killed


# ── encoders ─────────────────────────────────────────────────────────────────

_probe_cache: dict[str, bool] = {}
_probe_lock = threading.Lock()


def encoder_works(ffmpeg_encoder: str) -> bool:
    """Whether an encoder actually initializes here (builds list NVENC even without an NVIDIA GPU)."""
    with _probe_lock:
        if ffmpeg_encoder not in _probe_cache:
            probe = run_ffmpeg(["-f", "lavfi", "-i", "color=black:s=1280x720:d=0.2", "-c:v", ffmpeg_encoder, "-f", "null", "-"], timeout=20)
            _probe_cache[ffmpeg_encoder] = probe.returncode == 0
        return _probe_cache[ffmpeg_encoder]


def capabilities() -> dict[str, dict[str, bool]]:
    """vendor -> codec -> usable on this PC."""
    return {vendor: {codec: encoder_works(enc) for codec, enc in codecs.items()} for vendor, codecs in VENDOR_CODECS.items()}


def pick_encoder(preference: str = "auto", codec: str = "h264") -> str:
    """The vendor to encode ``codec`` with: the preferred one if it works, else the first GPU that does, else the CPU."""
    order = [preference] if preference in VENDOR_CODECS else []
    order += [v for v in ("nvenc", "amf", "qsv", "x264") if v not in order]
    for vendor in order:
        enc = VENDOR_CODECS[vendor].get(codec)
        if enc and encoder_works(enc):
            return vendor
    return "x264"


def resolve_codec(vendor: str, codec: str) -> str:
    """The codec actually used: CPU can't do real-time AV1, so it drops to HEVC."""
    return codec if codec in VENDOR_CODECS[vendor] else "hevc"


def video_args(vendor: str, codec: str, cfg: CaptureConfig) -> list[str]:
    """Encoder arguments for the chosen vendor, codec, quality and effort."""
    enc = VENDOR_CODECS[vendor][codec]
    cq = QUALITY.get(cfg.quality, QUALITY["high"])
    bitrate = cfg.rate_control == "bitrate"
    kbps = int(cfg.bitrate_mbps * 1000)
    target = ["-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.5)}k", "-bufsize", f"{kbps * 2}k"]
    if vendor == "nvenc":
        args = ["-preset", PRESETS.get(cfg.preset, "p5"), "-tune", "hq", "-rc", "vbr"]
        args += target if bitrate else ["-cq", str(cq), "-b:v", "0"]
        # Spatial AQ spends bits where the eye notices (flat skies, faces). Temporal AQ and
        # lookahead are left off: they make NVENC hold frames back, which breaks A/V timing.
        args += ["-spatial-aq", "1", "-aq-strength", "8", "-rc-lookahead", "0"]
        if cfg.preset == "quality":
            args += ["-multipass", "qres"]
    elif vendor == "amf":
        args = ["-quality", {"speed": "speed", "balanced": "balanced", "quality": "quality"}.get(cfg.preset, "balanced")]
        args += ["-rc", "vbr_peak", *target] if bitrate else ["-rc", "qvbr", "-qvbr_quality_level", str(max(1, 51 - cq))]
    elif vendor == "qsv":
        args = ["-preset", {"speed": "veryfast", "balanced": "medium", "quality": "slower"}.get(cfg.preset, "medium")]
        args += target if bitrate else ["-global_quality", str(cq)]
    else:
        args = ["-preset", {"speed": "ultrafast", "balanced": "veryfast", "quality": "faster"}.get(cfg.preset, "veryfast")]
        args += target if bitrate else ["-crf", str(cq + (2 if codec == "hevc" else 0))]
    return ["-c:v", enc, *args]


def media_info(path: Path) -> dict[str, Any]:
    """Duration and resolution from FFmpeg's stream banner (no ffprobe in the bundled build)."""
    proc = subprocess.run([ffmpeg_exe(), "-hide_banner", "-i", str(path)], capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
    text = proc.stderr
    info: dict[str, Any] = {"duration": None, "width": None, "height": None, "has_audio": "Audio:" in text}
    if m := re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", text):
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    if m := re.search(r"Video:.*?(\d{2,5})x(\d{2,5})", text):
        info["width"], info["height"] = int(m.group(1)), int(m.group(2))
    return info


# ── audio devices ────────────────────────────────────────────────────────────


def default_endpoint_ids() -> tuple[str | None, str | None]:  # pragma: no cover - Windows Core Audio
    """IDs of Windows' current default output and input devices (they change on BT profile switches)."""
    if os.name != "nt":
        return None, None
    import ctypes
    from ctypes import POINTER, byref, c_void_p, c_wchar_p, wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]

    ole32 = ctypes.WinDLL("ole32")
    ole32.CoInitializeEx(None, 0)  # multithreaded; harmless if already initialized

    def guid(text: str) -> GUID:
        g = GUID()
        ole32.CLSIDFromString(c_wchar_p(text), byref(g))
        return g

    enum = c_void_p()
    hr = ole32.CoCreateInstance(
        byref(guid("{BCDE0395-E52F-467C-8E3D-C4579291692E}")),
        None,
        0x17,
        byref(guid("{A95664D2-9614-4F35-A746-DE8DB63617E6}")),
        byref(enum),
    )
    if hr != 0 or not enum:
        return None, None

    def method(obj: c_void_p, index: int, *argtypes):
        vtbl = ctypes.cast(obj, POINTER(POINTER(c_void_p))).contents
        return ctypes.WINFUNCTYPE(ctypes.c_long, c_void_p, *argtypes)(vtbl[index])

    def endpoint(flow: int) -> str | None:
        dev = c_void_p()
        # IMMDeviceEnumerator::GetDefaultAudioEndpoint(flow, eMultimedia)
        if method(enum, 4, ctypes.c_int, ctypes.c_int, POINTER(c_void_p))(enum, flow, 1, byref(dev)) != 0 or not dev:
            return None
        ident = c_wchar_p()
        try:
            if method(dev, 5, POINTER(c_wchar_p))(dev, byref(ident)) != 0:  # IMMDevice::GetId
                return None
            value = ident.value
            ole32.CoTaskMemFree(ident)
            return value
        finally:
            method(dev, 2)(dev)  # Release

    try:
        return endpoint(0), endpoint(1)  # eRender, eCapture
    finally:
        method(enum, 2)(enum)


def list_audio_devices() -> dict[str, list[str]]:  # pragma: no cover - audio hardware
    """Names the player can pick in Settings (output = loopback sources, input = microphones)."""
    try:
        import pyaudiowpatch as pa
    except ImportError:
        return {"outputs": [], "inputs": []}
    p = pa.PyAudio()
    try:
        outputs = sorted({d["name"].replace(" [Loopback]", "") for d in p.get_loopback_device_info_generator()})
        wasapi = p.get_host_api_info_by_type(pa.paWASAPI)["index"]
        inputs = sorted(
            {
                d["name"]
                for i in range(p.get_device_count())
                if (d := p.get_device_info_by_index(i))["hostApi"] == wasapi and d["maxInputChannels"] > 0 and not d.get("isLoopbackDevice")
            }
        )
        return {"outputs": outputs, "inputs": inputs}
    finally:
        p.terminate()


def _resolve_device(p: Any, kind: str, preference: str) -> dict[str, Any] | None:  # pragma: no cover - audio hardware
    import pyaudiowpatch as pa

    if kind == "system":
        if preference and preference != "default":
            for d in p.get_loopback_device_info_generator():
                if d["name"].replace(" [Loopback]", "") == preference:
                    return d
        return p.get_default_wasapi_loopback()
    wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
    if preference and preference != "default":
        for i in range(p.get_device_count()):
            d = p.get_device_info_by_index(i)
            if d["hostApi"] == wasapi["index"] and d["name"] == preference and d["maxInputChannels"] > 0:
                return d
    return p.get_device_info_by_index(wasapi["defaultInputDevice"])


# ── audio timeline ───────────────────────────────────────────────────────────


def _to_output_format(data: bytes, rate: int, channels: int) -> bytes:
    """16-bit PCM at any rate / channel count -> 48 kHz stereo (nearest-neighbour; only for odd devices)."""
    if rate == OUT_RATE and channels == OUT_CHANNELS:
        return data
    src = array.array("h", data)
    frames = len(src) // channels
    left = src[0::channels][:frames]
    right = src[1::channels][:frames] if channels > 1 else left
    n_out = int(frames * OUT_RATE / rate)
    step = rate / OUT_RATE
    out = array.array("h", bytes(n_out * 4))
    idx = [min(frames - 1, int(i * step)) for i in range(n_out)] if frames else []
    out[0::2] = array.array("h", (left[i] for i in idx))
    out[1::2] = array.array("h", (right[i] for i in idx))
    return out.tobytes()


class LiveAacEncoder:
    """Encodes a track's PCM timeline to AAC as it's captured, in ~1 s ADTS segments.

    Saving a clip then only stream-copies audio that's already encoded, instead
    of encoding a minute of audio after the key press. One encoder covers one
    continuous run of the timeline (``base`` = wall time of its first sample);
    a discontinuity (device format change, clock resync) starts a new run.
    """

    def __init__(self, spool: Path, name: str, base: float, rate: int, channels: int, kbps: int) -> None:
        self.base = base
        self.prefix = f"aenc_{name}_{int(base * 1000)}"
        self.spool = spool
        self.csv = spool / f"{self.prefix}.csv"
        self.ok = True
        self.fed = 0.0  # seconds of audio handed to the encoder
        self.rate = rate
        self.frame_bytes = 2 * channels
        self.proc = subprocess.Popen(
            [
                ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                "-f", "s16le", "-ar", str(rate), "-ac", str(channels), "-i", "pipe:0",
                "-ar", str(OUT_RATE), "-ac", str(OUT_CHANNELS), "-c:a", "aac", "-b:a", f"{kbps}k",
                # flush_packets: write every AAC frame to disk at once, or the newest second of audio
                # sits in FFmpeg's buffer when a clip is saved and the clip comes out short.
                "-f", "segment", "-segment_time", "1", "-segment_format", "adts", "-segment_format_options", "flush_packets=1",
                "-segment_list", str(self.csv), "-segment_list_type", "csv", "-segment_list_flags", "live",
                str(spool / f"{self.prefix}_%06d.aac"),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=CREATE_NO_WINDOW,
        )  # fmt: skip
        _bind_to_backend(self.proc)

    def feed(self, data: bytes) -> None:
        if not self.ok:
            return
        try:
            self.proc.stdin.write(data)
            self.fed += len(data) / self.frame_bytes / self.rate
        except (OSError, ValueError):
            self.ok = False  # saves fall back to encoding from the PCM spool

    def segments(self) -> list[tuple[Path, float, float | None]]:
        """(file, start, end) in seconds since ``base``; the last one may still be growing (end None)."""
        rows: list[tuple[Path, float, float | None]] = []
        try:
            for line in self.csv.read_text(encoding="utf-8").splitlines():
                name, start, end = line.rsplit(",", 2)
                rows.append((self.spool / name, float(start), float(end)))
        except (OSError, ValueError):
            rows = []
        nxt = self.spool / f"{self.prefix}_{len(rows):06d}.aac"
        if nxt.exists():
            rows.append((nxt, rows[-1][2] if rows else 0.0, None))
        return rows

    def prune(self, older_than: float) -> None:
        for path, _start, end in self.segments()[:-2]:
            if end is not None and self.base + end < older_than:
                path.unlink(missing_ok=True)

    def close(self) -> None:
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except Exception:
            self.proc.kill()


class AudioTrack:
    """Spools one audio source to disk as raw PCM on a wall-clock timeline.

    Files are ``<name>_<start epoch ms>_<rate>_<channels>.pcm``; each holds a
    continuous run of 16-bit samples, and a gap between chunks is filled with
    silence, so sample ``i`` of a file always plays at ``start + i / rate``.
    The source device can change under a track (see :class:`AudioCapture`):
    the timeline carries on and a new file starts if the format changes.
    """

    FILE_S = 30.0

    def __init__(self, name: str, spool: Path, rate: int = OUT_RATE, channels: int = OUT_CHANNELS, live_kbps: int | None = None) -> None:
        self.name, self.spool = name, spool
        self.rate, self.channels = rate, channels
        self.live_kbps = live_kbps  # encode to AAC while capturing (fast saves)
        self.encoder: LiveAacEncoder | None = None
        self.old_encoders: list[LiveAacEncoder] = []
        self.q: queue.Queue[tuple[float, bytes] | None] = queue.Queue()
        self.device: str | None = None
        self.last_packet = 0.0
        self.opened_at = 0.0
        self._fh = None
        self._file_start = 0.0
        self._cursor = 0.0  # wall time of the next sample to write
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name=f"clutch-audio-{name}", daemon=True)
        self._stream = None

    @property
    def frame_bytes(self) -> int:
        return 2 * self.channels

    # stream plumbing -------------------------------------------------------
    def attach(self, p: Any, device: dict[str, Any]) -> None:  # pragma: no cover - needs audio hardware
        import pyaudiowpatch as pa

        if not self._thread.is_alive():
            self._thread.start()  # the writer keeps the timeline (and silence fill) going across reattaches
        rate, channels = int(device["defaultSampleRate"]), max(1, min(2, int(device["maxInputChannels"])))
        with self._lock:
            if (rate, channels) != (self.rate, self.channels):
                self.rate, self.channels = rate, channels
                if self._fh:  # the next write starts a file in the new format
                    self._fh.close()
                    self._fh = None

        def callback(data, frames, _info, _status):
            self.last_packet = time.time()
            self.q.put((self.last_packet, data))
            return (None, pa.paContinue)

        self._stream = p.open(
            format=pa.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=device["index"],
            frames_per_buffer=int(rate * 0.02),
            stream_callback=callback,
        )
        self.device = device["name"].replace(" [Loopback]", "")
        self.opened_at = time.time()

    def detach(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:  # pragma: no cover - needs audio hardware
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

    def close(self) -> None:
        self.detach()
        self.q.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None
            for enc in [self.encoder, *self.old_encoders]:
                if enc:
                    enc.close()
            self.encoder, self.old_encoders = None, []

    def live_window(self, a0: float) -> tuple[list[Path], float] | None:
        """Already-encoded AAC covering ``a0`` onward: (whole segments, shift in seconds).

        Stream copy can't cut inside an AAC frame, so whole segments are used
        and the audio track is shifted by ``shift`` (<= 0: it starts a little
        before the video). The MP4 edit list hides the lead-in, keeping sync
        sample-accurate. None when the encoder can't cover the window (started
        after ``a0``, broke, or pruned): the caller encodes from the PCM spool.
        """
        with self._lock:
            enc = self.encoder
            if not enc or not enc.ok or enc.base > a0 + 0.03:
                return None
            with contextlib.suppress(OSError, ValueError):
                enc.proc.stdin.flush()  # push what's captured so far to the encoder
        rel = a0 - enc.base
        # A segment's decoded audio is its input played AAC_PRIMING_S late.
        segs = [s for s in enc.segments() if s[2] is None or s[2] - AAC_PRIMING_S > rel]
        if not segs or segs[0][1] - AAC_PRIMING_S > rel + 0.03:
            return None
        return [s[0] for s in segs], enc.base + segs[0][1] - AAC_PRIMING_S - a0

    def _run(self) -> None:
        while True:
            try:
                item = self.q.get(timeout=0.25)
            except queue.Empty:
                self.fill_silence(time.time() - 0.15)  # nothing playing: keep the timeline moving
                continue
            if item is None:
                return
            self.write(*item)

    # timeline ---------------------------------------------------------------
    def write(self, arrived: float, data: bytes) -> None:
        with self._lock:
            n = len(data) // self.frame_bytes
            begins = arrived - n / self.rate
            if self._fh is None:
                self._new_file(begins)
            elif begins - self._cursor > 0.04:  # a gap bigger than two buffers: silence
                self._pad(begins - self._cursor)
            elif self._cursor - begins > 0.5:  # clock drifted far ahead of the device: resync
                self._new_file(begins)
            self._emit(data)
            self._cursor += n / self.rate
            if self._cursor - self._file_start > self.FILE_S:
                self._new_file(self._cursor, continuous=True)

    def _emit(self, data: bytes) -> None:
        self._fh.write(data)
        if self.encoder:
            self.encoder.feed(data)

    def fill_silence(self, until: float) -> None:
        with self._lock:
            if self._fh is None:
                self._new_file(until)
                return
            if until - self._cursor > 0.04:
                self._pad(until - self._cursor)
                if self._cursor - self._file_start > self.FILE_S:
                    self._new_file(self._cursor, continuous=True)

    def _pad(self, seconds: float) -> None:
        frames = int(seconds * self.rate)
        self._emit(b"\x00" * frames * self.frame_bytes)
        self._cursor += frames / self.rate

    def _new_file(self, start: float, continuous: bool = False) -> None:
        if self._fh:
            self._fh.close()
        if not continuous and self.live_kbps:
            # A break in the timeline: the live encoder's run ends here and a new one starts.
            if self.encoder:
                self.encoder.close()
                self.old_encoders.append(self.encoder)
            self.encoder = LiveAacEncoder(self.spool, self.name, start, self.rate, self.channels, self.live_kbps)
        self._file_start = self._cursor = start
        path = self.spool / f"{self.name}_{int(start * 1000)}_{self.rate}_{self.channels}.pcm"
        self._fh = open(path, "wb")  # noqa: SIM115 - long-lived handle

    def flush(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.flush()

    # extraction -------------------------------------------------------------
    def files(self) -> list[tuple[float, int, int, Path]]:
        """(start, rate, channels, path), oldest first."""
        out = []
        for p in self.spool.glob(f"{self.name}_*.pcm"):
            parts = p.stem.split("_")
            try:
                start, rate, ch = int(parts[-3]) / 1000, int(parts[-2]), int(parts[-1])
            except (ValueError, IndexError):
                continue
            out.append((start, rate, ch, p))
        return sorted(out, key=lambda f: f[0])

    def extract(self, t0: float, t1: float, dest: Path) -> Path:
        """Write ``[t0, t1)`` of the timeline as a 48 kHz stereo WAV (silence where nothing was captured)."""
        self.flush()
        out_frame = 2 * OUT_CHANNELS
        buf = bytearray(int((t1 - t0) * OUT_RATE) * out_frame)
        for start, rate, ch, path in self.files():
            try:
                data = path.read_bytes()
            except OSError:
                continue
            fb = 2 * ch
            frames = len(data) // fb
            lo, hi = max(t0, start), min(t1, start + frames / rate)
            if hi <= lo:
                continue
            chunk = _to_output_format(data[int((lo - start) * rate) * fb : int((hi - start) * rate) * fb], rate, ch)
            at = int((lo - t0) * OUT_RATE) * out_frame
            chunk = chunk[: max(0, len(buf) - at)]
            buf[at : at + len(chunk)] = chunk
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(OUT_CHANNELS)
            w.setsampwidth(2)
            w.setframerate(OUT_RATE)
            w.writeframes(bytes(buf))
        return dest

    def prune(self, older_than: float) -> None:
        for enc in [self.encoder, *self.old_encoders]:
            if enc:
                enc.prune(older_than)
        files = self.files()
        for i, (_start, _rate, _ch, path) in enumerate(files):
            nxt = files[i + 1][0] if i + 1 < len(files) else None
            if nxt is not None and nxt < older_than:  # the whole file ends before the cutoff
                path.unlink(missing_ok=True)


class AudioCapture:
    """Owns the audio tracks and keeps their streams alive.

    PortAudio enumerates devices once per initialization, so following a new
    default device means tearing every stream down and re-initializing. The
    timelines (and their silence fill) keep running across a restart.
    """

    STALE_S = 12.0  # no packets for this long: reopen (loopback is quiet when nothing plays, so this is cheap insurance)
    CHECK_S = 2.0

    def __init__(
        self, spool: Path, system: bool, mic: bool, system_device: str = "default", mic_device: str = "default", kbps: int = 192
    ) -> None:
        self.spool = spool
        self.wanted = [(name, pref) for name, on, pref in (("system", system, system_device), ("mic", mic, mic_device)) if on]
        # With one track the clip's audio can be stream-copied from the live encoder; mixing two needs a re-encode anyway.
        live = kbps if len(self.wanted) == 1 else None
        self.tracks: dict[str, AudioTrack] = {name: AudioTrack(name, spool, live_kbps=live) for name, _ in self.wanted}
        self._pa = None
        self._defaults: tuple[str | None, str | None] = (None, None)
        self.restarts = 0
        self.last_error: str | None = None
        self._lock = threading.Lock()

    def start(self) -> None:  # pragma: no cover - needs audio hardware
        try:
            import pyaudiowpatch as pa
        except ImportError:
            self.last_error = "PyAudioWPatch isn't installed: no audio"
            return
        with self._lock:
            self._defaults = default_endpoint_ids()
            self._pa = pa.PyAudio()
            for name, pref in self.wanted:
                try:
                    device = _resolve_device(self._pa, name, pref)
                    if device:
                        self.tracks[name].attach(self._pa, device)
                except Exception as exc:
                    self.last_error = f"{name}: {exc}"

    def _restart(self) -> None:  # pragma: no cover - needs audio hardware
        with self._lock:
            for t in self.tracks.values():
                t.detach()
            if self._pa is not None:
                self._pa.terminate()
                self._pa = None
        self.restarts += 1
        self.start()

    def check(self) -> None:  # pragma: no cover - needs audio hardware
        """Called every couple of seconds by the buffer's janitor thread."""
        if not self.wanted:
            return
        now = time.time()
        stale = any(now - max(t.last_packet, t.opened_at) > self.STALE_S for t in self.tracks.values())
        moved = default_endpoint_ids() != self._defaults
        dead = any(t._stream is None for t in self.tracks.values())
        if moved or stale or dead:
            self._restart()

    def names(self) -> list[str]:
        return [f"{t.name}:{t.device}" for t in self.tracks.values() if t.device]

    def close(self) -> None:
        for t in self.tracks.values():
            t.close()
        if self._pa is not None:  # pragma: no cover - needs audio hardware
            self._pa.terminate()
            self._pa = None


def open_audio(spool: Path, cfg: CaptureConfig) -> AudioCapture:  # pragma: no cover - needs audio hardware
    capture = AudioCapture(spool, cfg.system_audio, cfg.mic, cfg.audio_device, cfg.mic_device, cfg.audio_kbps)
    capture.start()
    return capture


# ── video buffer ─────────────────────────────────────────────────────────────


@dataclass
class CaptureConfig:
    fps: int = 60
    quality: str = "high"
    encoder: str = "auto"
    codec: str = "h264"
    preset: str = "balanced"
    rate_control: str = "quality"  # quality (constant quality) | bitrate (target Mbps)
    bitrate_mbps: float = 50
    resolution: str = "native"
    monitor: int = 0
    buffer_seconds: int = 60
    system_audio: bool = True
    mic: bool = False
    audio_device: str = "default"
    mic_device: str = "default"
    audio_kbps: int = 192


class ReplayBuffer:
    def __init__(self, spool_root: Path, *, audio_factory: Callable[[Path, CaptureConfig], AudioCapture | None] = open_audio) -> None:
        self.spool_root = spool_root
        self.audio_factory = audio_factory
        self.cfg = CaptureConfig()
        self.proc: subprocess.Popen | None = None
        self.spool: Path | None = None
        self.audio: AudioCapture | None = None
        self.encoder: str | None = None
        self.codec: str | None = None
        self.started_at: float | None = None
        self.recording_since: float | None = None
        self.error: str | None = None
        self._stderr: list[str] = []
        self._lock = threading.RLock()
        self._janitor: threading.Thread | None = None
        self._stop = threading.Event()

    @property
    def tracks(self) -> list[AudioTrack]:
        return list(self.audio.tracks.values()) if self.audio else []

    # lifecycle -------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def ffmpeg_args(self, vendor: str, codec: str) -> list[str]:
        height = RESOLUTIONS.get(self.cfg.resolution)
        src = f"ddagrab=output_idx={self.cfg.monitor}:framerate={self.cfg.fps}:draw_mouse=1"
        if height:  # downscale on the CPU (this FFmpeg build can't map D3D11 frames to CUDA)
            vf = ["-vf", f"hwdownload,format=bgra,scale=-2:'min({height},ih)':flags=bicubic,format=nv12"]
        elif GPU_FRAMES[vendor]:
            vf = []
        else:
            vf = ["-vf", "hwdownload,format=bgra,format=nv12"]
        return [
            "-f", "lavfi", "-i", src,
            *vf,
            *video_args(vendor, codec, self.cfg),
            "-g", str(self.cfg.fps), "-bf", "0",  # a keyframe every second: clips can start on any segment
            "-f", "segment", "-segment_time", str(SEGMENT_S), "-reset_timestamps", "1",
            str(self.spool / "v_%08d.ts"),
        ]  # fmt: skip

    def start(self, cfg: CaptureConfig | None = None) -> None:
        with self._lock:
            if self.active:
                return
            self.cfg = cfg or self.cfg
            self.error = None
            self.spool = self.spool_root / f"buffer_{int(time.time())}"
            self.spool.mkdir(parents=True, exist_ok=True)
            self.encoder = pick_encoder(self.cfg.encoder, self.cfg.codec)
            self.codec = resolve_codec(self.encoder, self.cfg.codec)
            self._stderr = []
            self.proc = subprocess.Popen(
                [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", *self.ffmpeg_args(self.encoder, self.codec)],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
            _bind_to_backend(self.proc)
            threading.Thread(target=self._drain_stderr, args=(self.proc,), daemon=True).start()
            self.audio = self.audio_factory(self.spool, self.cfg) if (self.cfg.system_audio or self.cfg.mic) else None
            self.started_at = time.time()
            self._stop.clear()
            self._janitor = threading.Thread(target=self._janitor_loop, name="clutch-buffer-janitor", daemon=True)
            self._janitor.start()

    def _drain_stderr(self, proc: subprocess.Popen) -> None:
        for line in iter(proc.stderr.readline, b""):
            self._stderr = [*self._stderr[-20:], line.decode(errors="replace").strip()]

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            proc, self.proc = self.proc, None
            if proc and proc.poll() is None:
                try:
                    proc.stdin.write(b"q")
                    proc.stdin.flush()
                    proc.wait(timeout=5)
                except Exception:
                    proc.kill()
            if self.audio:
                self.audio.close()
            self.audio = None
            self.recording_since = None
            spool, self.spool = self.spool, None
            self.started_at = None
        if spool:
            shutil.rmtree(spool, ignore_errors=True)

    def cleanup_stale(self) -> None:
        """Stop recorders and remove spools left by a previous run that crashed."""
        kill_stale_recorders(self.spool_root)
        for d in self.spool_root.glob("buffer_*"):
            if d != self.spool:
                shutil.rmtree(d, ignore_errors=True)

    def _janitor_loop(self) -> None:  # pragma: no cover - timing loop around prune()
        while not self._stop.wait(AudioCapture.CHECK_S):
            if self.proc and self.proc.poll() is not None:
                self.error = (self._stderr[-1] if self._stderr else "") or f"FFmpeg exited ({self.proc.returncode})"
                return
            self.prune()
            if self.audio:
                try:
                    self.audio.check()
                except Exception as exc:
                    self.audio.last_error = str(exc)

    def prune(self) -> None:
        with self._lock:
            if not self.spool:
                return
            keep_from = time.time() - self.cfg.buffer_seconds - KEEP_MARGIN_S
            if self.recording_since is not None:
                keep_from = min(keep_from, self.recording_since - KEEP_MARGIN_S)
            segs = self.segments()
            for start, path in segs[:-2]:
                if start < keep_from:
                    path.unlink(missing_ok=True)
            for t in self.tracks:
                t.prune(keep_from)

    def segments(self) -> list[tuple[float, Path]]:
        """Video segments with their wall-clock start (file creation time)."""
        if not self.spool:
            return []
        out = []
        for p in self.spool.glob("v_*.ts"):
            try:
                st = p.stat()
                # Creation time: when the segment's first frame was written. (st_ctime may mean
                # "metadata changed" on newer Pythons, and hard-linking a segment changes that.)
                out.append((getattr(st, "st_birthtime", st.st_ctime), p))
            except OSError:
                continue
        return sorted(out, key=lambda s: s[1].name)

    # output ----------------------------------------------------------------
    def save(self, dest: Path, seconds: float | None = None, since: float | None = None) -> dict[str, Any]:
        """Stitch the buffer's last ``seconds`` (or everything ``since`` a time) into ``dest``.

        Returns what the clip index needs (duration, size) without probing the file again.
        """
        with self._lock:
            if not self.active or not self.spool:
                raise RuntimeError("The replay buffer isn't running")
            now = time.time()
            clock = [time.perf_counter()]
            cutoff = since if since is not None else now - (seconds or self.cfg.buffer_seconds)
            segs = [s for s in self.segments() if s[0] >= cutoff - SEGMENT_S]
            if not segs:
                raise RuntimeError("Nothing captured yet — give the buffer a second")
            work = self.spool / f"save_{int(now * 1000)}"
            work.mkdir()
            # Hard links, not copies: instant for any size, and they survive the janitor deleting the originals.
            links = []
            for i, (_start, path) in enumerate(segs):
                target = work / f"{i:06d}.ts"
                try:
                    os.link(path, target)
                except OSError:
                    try:
                        shutil.copyfile(path, target)
                    except OSError:
                        continue
                links.append(target)
            t0 = segs[0][0]
            tracks = self.tracks
        try:
            # MPEG-TS joins byte-for-byte (it's how HLS works), so the segments are streamed into one
            # input: the concat demuxer would analyse every segment, ~35 ms each.
            args = ["-f", "mpegts", "-i", "pipe:0"]
            a0 = t0 - AV_OFFSET_S
            live = tracks[0].live_window(a0) if len(tracks) == 1 else None
            if live:
                # Fast path: the audio is already AAC; stream-copy it, starting at the right sample.
                files, shift = live
                alist = work / "audio.txt"
                lines = []
                for i, f in enumerate(files):
                    link = work / f"a{i:06d}.aac"
                    try:
                        os.link(f, link)
                    except OSError:
                        shutil.copyfile(f, link)
                    lines.append(f"file '{link.name}'")
                alist.write_text("\n".join(lines) + "\n", encoding="utf-8")
                args += ["-itsoffset", f"{shift:.6f}", "-f", "concat", "-safe", "0", "-i", str(alist)]
                args += ["-map", "0:v", "-map", "1:a", "-c:a", "copy", "-bsf:a", "aac_adtstoasc"]
                wavs = []
            else:
                wavs = [t.extract(a0, now + 0.5, work / f"{t.name}.wav") for t in tracks]
            for w in wavs:
                args += ["-i", str(w)]
            if not live:
                args += ["-map", "0:v"]
            if len(wavs) == 1:
                args += ["-map", "1:a"]
            elif len(wavs) > 1:
                # Game audio and mic mixed into one track (players share clips; nobody wants to pick tracks).
                mix = "".join(f"[{i + 1}:a]" for i in range(len(wavs)))
                args += ["-filter_complex", f"{mix}amix=inputs={len(wavs)}:duration=first:normalize=0[a]", "-map", "[a]"]
            if wavs:
                args += ["-c:a", "aac", "-b:a", f"{self.cfg.audio_kbps}k"]
            if self.codec == "hevc":
                args += ["-tag:v", "hvc1"]  # the tag browsers and Apple players expect for HEVC in MP4
            args += ["-c:v", "copy", "-shortest", "-movflags", "+faststart", str(dest)]
            dest.parent.mkdir(parents=True, exist_ok=True)
            clock.append(time.perf_counter())
            result = _run_ffmpeg_fed(args, links)
            clock.append(time.perf_counter())
            if result.returncode != 0 or not dest.exists():
                raise RuntimeError(f"Couldn't write the clip: {result.stderr.strip()[-300:]}")
            audio = "live" if live else ("pcm" if wavs else None)
            probe = media_info(dest)  # ~30 ms: the real length (segments are picked with some slack) and size
            phases = {"prepare_ms": round((clock[1] - clock[0]) * 1000), "mux_ms": round((clock[2] - clock[1]) * 1000)}
            return {**probe, "size": dest.stat().st_size, "audio": audio, **phases}
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def start_recording(self) -> float:
        with self._lock:
            if not self.active:
                raise RuntimeError("The replay buffer isn't running")
            self.recording_since = time.time()
            return self.recording_since

    def stop_recording(self, dest: Path) -> dict[str, Any]:
        with self._lock:
            since = self.recording_since
            if since is None:
                raise RuntimeError("Not recording")
        try:
            return self.save(dest, since=since)
        finally:
            self.recording_since = None

    def status(self) -> dict[str, Any]:
        segs = self.segments() if self.active else []
        return {
            "active": self.active,
            "encoder": self.encoder,
            "codec": self.codec,
            "buffer_seconds": self.cfg.buffer_seconds,
            "buffered_seconds": round(time.time() - segs[0][0], 1) if segs else 0,
            "recording": self.recording_since is not None,
            "recording_seconds": round(time.time() - self.recording_since, 1) if self.recording_since else None,
            "audio": self.audio.names() if self.audio else [],
            "audio_restarts": self.audio.restarts if self.audio else 0,
            "error": self.error or (self.audio.last_error if self.audio else None),
            "fps": self.cfg.fps,
        }


def screenshot(dest: Path, monitor: int = 0) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    result = run_ffmpeg(
        ["-f", "lavfi", "-i", f"ddagrab=output_idx={monitor}:framerate=5", "-vf", "hwdownload,format=bgra", "-frames:v", "1", str(dest)],
        timeout=20,
    )
    if result.returncode != 0 or not dest.exists():
        raise RuntimeError(f"Screenshot failed: {result.stderr.strip()[-200:]}")
    return dest
