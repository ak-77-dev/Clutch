"""Replay buffer, recording and screenshots.

Video: one long-running FFmpeg process grabs the desktop with the Desktop
Duplication API (``ddagrab``), encodes on the GPU (NVENC / AMF / Quick Sync,
x264 as a fallback) and writes 1-second MPEG-TS segments into a spool folder.

Audio: WASAPI loopback (what you hear) and optionally the microphone are
written to the same spool as raw PCM, with silence filled in by wall clock:
Windows delivers no loopback packets while nothing is playing, and without the
fill a quiet stretch would pull every later sound out of sync.

A clip is "the last N seconds of segments + the matching PCM", stitched with a
stream copy (no re-encode), so saving takes well under a second. Recording is
the same thing with pruning paused from the moment you start.
"""

from __future__ import annotations

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
# (NVENC, 60 fps): audio led video by 40-57 ms without this correction.
AV_OFFSET_S = 0.045
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

ENCODERS = {
    # name -> (ffmpeg encoder, extra args, takes GPU (d3d11) frames directly)
    "nvenc": ("h264_nvenc", ["-preset", "p4", "-tune", "hq", "-rc", "vbr", "-b:v", "0"], True),
    "amf": ("h264_amf", ["-quality", "balanced", "-rc", "qvbr"], True),
    "qsv": ("h264_qsv", ["-preset", "medium"], False),
    "x264": ("libx264", ["-preset", "veryfast", "-tune", "zerolatency"], False),
}
QUALITY = {"low": 30, "medium": 26, "high": 22, "ultra": 18}


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


_encoder_cache: dict[str, str] = {}


def pick_encoder(preference: str = "auto") -> str:
    """The first hardware encoder that actually initializes on this PC (x264 otherwise)."""
    if preference in ENCODERS and preference != "auto":
        return preference
    if "auto" in _encoder_cache:
        return _encoder_cache["auto"]
    chosen = "x264"
    for name in ("nvenc", "amf", "qsv"):
        # Listing encoders isn't enough: builds include NVENC even without an NVIDIA GPU.
        probe = run_ffmpeg(["-f", "lavfi", "-i", "color=black:s=256x256:d=0.2", "-c:v", ENCODERS[name][0], "-f", "null", "-"], timeout=20)
        if probe.returncode == 0:
            chosen = name
            break
    _encoder_cache["auto"] = chosen
    return chosen


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


# ── audio ────────────────────────────────────────────────────────────────────


class AudioTrack:
    """Spools one WASAPI stream to disk as raw PCM with a wall-clock timeline.

    Files are ``<name>_<start epoch ms>.pcm`` and each holds a continuous run of
    16-bit samples; a gap between chunks is filled with silence, so sample
    ``i`` of a file always plays at ``start + i / rate``.
    """

    FILE_S = 30.0

    def __init__(self, name: str, spool: Path, rate: int, channels: int) -> None:
        self.name, self.spool, self.rate, self.channels = name, spool, rate, channels
        self.frame_bytes = 2 * channels
        self.q: queue.Queue[tuple[float, bytes] | None] = queue.Queue()
        self._fh = None
        self._file_start = 0.0
        self._cursor = 0.0  # wall time of the next sample to write
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name=f"clutch-audio-{name}", daemon=True)
        self._stream = None
        self._pa = None

    # stream plumbing -------------------------------------------------------
    def open(self, device_index: int) -> None:  # pragma: no cover - needs audio hardware
        import pyaudiowpatch as pa

        self._pa = pa.PyAudio()

        def callback(data, frames, _info, _status):
            self.q.put((time.time(), data))
            return (None, pa.paContinue)

        self._stream = self._pa.open(
            format=pa.paInt16,
            channels=self.channels,
            rate=self.rate,
            input=True,
            input_device_index=device_index,
            frames_per_buffer=int(self.rate * 0.02),
            stream_callback=callback,
        )
        self._thread.start()

    def close(self) -> None:
        if self._stream is not None:  # pragma: no cover - needs audio hardware
            try:
                self._stream.stop_stream()
                self._stream.close()
            finally:
                self._pa.terminate()
        self.q.put(None)
        if self._thread.is_alive():
            self._thread.join(timeout=2)
        with self._lock:
            if self._fh:
                self._fh.close()
                self._fh = None

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
            self._fh.write(data)
            self._cursor += n / self.rate
            if self._cursor - self._file_start > self.FILE_S:
                self._new_file(self._cursor)

    def fill_silence(self, until: float) -> None:
        with self._lock:
            if self._fh is None:
                self._new_file(until)
                return
            if until - self._cursor > 0.04:
                self._pad(until - self._cursor)
                if self._cursor - self._file_start > self.FILE_S:
                    self._new_file(self._cursor)

    def _pad(self, seconds: float) -> None:
        frames = int(seconds * self.rate)
        self._fh.write(b"\x00" * frames * self.frame_bytes)
        self._cursor += frames / self.rate

    def _new_file(self, start: float) -> None:
        if self._fh:
            self._fh.close()
        self._file_start = self._cursor = start
        self._fh = open(self.spool / f"{self.name}_{int(start * 1000)}.pcm", "wb")  # noqa: SIM115 - long-lived handle

    def flush(self) -> None:
        with self._lock:
            if self._fh:
                self._fh.flush()

    # extraction -------------------------------------------------------------
    def files(self) -> list[tuple[float, Path]]:
        out = []
        for p in self.spool.glob(f"{self.name}_*.pcm"):
            try:
                out.append((int(p.stem.rsplit("_", 1)[1]) / 1000, p))
            except ValueError:
                continue
        return sorted(out)

    def extract(self, t0: float, t1: float, dest: Path) -> Path:
        """Write ``[t0, t1)`` of the timeline as a WAV (silence where nothing was captured)."""
        self.flush()
        total = int((t1 - t0) * self.rate)
        buf = bytearray(total * self.frame_bytes)
        for start, path in self.files():
            try:
                data = path.read_bytes()
            except OSError:
                continue
            frames = len(data) // self.frame_bytes
            lo, hi = max(t0, start), min(t1, start + frames / self.rate)
            if hi <= lo:
                continue
            src_from, src_to = int((lo - start) * self.rate), int((hi - start) * self.rate)
            at = int((lo - t0) * self.rate) * self.frame_bytes
            chunk = data[src_from * self.frame_bytes : src_to * self.frame_bytes][: max(0, len(buf) - at)]
            buf[at : at + len(chunk)] = chunk
        with wave.open(str(dest), "wb") as w:
            w.setnchannels(self.channels)
            w.setsampwidth(2)
            w.setframerate(self.rate)
            w.writeframes(bytes(buf))
        return dest

    def prune(self, older_than: float) -> None:
        files = self.files()
        for i, (_start, path) in enumerate(files):
            nxt = files[i + 1][0] if i + 1 < len(files) else None
            if nxt is not None and nxt < older_than:  # the whole file ends before the cutoff
                path.unlink(missing_ok=True)


def open_audio_tracks(spool: Path, system: bool, mic: bool) -> list[AudioTrack]:  # pragma: no cover - audio hardware
    tracks: list[AudioTrack] = []
    try:
        import pyaudiowpatch as pa
    except ImportError:
        return tracks
    p = pa.PyAudio()
    try:
        devices = []
        if system:
            lb = p.get_default_wasapi_loopback()
            devices.append(("system", lb))
        if mic:
            wasapi = p.get_host_api_info_by_type(pa.paWASAPI)
            devices.append(("mic", p.get_device_info_by_index(wasapi["defaultInputDevice"])))
    except Exception:
        devices = []
    finally:
        p.terminate()
    for name, dev in devices:
        track = AudioTrack(name, spool, int(dev["defaultSampleRate"]), min(2, int(dev["maxInputChannels"])) or 2)
        try:
            track.open(dev["index"])
            tracks.append(track)
        except Exception:
            track.close()
    return tracks


# ── video buffer ─────────────────────────────────────────────────────────────


@dataclass
class CaptureConfig:
    fps: int = 60
    quality: str = "high"
    encoder: str = "auto"
    monitor: int = 0
    buffer_seconds: int = 60
    system_audio: bool = True
    mic: bool = False


class ReplayBuffer:
    def __init__(self, spool_root: Path, *, audio_factory: Callable[..., list[AudioTrack]] = open_audio_tracks) -> None:
        self.spool_root = spool_root
        self.audio_factory = audio_factory
        self.cfg = CaptureConfig()
        self.proc: subprocess.Popen | None = None
        self.spool: Path | None = None
        self.tracks: list[AudioTrack] = []
        self.encoder: str | None = None
        self.started_at: float | None = None
        self.recording_since: float | None = None
        self.error: str | None = None
        self._stderr: list[str] = []
        self._lock = threading.RLock()
        self._janitor: threading.Thread | None = None
        self._stop = threading.Event()

    # lifecycle -------------------------------------------------------------
    @property
    def active(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def ffmpeg_args(self, encoder: str) -> list[str]:
        codec, extra, gpu_frames = ENCODERS[encoder]
        cq = QUALITY.get(self.cfg.quality, 22)
        src = f"ddagrab=output_idx={self.cfg.monitor}:framerate={self.cfg.fps}:draw_mouse=1"
        vf = [] if gpu_frames else ["-vf", "hwdownload,format=bgra,format=yuv420p"]
        quality = {
            "nvenc": ["-cq", str(cq)],
            "amf": ["-qvbr_quality_level", str(max(1, 51 - cq))],
            "qsv": ["-global_quality", str(cq)],
            "x264": ["-crf", str(cq)],
        }[encoder]
        return [
            "-f", "lavfi", "-i", src,
            *vf,
            "-c:v", codec, *extra, *quality,
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
            self.encoder = pick_encoder(self.cfg.encoder)
            self._stderr = []
            self.proc = subprocess.Popen(
                [ffmpeg_exe(), "-hide_banner", "-loglevel", "error", *self.ffmpeg_args(self.encoder)],
                stdin=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                creationflags=CREATE_NO_WINDOW,
            )
            threading.Thread(target=self._drain_stderr, args=(self.proc,), daemon=True).start()
            self.tracks = self.audio_factory(self.spool, self.cfg.system_audio, self.cfg.mic)
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
            for t in self.tracks:
                t.close()
            self.tracks = []
            self.recording_since = None
            spool, self.spool = self.spool, None
            self.started_at = None
        if spool:
            shutil.rmtree(spool, ignore_errors=True)

    def cleanup_stale(self) -> None:
        """Remove spools left by a previous run that crashed."""
        for d in self.spool_root.glob("buffer_*"):
            if d != self.spool:
                shutil.rmtree(d, ignore_errors=True)

    def _janitor_loop(self) -> None:  # pragma: no cover - timing loop around prune()
        while not self._stop.wait(2.0):
            if self.proc and self.proc.poll() is not None:
                self.error = (self._stderr[-1] if self._stderr else "") or f"FFmpeg exited ({self.proc.returncode})"
                return
            self.prune()

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
                out.append((p.stat().st_ctime, p))
            except OSError:
                continue
        return sorted(out, key=lambda s: s[1].name)

    # output ----------------------------------------------------------------
    def save(self, dest: Path, seconds: float | None = None, since: float | None = None) -> Path:
        """Stitch the buffer's last ``seconds`` (or everything ``since`` a time) into ``dest``."""
        with self._lock:
            if not self.active or not self.spool:
                raise RuntimeError("The replay buffer isn't running")
            now = time.time()
            cutoff = since if since is not None else now - (seconds or self.cfg.buffer_seconds)
            segs = [s for s in self.segments() if s[0] >= cutoff - SEGMENT_S]
            if not segs:
                raise RuntimeError("Nothing captured yet — give the buffer a second")
            work = self.spool / f"save_{int(now * 1000)}"
            work.mkdir()
            # Copy first: FFmpeg keeps writing the newest segment and the janitor keeps deleting old ones.
            copies = []
            for i, (_start, path) in enumerate(segs):
                target = work / f"{i:06d}.ts"
                try:
                    shutil.copyfile(path, target)
                    copies.append(target)
                except OSError:
                    continue
            t0 = segs[0][0]
            tracks = list(self.tracks)
        try:
            listing = work / "list.txt"
            listing.write_text("".join(f"file '{c.name}'\n" for c in copies), encoding="utf-8")
            args = ["-f", "concat", "-safe", "0", "-i", str(listing)]
            a0 = t0 - AV_OFFSET_S
            wavs = [t.extract(a0, now + 0.5, work / f"{t.name}.wav") for t in tracks]
            for w in wavs:
                args += ["-i", str(w)]
            args += ["-map", "0:v"]
            if len(wavs) == 1:
                args += ["-map", "1:a"]
            elif len(wavs) > 1:
                # Game audio and mic mixed into one track (players share clips; nobody wants to pick tracks).
                mix = "".join(f"[{i + 1}:a]" for i in range(len(wavs)))
                args += ["-filter_complex", f"{mix}amix=inputs={len(wavs)}:duration=first:normalize=0[a]", "-map", "[a]"]
            if wavs:
                args += ["-c:a", "aac", "-b:a", "192k"]
            args += ["-c:v", "copy", "-shortest", "-movflags", "+faststart", str(dest)]
            dest.parent.mkdir(parents=True, exist_ok=True)
            result = run_ffmpeg(args)
            if result.returncode != 0 or not dest.exists():
                raise RuntimeError(f"Couldn't write the clip: {result.stderr.strip()[-300:]}")
            return dest
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def start_recording(self) -> float:
        with self._lock:
            if not self.active:
                raise RuntimeError("The replay buffer isn't running")
            self.recording_since = time.time()
            return self.recording_since

    def stop_recording(self, dest: Path) -> Path:
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
            "buffer_seconds": self.cfg.buffer_seconds,
            "buffered_seconds": round(time.time() - segs[0][0], 1) if segs else 0,
            "recording": self.recording_since is not None,
            "recording_seconds": round(time.time() - self.recording_since, 1) if self.recording_since else None,
            "audio": [t.name for t in self.tracks],
            "error": self.error,
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
