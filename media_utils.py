"""FFmpeg filters, runtime checks, audio muxing and decoded output verification."""

from fractions import Fraction
from pathlib import Path
from typing import Any
import json
import logging
import shutil
import subprocess
import tempfile

LOGGER = logging.getLogger(__name__)
ERROR_TAIL = 6000
AUDIO_MISMATCH = 0.2
AAC_FRAME_SAMPLES = 1024


def run(args: list[str], data: bytes | None = None, timeout: float = 300) -> bytes:
    """Execute a bounded diagnostic command and expose a bounded error tail."""
    try:
        result = subprocess.run(args, input=data, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Cannot execute {args[0]!r}: {exc}") from exc
    if result.returncode:
        raise RuntimeError(f"{args[0]} failed: {result.stderr.decode('utf-8', 'replace')[-ERROR_TAIL:]}")
    return result.stdout


def find_ffprobe(ffmpeg: str) -> str:
    """Prefer ffprobe beside the VHS-selected FFmpeg, then search PATH."""
    sibling = Path(ffmpeg).resolve().with_name("ffprobe.exe" if Path(ffmpeg).suffix == ".exe" else "ffprobe")
    probe = str(sibling) if sibling.is_file() else shutil.which("ffprobe")
    if not probe:
        raise RuntimeError("Install ffprobe alongside the VHS-selected FFmpeg or add ffprobe to PATH")
    return probe


def check_nvenc(ffmpeg: str, temp_dir: str) -> str:
    """Test the actual selected encoder and driver; never fall back to CPU."""
    probe = find_ffprobe(ffmpeg)
    encoders = run([ffmpeg, "-hide_banner", "-encoders"], timeout=30).decode("utf-8", "replace")
    if "h264_nvenc" not in encoders:
        raise RuntimeError("VHS FFmpeg has no h264_nvenc; set VHS_FORCE_FFMPEG_PATH to an NVENC-enabled FFmpeg")
    with tempfile.TemporaryDirectory(prefix="vhse_probe_", dir=temp_dir) as directory:
        path = str(Path(directory) / "probe.mp4")
        try:
            run([ffmpeg, "-v", "error", "-n", "-f", "lavfi", "-i", "color=s=192x128:r=1",
                 "-frames:v", "1", *encoder_args(19, "p5"), path], timeout=45)
        except RuntimeError as exc:
            raise RuntimeError(
                f"NVENC cannot encode using {ffmpeg!r}. Check NVIDIA driver, GPU encode support and Docker "
                f"NVIDIA_DRIVER_CAPABILITIES=compute,video,utility. Details: {exc}"
            ) from exc
    return probe


def encoder_args(cq: int, preset: str) -> list[str]:
    """Return the shared H.264 NVENC encoding settings."""
    return ["-c:v", "h264_nvenc", "-preset", preset, "-tune", "hq", "-rc", "vbr", "-cq", str(cq),
            "-b:v", "0", "-pix_fmt", "yuv420p", "-movflags", "+faststart"]


def check_cpu_h264(ffmpeg: str, temp_dir: str) -> str:
    """Test software H.264 independently of NVIDIA hardware and runtime libraries."""
    probe = find_ffprobe(ffmpeg)
    encoders = run([ffmpeg, "-hide_banner", "-encoders"], timeout=30).decode("utf-8", "replace")
    if "libx264" not in encoders:
        raise RuntimeError("VHS FFmpeg has no libx264; install an FFmpeg build with software H.264 support")
    with tempfile.TemporaryDirectory(prefix="vhse_cpu_probe_", dir=temp_dir) as directory:
        path = str(Path(directory) / "probe.mp4")
        run([ffmpeg, "-v", "error", "-n", "-f", "lavfi", "-i", "color=s=192x128:r=1", "-frames:v", "1",
             "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", path], timeout=45)
    return probe


def resize_filter(source: tuple[int, int], target: tuple[int, int], width: str, height: str,
                  mode: str, method: str) -> str:
    """Build one filter chain with VHS-compatible BT.709 conversion."""
    if mode not in {"crop", "pad", "stretch"} or method not in {"bicubic", "lanczos", "bilinear"}:
        raise ValueError("Invalid resize mode or method")
    w, h = target
    color = "out_color_matrix=bt709"
    if not str(width).strip() and not str(height).strip():
        geometry = f"pad={w}:{h}:0:0," if source != target else ""
        return f"{geometry}scale={color},setsar=1,format=yuv420p"
    if mode == "stretch" or not str(width).strip() or not str(height).strip():
        geometry = f"scale={w}:{h}:flags={method}:{color}"
    else:
        aspect = "increase" if mode == "crop" else "decrease"
        end = f"crop={w}:{h}:(iw-ow)/2:(ih-oh)/2" if mode == "crop" else f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2"
        geometry = f"scale={w}:{h}:force_original_aspect_ratio={aspect}:flags={method}:{color},{end}"
    return f"{geometry},setsar=1,format=yuv420p"


def audio_filter(source_duration: Fraction, target_duration: Fraction) -> str:
    """Normalize the source end, change tempo, then normalize the output end."""
    factor = source_duration / target_duration
    filters = ["asetpts=PTS-STARTPTS", f"apad=whole_dur={float(source_duration):.12f}",
               f"atrim=duration={float(source_duration):.12f}"]
    while factor > 2:
        filters.append("atempo=2")
        factor /= 2
    while factor < Fraction(1, 2):
        filters.append("atempo=0.5")
        factor *= 2
    if factor != 1:
        filters.append(f"atempo={float(factor):.15g}")
    filters.extend([f"apad=whole_dur={float(target_duration):.12f}",
                    f"atrim=duration={float(target_duration):.12f}", "asetpts=PTS-STARTPTS"])
    return ",".join(filters)


def write_metadata(path: Path, prompt: Any, extra_pnginfo: dict[str, Any] | None) -> dict[str, str]:
    """Write per-operation FFmetadata with ComfyUI JSON tags and a legacy comment payload."""
    values = dict(extra_pnginfo or {})
    if prompt is not None:
        values["prompt"] = prompt
    tags = {key: json.dumps(value, ensure_ascii=False) for key, value in values.items()}
    # Older MP4 workflow readers use the comment JSON instead of custom MP4 tags.
    comment = dict(values)
    if prompt is not None:
        comment["prompt"] = tags["prompt"]
    tags["comment"] = json.dumps(comment, ensure_ascii=False)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write(";FFMETADATA1\n")
        for key, value in tags.items():
            escaped = []
            for text in (key, value):
                for character in ("\\", ";", "#", "=", "\n"):
                    text = text.replace(character, f"\\{character}")
                escaped.append(text)
            stream.write(f"{escaped[0]}={escaped[1]}\n")
    return tags


def mux_metadata(ffmpeg: str, video: Path, output: Path, metadata: Path) -> None:
    """Embed metadata without re-encoding video or audio streams."""
    run([ffmpeg, "-v", "error", "-n", "-i", str(video), "-f", "ffmetadata", "-i", str(metadata),
         "-map", "0", "-c", "copy", "-map_metadata", "1", "-movflags", "+faststart+use_metadata_tags",
         str(output)])


def verify_metadata(probe: str, path: Path, expected: dict[str, str]) -> None:
    """Ensure all requested ComfyUI metadata survived the final MP4 mux."""
    info = json.loads(run([probe, "-v", "error", "-show_entries", "format_tags", "-of", "json", str(path)]))
    tags = info.get("format", {}).get("tags", {})
    if any(tags.get(key) != value for key, value in expected.items()):
        raise RuntimeError(f"Embedded metadata verification failed: {path.resolve()}")


def mux_audio(ffmpeg: str, video: Path, output: Path, audio: dict[str, Any],
              source_duration: Fraction, target_duration: Fraction, metadata: Path | None = None) -> None:
    """Add normalized AUDIO with video stream copy and no -shortest."""
    waveform = audio["waveform"]
    rate = audio["sample_rate"]
    if len(waveform.shape) != 3 or waveform.shape[0] != 1 or waveform.shape[1] < 1:
        raise ValueError("AUDIO waveform must have shape [1, channels, samples]")
    if not isinstance(rate, int) or rate <= 0 or waveform.shape[2] < 1:
        raise ValueError("AUDIO requires a positive sample rate and nonempty samples")
    duration = waveform.shape[2] / rate
    if abs(duration - float(source_duration)) > AUDIO_MISMATCH:
        LOGGER.warning(f"Audio duration {duration:.6f}s differs from source video {float(source_duration):.6f}s")
    data = waveform.detach().cpu().squeeze(0).transpose(0, 1).contiguous().numpy().astype("<f4").tobytes()
    metadata_args = ["-f", "ffmetadata", "-i", str(metadata)] if metadata else []
    run([ffmpeg, "-v", "error", "-n", "-i", str(video), "-f", "f32le", "-ar", str(rate),
         "-ac", str(waveform.shape[1]), "-i", "pipe:0", *metadata_args,
         "-map", "0:v:0", "-map", "1:a:0", "-map_metadata", "2" if metadata else "-1",
         "-c:v", "copy", "-af", audio_filter(source_duration, target_duration), "-c:a", "aac",
         "-b:a", "192k", "-movflags", "+faststart+use_metadata_tags", str(output)], data)


def verify_output(probe: str, ffmpeg: str, path: Path, count: int, fps: Fraction,
                  size: tuple[int, int], has_audio: bool) -> dict[str, Any]:
    """Verify decoded frames, CFR timestamps, geometry, codec and decoded audio duration."""
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError(f"Missing or empty output file: {path.resolve()}")
    info = json.loads(run([probe, "-v", "error", "-count_frames", "-show_streams", "-of", "json", str(path)]))
    videos = [s for s in info["streams"] if s["codec_type"] == "video"]
    audios = [s for s in info["streams"] if s["codec_type"] == "audio"]
    if len(videos) != 1 or len(audios) != int(has_audio) or len(info["streams"]) != 1 + int(has_audio):
        raise RuntimeError(f"Unexpected stream layout in {path.resolve()}")
    v = videos[0]
    expected = float(Fraction(count, 1) / fps)
    if (v["codec_name"] != "h264" or v["pix_fmt"] != "yuv420p" or int(v["nb_read_frames"]) != count
            or (v["width"], v["height"]) != size or Fraction(v["avg_frame_rate"]) != fps
            or Fraction(v["r_frame_rate"]) != fps or v.get("sample_aspect_ratio") != "1:1"):
        raise RuntimeError(f"Video codec/frame count/FPS/geometry verification failed: {path.resolve()}: {v}")
    tolerance = max(0.0001, float(Fraction(v["time_base"])))
    if abs(float(v["duration"]) - expected) > tolerance or abs(float(v.get("start_time", 0))) > tolerance:
        raise RuntimeError(f"Video timeline verification failed: {path.resolve()}")
    # Stream rate metadata alone does not prove constant presentation intervals.
    timestamps = run([probe, "-v", "error", "-select_streams", "v:0", "-show_frames", "-show_entries",
                      "frame=best_effort_timestamp_time", "-of", "json", str(path)])
    frames = json.loads(timestamps)["frames"]
    if len(frames) != count or any(
        abs(float(frame["best_effort_timestamp_time"]) - float(Fraction(i, 1) / fps)) > tolerance
        for i, frame in enumerate(frames)
    ):
        raise RuntimeError(f"Non-CFR video timestamps: {path.resolve()}")
    if has_audio:
        a = audios[0]
        rate, channels = int(a["sample_rate"]), int(a["channels"])
        # Count decoded samples without retaining the decoded audio in memory.
        with tempfile.TemporaryFile(dir=path.parent) as sink:
            result = subprocess.run([ffmpeg, "-v", "error", "-i", str(path), "-map", "0:a:0", "-f",
                                     "f32le", "-acodec", "pcm_f32le", "-"], stdout=sink,
                                    stderr=subprocess.PIPE, timeout=300, check=False)
            if result.returncode:
                raise RuntimeError(f"Audio decode failed: {result.stderr.decode('utf-8', 'replace')[-ERROR_TAIL:]}")
            decoded = sink.tell() / (4 * rate * channels)
        allowed = 2 * AAC_FRAME_SAMPLES / rate + 0.002
        if (a["codec_name"] != "aac" or abs(decoded - expected) > allowed
                or abs(float(a.get("start_time", 0))) > 1 / rate):
            raise RuntimeError(f"Audio synchronization verification failed: {path.resolve()}, decoded={decoded}s")
        LOGGER.info(f"Decoded audio duration={decoded:.6f}s; target={expected:.6f}s; tolerance={allowed:.6f}s")
    LOGGER.info(f"Verified {path.resolve()}: frames={count}, FPS={fps}, size={size}, duration={expected:.6f}s")
    return info
