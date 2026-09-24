"""Editing beyond trim: montages, captions, vertical (9:16) versions, and dropping the mic.

Every operation writes a new file next to the source and indexes it as an
export, so originals are never changed.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from clutch.local.capture import media_info, pick_encoder, run_ffmpeg

FONT_CANDIDATES = (
    r"C:\Windows\Fonts\impact.ttf",
    r"C:\Windows\Fonts\arialbd.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
)


def _filter_path(path: str | Path) -> str:
    """A path as FFmpeg's filter syntax wants it (forward slashes, escaped drive colon)."""
    return str(path).replace("\\", "/").replace(":", r"\:")


def _video_codec(encoder: str) -> list[str]:
    vendor = pick_encoder(encoder, "h264")
    return {
        "nvenc": ["-c:v", "h264_nvenc", "-preset", "p5", "-cq", "19", "-b:v", "0"],
        "amf": ["-c:v", "h264_amf", "-rc", "qvbr", "-qvbr_quality_level", "30"],
        "qsv": ["-c:v", "h264_qsv", "-global_quality", "20"],
    }.get(vendor, ["-c:v", "libx264", "-preset", "veryfast", "-crf", "19"])


def _run(args: list[str], what: str) -> None:
    result = run_ffmpeg(args, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(f"{what} failed: {result.stderr.strip()[-300:]}")


def montage(
    sources: list[dict[str, Any]],
    dest: Path,
    *,
    width: int = 1920,
    height: int = 1080,
    fps: int = 60,
    fade: float = 0.25,
    encoder: str = "auto",
) -> Path:
    """Clips back to back at one size and frame rate, with a short dip to black between them."""
    if len(sources) < 2:
        raise ValueError("Pick at least two clips for a montage")
    inputs: list[str] = []
    chains: list[str] = []
    for i, clip in enumerate(sources):
        info = media_info(Path(clip["path"]))
        dur = info.get("duration") or clip.get("duration") or 0
        inputs += ["-i", clip["path"]]
        out = max(0.0, dur - fade)
        chains.append(
            f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            f"fps={fps},setsar=1,format=yuv420p,fade=t=in:d={fade},fade=t=out:st={out:.3f}:d={fade}[v{i}]"
        )
        if info.get("has_audio"):
            chains.append(
                f"[{i}:a]aresample=48000,aformat=channel_layouts=stereo,afade=t=in:d={fade},afade=t=out:st={out:.3f}:d={fade}[a{i}]"
            )
        else:  # a silent track keeps the concat filter's stream count consistent
            chains.append(f"anullsrc=r=48000:cl=stereo,atrim=0:{dur:.3f}[a{i}]")
    joined = "".join(f"[v{i}][a{i}]" for i in range(len(sources)))
    graph = ";".join(chains) + f";{joined}concat=n={len(sources)}:v=1:a=1[v][a]"
    _run(
        [
            *inputs,
            "-filter_complex",
            graph,
            "-map",
            "[v]",
            "-map",
            "[a]",
            *_video_codec(encoder),
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(dest),
        ],
        "Montage",
    )
    return dest


def caption(source: Path, dest: Path, text: str, *, position: str = "bottom", size: int = 64, encoder: str = "auto") -> Path:
    """Burn a caption in (meme style: white with a black outline)."""
    text = text.strip()
    if not text:
        raise ValueError("Caption text is empty")
    font = next((f for f in FONT_CANDIDATES if os.path.exists(f)), None)
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
        fh.write(text)  # textfile= avoids escaping quotes, colons and emoji in the filter string
        textfile = fh.name
    y = {"top": "h*0.06", "middle": "(h-text_h)/2", "bottom": "h-text_h-h*0.08"}.get(position, "h-text_h-h*0.08")
    font_opt = f"fontfile='{_filter_path(font)}':" if font else "font='Sans':"
    draw = f"drawtext={font_opt}textfile='{_filter_path(textfile)}':fontsize={size}:fontcolor=white:borderw=5:bordercolor=black:x=(w-text_w)/2:y={y}"
    try:
        _run(["-i", str(source), "-vf", draw, *_video_codec(encoder), "-c:a", "copy", "-movflags", "+faststart", str(dest)], "Caption")
    finally:
        os.unlink(textfile)
    return dest


def vertical(source: Path, dest: Path, *, mode: str = "blur", encoder: str = "auto") -> Path:
    """A 1080x1920 version for TikTok / Shorts / Reels.

    ``blur`` keeps the whole frame over a blurred, zoomed copy of itself;
    ``crop`` fills the screen with the centre of the frame.
    """
    if mode == "crop":
        vf = "crop=ih*9/16:ih,scale=1080:1920,setsar=1"
        graph = ["-vf", vf]
    else:
        graph = [
            "-filter_complex",
            "[0:v]split[a][b];[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=24:2,eq=brightness=-0.08[bg];"
            "[b]scale=1080:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]",
            "-map",
            "[v]",
            "-map",
            "0:a?",
        ]
    _run(["-i", str(source), *graph, *_video_codec(encoder), "-c:a", "copy", "-movflags", "+faststart", str(dest)], "Vertical export")
    return dest


def without_mic(source: Path, dest: Path) -> Path:
    """Keep only the game-audio track of a clip recorded with separate tracks (instant, no re-encode)."""
    if audio_track_count(source) < 3:
        raise ValueError("This clip has no separate game-audio track (turn on separate tracks before recording)")
    _run(["-i", str(source), "-map", "0:v", "-map", "0:a:1", "-c", "copy", "-movflags", "+faststart", str(dest)], "Export without mic")
    return dest


def audio_track_count(path: Path) -> int:
    return media_info(path)["audio_tracks"]
