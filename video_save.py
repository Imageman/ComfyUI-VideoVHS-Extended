"""Small ComfyUI facade delegating video encoding to the original VHS node."""

from copy import deepcopy
from fractions import Fraction
from pathlib import Path
from typing import Any
import importlib
import inspect
import logging
import os
import shutil
import tempfile
import time
import traceback

from .frame_mapping import FrameMapping, UNSPECIFIED, dimensions, parse_fps, parse_frames
from .media_utils import (
    check_cpu_h264, check_nvenc, mux_audio, mux_metadata, resize_filter,
    verify_metadata, verify_output, write_metadata,
)

LOGGER = logging.getLogger(__name__)
PROFILE = "vhse_h264_nvenc_cq-mp4.json"
CPU_PROFILE = "vhse_h264_cpu_crf-mp4.json"
CPU_PRESETS = ["ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"]
PROFILE_DIR = Path(__file__).resolve().parent / "video_formats"


def upstream() -> tuple[Any, str]:
    """Resolve the registered VHS at execution time, independently of load order."""
    import nodes
    import folder_paths

    cls = nodes.NODE_CLASS_MAPPINGS.get("VHS_VideoCombine")
    if cls is None:
        raise RuntimeError("Install and enable original ComfyUI-VideoHelperSuite, then restart ComfyUI")
    method = getattr(cls, "combine_video", None)
    if method is None or "images" not in inspect.signature(method).parameters:
        raise RuntimeError("Incompatible VHS_VideoCombine: expected combine_video(images=...). Update VHS")
    module = importlib.import_module(cls.__module__)
    ffmpeg = getattr(module, "ffmpeg_path", None)
    if not ffmpeg:
        raise RuntimeError("VHS has no FFmpeg. Set VHS_FORCE_FFMPEG_PATH before starting ComfyUI")
    forced = os.environ.get("VHS_FORCE_FFMPEG_PATH")
    if forced and str(Path(forced).resolve()) != str(Path(ffmpeg).resolve()):
        raise RuntimeError("VHS_FORCE_FFMPEG_PATH changed after VHS loaded; restart ComfyUI")
    # VHS initializes this category with tuple paths, while ComfyUI's public helper appends to a list.
    category = "VHS_video_formats"
    if category in folder_paths.folder_names_and_paths:
        paths, extensions = folder_paths.folder_names_and_paths[category]
        if not isinstance(paths, list):
            folder_paths.folder_names_and_paths[category] = (list(paths), extensions)
    folder_paths.add_model_folder_path(category, str(PROFILE_DIR))
    return cls, ffmpeg


def publish(source: Path, prefix: str, output_dir: str) -> tuple[Path, str]:
    """Publish under ComfyUI naming rules using exclusive file creation."""
    import folder_paths

    directory, name, counter, subfolder, _ = folder_paths.get_save_image_path(prefix, output_dir)
    directory_path = Path(directory)
    for offset in range(10000):
        path = directory_path / f"{name}_{counter + offset:05}.mp4"
        try:
            destination = path.open("xb")
        except FileExistsError:
            continue
        try:
            with destination, source.open("rb") as incoming:
                shutil.copyfileobj(incoming, destination)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return path, subfolder
    raise RuntimeError(f"Cannot allocate an output filename in {directory_path.resolve()}")


class VHSVideoSaveExtended:
    """Save exact-frame H.264 MP4 with NVENC or CPU, resizing and pitch-preserving audio."""

    RETURN_TYPES = ("VHS_FILENAMES",)
    RETURN_NAMES = ("Filenames",)
    OUTPUT_NODE = True
    CATEGORY = "Video VHS Extended"
    FUNCTION = "save_video"

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, Any]:
        """Expose a fixed interface without depending on VHS import order."""
        return {
            "required": {
                "images": ("IMAGE", {"tooltip": "Frames to save, in playback order."}),
                "source_fps": ("STRING", {"default": "24", "tooltip":
                    "FPS of the input frames, e.g. 24 or 24000/1001."}),
                "target_fps": ("STRING", {"default": "", "tooltip":
                    "Empty keeps source FPS. A different FPS requires a positive target_frames value."}),
                "width": ("STRING", {"default": "1920", "tooltip":
                    "Output width, even pixels. Empty derives it from height; both empty keep source size."}),
                "height": ("STRING", {"default": "1080", "tooltip":
                    "Output height, even pixels. Empty derives it from width; both empty keep source size."}),
                "resize_method": (["bicubic", "lanczos", "bilinear"], {"default": "bicubic", "tooltip":
                    "Bicubic: balanced. Lanczos: sharper, may ring. Bilinear: faster and softer."}),
                "resize_mode": (["crop", "pad", "stretch"], {"default": "crop", "tooltip":
                    "Crop: fill and center-crop. Pad: fit with black borders. Stretch: change proportions."}),
                "cq": ("INT", {"default": 19, "min": 0, "max": 51, "tooltip":
                    "NVENC CQ or CPU CRF. Lower means higher quality and usually larger files. "
                    "19 is a starting point; CQ and CRF values are not equivalent."}),
                "preset": (["p4", "p5", "p6", "p7"], {"default": "p5", "tooltip":
                    "NVIDIA only: p4 is faster; p7 spends more time on compression. "
                    "p5 is balanced. Ignored for CPU encoding."}),
                "filename_prefix": ("STRING", {"default": "video_extended", "tooltip":
                    "Output name prefix, optionally with a subfolder. A numbered suffix is added."}),
                "save_output": ("BOOLEAN", {"default": True, "tooltip":
                    "On: save in ComfyUI output. Off: save in the temporary folder."}),
            },
            # Optional placement permits older API workflows to omit target_frames.
            "optional": {
                "target_frames": ("STRING", {"default": "", "tooltip":
                    "Exact output count. Empty, 0, default, auto or none keep the original count and speed."}),
                "audio": ("AUDIO", {"tooltip": "Optional audio. Its speed follows the video, preserving pitch."}),
                "include_audio": ("BOOLEAN", {"default": True, "tooltip":
                    "Include supplied audio. If off or no audio is connected, save video only."}),
                "encoder": (["h264_nvenc", "libx264"], {"default": "h264_nvenc", "tooltip":
                    "h264_nvenc: NVIDIA GPU. libx264: CPU H.264, no NVIDIA GPU required."}),
                "cpu_preset": (CPU_PRESETS, {"default": "medium", "tooltip":
                    "CPU only: ultrafast is quickest with larger files; veryslow takes longer for better "
                    "compression. Medium is balanced. Ignored for NVIDIA encoding."}),
                "save_metadata": ("BOOLEAN", {"default": True, "tooltip":
                    "Embed the ComfyUI workflow and prompt in the MP4 when supplied by ComfyUI."}),
            },
            "hidden": {"prompt": "PROMPT", "extra_pnginfo": "EXTRA_PNGINFO", "unique_id": "UNIQUE_ID"},
        }

    def save_video(self, images: Any, source_fps: str = "24", target_fps: str = "",
                   target_frames: str = "", width: str = "1920", height: str = "1080",
                   resize_method: str = "bicubic", resize_mode: str = "crop", cq: int = 19,
                   preset: str = "p5", filename_prefix: str = "video_extended", save_output: bool = True,
                   audio: dict[str, Any] | None = None, include_audio: bool = True,
                   prompt: Any = None, extra_pnginfo: dict[str, Any] | None = None,
                   unique_id: str | None = None, encoder: str = "h264_nvenc",
                   cpu_preset: str = "medium", save_metadata: bool = True) -> dict[str, Any]:
        """Encode in an owned staging directory, verify, then publish a single final MP4."""
        started = time.monotonic()
        context = f"filename_prefix={filename_prefix!r}"
        try:
            import folder_paths

            if len(images) < 1 or len(images[0].shape) != 3 or images[0].shape[-1] not in (3, 4):
                raise ValueError("images must be a nonempty RGB/RGBA IMAGE batch")
            if not isinstance(cq, int) or not 0 <= cq <= 51:
                raise ValueError("CQ/CRF must be an integer in [0, 51]")
            if encoder not in {"h264_nvenc", "libx264"}:
                raise ValueError("encoder must be h264_nvenc or libx264")
            if encoder == "h264_nvenc" and preset not in {"p4", "p5", "p6", "p7"}:
                raise ValueError("NVIDIA preset must be p4/p5/p6/p7")
            if encoder == "libx264" and cpu_preset not in CPU_PRESETS:
                raise ValueError(f"cpu_preset must be one of {CPU_PRESETS}")
            profile = CPU_PROFILE if encoder == "libx264" else PROFILE
            active_preset = cpu_preset if encoder == "libx264" else preset
            quality_mode = "CRF" if encoder == "libx264" else "CQ"
            count = parse_frames(target_frames, len(images))
            source_rate = parse_fps(source_fps)
            rate = parse_fps(target_fps) if str(target_fps).strip() else source_rate
            unspecified = ("" if target_frames is None else str(target_frames).strip().lower()) in UNSPECIFIED
            if unspecified and rate != source_rate:
                raise ValueError("Different target_fps requires an explicit positive target_frames")
            source_size = (int(images[0].shape[1]), int(images[0].shape[0]))
            size = dimensions(source_size, width, height)
            filters = resize_filter(source_size, size, width, height, resize_mode, resize_method)
            if not str(width).strip() and not str(height).strip() and size != source_size:
                LOGGER.warning(f"Odd source dimensions {source_size}: minimal right/bottom padding to {size}")
            source_duration = Fraction(len(images), 1) / source_rate
            duration = Fraction(count, 1) / rate
            enabled_audio = audio is not None and include_audio
            cls, ffmpeg = upstream()
            output_dir = folder_paths.get_output_directory() if save_output else folder_paths.get_temp_directory()
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            context = f"output_dir={Path(output_dir).resolve()}, filename_prefix={filename_prefix!r}"
            probe = check_cpu_h264(ffmpeg, output_dir) if encoder == "libx264" else check_nvenc(ffmpeg, output_dir)
            LOGGER.info(
                f"Frames {len(images)} -> {count}; FPS {source_rate} -> {rate}; "
                f"duration {float(source_duration):.6f}s -> {float(duration):.6f}s; "
                f"resolution {source_size} -> {size}; resize={resize_mode}/{resize_method}; "
                f"retime={count != len(images) or rate != source_rate}; audio={enabled_audio}; "
                f"speed_factor={float(source_duration / duration):.9g}; "
                f"encoder={encoder} {quality_mode}={cq} preset={active_preset}; "
                f"FFmpeg={ffmpeg}"
            )
            metadata = deepcopy(extra_pnginfo or {})
            metadata.setdefault("workflow", {}).setdefault("extra", {}).update(
                {"VHS_MetadataImage": False, "VHS_KeepIntermediate": False}
            )
            frames = images if count == len(images) else FrameMapping(images, count)
            with tempfile.TemporaryDirectory(prefix="vhse_", dir=output_dir) as stage:
                relative = Path(stage).relative_to(output_dir).as_posix()
                result = cls().combine_video(
                    images=frames, frame_rate=rate, loop_count=0, filename_prefix=f"{relative}/video",
                    format=f"video/{profile}", pingpong=False, save_output=save_output, audio=None,
                    prompt=prompt, extra_pnginfo=metadata, unique_id=unique_id,
                    cq=cq, preset=preset, cpu_preset=cpu_preset, vhse_filter=filters,
                )
                if not isinstance(result, dict) or "result" not in result or "ui" not in result:
                    raise RuntimeError("Incompatible VHS return value; expected result and ui preview")
                previews = result["ui"].get("gifs", [])
                if not previews or not isinstance(previews[0], dict):
                    raise RuntimeError("Incompatible VHS return value; expected a video preview descriptor")
                preview = dict(previews[0])
                video = Path(result["result"][0][1][-1]).resolve()
                if video.parent != Path(stage).resolve():
                    raise RuntimeError(f"VHS returned a path outside the owned staging directory: {video}")
                final = video
                metadata_path = Path(stage) / "workflow.ffmetadata" if save_metadata else None
                expected_metadata = write_metadata(metadata_path, prompt, extra_pnginfo) if save_metadata else {}
                if enabled_audio:
                    final = Path(stage) / "audio.mp4"
                    mux_audio(ffmpeg, video, final, audio, source_duration, duration, metadata_path)
                elif metadata_path is not None:
                    final = Path(stage) / "metadata.mp4"
                    mux_metadata(ffmpeg, video, final, metadata_path)
                verify_output(probe, ffmpeg, final, count, rate, size, enabled_audio)
                if save_metadata:
                    verify_metadata(probe, final, expected_metadata)
                path, subfolder = publish(final, filename_prefix, output_dir)
            preview.update(filename=path.name, subfolder=subfolder, fullpath=str(path),
                           format=f"video/{profile}", frame_rate=float(rate))
            preview.pop("workflow", None)
            LOGGER.info(f"Saved {path.resolve()} in {time.monotonic() - started:.3f}s")
            return {"ui": {"gifs": [preview]}, "result": ((save_output, [str(path)]),)}
        except Exception:
            LOGGER.error(f"VHSE save failed ({context})\n{traceback.format_exc()}")
            raise
