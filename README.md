# ComfyUI-VideoVHS-Extended

An independent extension providing **Video Save Extended (VHS)** (`VHSE_VideoSave`).
It saves an IMAGE batch as H.264 MP4 using NVIDIA NVENC or CPU libx264, with optional exact frame-count retiming,
FFmpeg resizing and pitch-preserving audio retiming.

This is not an official VideoHelperSuite component or a replacement for VideoHelperSuite.
Keep the original package installed. This extension registers only its own `VHSE_VideoSave` node.

## Installation

Requires Python 3.12+ (including 3.13), ComfyUI and the original
[ComfyUI-VideoHelperSuite](https://github.com/Kosinkadink/ComfyUI-VideoHelperSuite).

1. Place this repository in `ComfyUI/custom_nodes/ComfyUI-VideoVHS-Extended`.
2. Install original VideoHelperSuite normally, including its dependencies.
3. Install FFmpeg with `h264_nvenc` for NVIDIA or `libx264` for CPU, plus an `ffprobe` executable.
4. For NVIDIA encoding, ensure the GPU, NVIDIA driver and runtime encode library are available.
5. Restart ComfyUI and add **Video VHS Extended → Video Save Extended (VHS)**.

Select `encoder=libx264` for CPU encoding without an NVIDIA GPU. The default remains `h264_nvenc`.
There are no additional Python runtime dependencies beyond those already provided by ComfyUI/VHS.
Do not install a separate torch build into your ComfyUI environment for this extension.

### Linux, Docker and RunPod

Choose FFmpeg before starting ComfyUI, for example:

```bash
export VHS_FORCE_FFMPEG_PATH=/usr/bin/ffmpeg
export NVIDIA_DRIVER_CAPABILITIES=compute,video,utility
python main.py --listen 0.0.0.0
```

Pass the GPU through to the container (for Docker, typically `--gpus all`).
The image must include an NVENC-capable FFmpeg; the host NVIDIA driver and container runtime must expose
the video encode library. An encoder listed by `ffmpeg -encoders` does not prove runtime availability.
The node tests the chosen encoder using the actual FFmpeg selected by VHS before every save.
CPU encoding probes only libx264 and requires no NVIDIA libraries. A failed NVIDIA encode does not automatically
switch to CPU: choose `libx264` explicitly. Changing `VHS_FORCE_FFMPEG_PATH` after VHS loads requires a restart.

On Windows, set `VHS_FORCE_FFMPEG_PATH` to the full executable path before starting ComfyUI.
`ffprobe` is selected beside that executable when available, otherwise from PATH.

## Inputs

| Input | Default | Behavior |
| --- | --- | --- |
| `images` | required | Nonempty RGB/RGBA ComfyUI IMAGE batch |
| `source_fps` | `"24"` | Incoming sequence FPS; integer, decimal or rational string |
| `target_fps` | `""` | Empty uses source FPS; different FPS requires explicit positive `target_frames` |
| `target_frames` | `""` | Exact output count; empty or sentinel disables retiming |
| `width` | `"1920"` | Target width, or empty for automatic calculation |
| `height` | `"1080"` | Target height, or empty for automatic calculation |
| `resize_method` | `bicubic` | `bicubic`, `lanczos`, `bilinear` |
| `resize_mode` | `crop` | `crop`, `pad`, `stretch` |
| `cq` | `19` | NVENC CQ or CPU CRF, integer 0–51; these two scales are not equivalent |
| `preset` | `p5` | NVIDIA only: `p4`, `p5`, `p6`, `p7`; ignored for CPU |
| `filename_prefix` | `video_extended` | Standard ComfyUI prefix, including subfolders |
| `save_output` | `true` | Save in ComfyUI output; false uses ComfyUI temp |
| `audio` | absent | Optional ComfyUI AUDIO, shape `[1, channels, samples]` |
| `include_audio` | `true` | False ignores supplied audio |
| `encoder` | `h264_nvenc` | `h264_nvenc` for NVIDIA GPU; `libx264` for software CPU H.264 |
| `cpu_preset` | `medium` | CPU only: `ultrafast` through `veryslow`; ignored for NVIDIA |
| `save_metadata` | `true` | Embed the supplied ComfyUI workflow, prompt and extra metadata inside the MP4 |

All visible inputs have short English hover hints, including both preset fields.
`encoder` and `cpu_preset` are optional, so existing API workflows retain NVENC and p5 without changes.
For NVIDIA, p4 is faster and p7 spends more encoding effort on compression; p5 balances speed and compression.
CPU presets are `ultrafast`, `superfast`, `veryfast`, `faster`, `fast`, `medium`, `slow`, `slower`, `veryslow`.
Faster CPU presets usually make larger files at the same CRF; slower ones spend more CPU time on compression.
Lower CQ/CRF values usually increase quality and file size. The same number does not guarantee equal quality
between NVENC and libx264. CPU mode passes `cq` as `-crf`, with no NVENC-specific flags.

`target_frames` is an optional STRING widget so an API request can omit it without failing ComfyUI's
required-input validation. All other processing semantics are identical to a blank widget.

FPS must be greater than zero and at most 1000. Integer rates 24/25/30 and rational rates such as
`24000/1001` are passed to FFmpeg exactly using `Fraction`, without floating-point rate conversion.
Set `source_fps` to the actual input sequence FPS; IMAGE tensors do not carry this information.

### Exact frame count

Empty, `"0"`, `"default"`, `"auto"`, `"none"` (case-insensitive, whitespace ignored), or an omitted API field
preserves the original count and source FPS. An explicitly different target FPS is then an error.
Otherwise, a positive integer is authoritative. Negative values, decimal counts and malformed text fail.

Uniform nearest-frame sampling preserves the first and last source frames. Exact half ties round upward,
using integer arithmetic. A lazy sequence references the original batch; it does not concatenate another batch.
Repeated frames are allowed. One input frame may be repeated; reducing multiple source frames to one is rejected.
Equal input/output counts reuse the original batch directly. No interpolation, blending or AI models are used.

Video duration is `output_frames / output_fps`; source duration is `source_frames / source_fps`.
The output is CFR. Changing count changes speed unless a target FPS compensates for that change.

### Dimensions and color

The default is bicubic proportional scaling followed by **center crop to 1920×1080**.
`pad` fits the image proportionally and centers it on black; `stretch` fills the requested dimensions.

- Both dimensions empty: preserve geometry; odd source dimensions receive minimal right/bottom black padding.
- One dimension empty: derive it from the source aspect ratio, rounding to the nearest even integer, ties upward.
- Both supplied: use exactly those even dimensions. Explicit odd dimensions are rejected, including a lone dimension.
- Explicit dimensions and resolved dimensions must be between 2 and 8192. Actual hardware limits may be lower.

Scaling occurs in the final FFmpeg encode, without a resized IMAGE batch or intermediate PNG sequence.
The profile disables VHS tensor padding and performs geometry adjustment in its single filter chain.
The output uses square pixels, yuv420p and the same RGB/BT.709 interpretation as the reference VHS NVENC profile.
NVIDIA mode uses `h264_nvenc`, `tune=hq`, `rc=vbr`, `b:v=0`. CPU mode uses `libx264` with CRF and `cpu_preset`.
Both use MP4 `+faststart` and separate VHS-compatible format profiles.

### Audio

Absent or disabled audio produces video only, with no artificial silence track.
Enabled audio starts at timestamp zero. A duration mismatch greater than 0.2 seconds produces a warning.
Before retiming, audio is padded or trimmed at the end to match the source video duration.
Pitch-preserving `atempo` uses `source_duration / target_duration`, with factors chained in the 0.5–2 range.
Equal durations do not apply a tempo filter. The final end is padded/trimmed to target duration.

The final mux copies the video stream and encodes AAC at 192 kbit/s. It does not use `-shortest` and never
encodes the video twice. Source sample rate and channel count are retained where AAC supports them;
unsupported layouts/rates fail through FFmpeg rather than silently discarding audio.

Decoded AAC length may include codec padding: verification allows two AAC frames (`2048 / sample_rate` seconds)
plus 2 ms, and checks audio start within one source sample. Decoded audio duration and tolerance are logged.

## Output, preview and errors

The node returns one `VHS_FILENAMES` output containing `(save_output, [final_mp4_path])`.
A small frontend extension supplies a native MP4 video preview with playback/audio controls.
No changes to the original VHS frontend are required. Workflow metadata PNG sidecars are suppressed.
`save_metadata` is enabled by default: the MP4 embeds the prompt, workflow and additional `extra_pnginfo`
provided by ComfyUI. Custom JSON tags and a legacy JSON comment are written and checked after the final mux.
Disable the switch to omit these fields. API callers must supply a GUI workflow through `extra_pnginfo.workflow`
if they want it embedded; the node cannot reconstruct the GUI graph from an API prompt alone.
Video-only saves use a stream-copy metadata mux. Saves with audio embed metadata in the existing audio mux pass.

VHS encodes inside an owned staging directory within the standard output/temp directory.
After verification, the MP4 is copied to a standard ComfyUI numbered filename using exclusive creation.
Existing files are never overwritten. Staging files are removed on success or failure.
This copy does not re-encode media, but requires temporary disk space for both copies during publication.

Every completed output is checked with ffprobe for exactly one H.264 video stream, decoded frame count,
exact FPS, CFR presentation timestamps, size, yuv420p, square pixels, start time and video duration.
Audio stream presence and decoded audio synchronization are checked when applicable.
Invalid media raises an error before publication. Errors include operation context and a stack trace;
FFmpeg error messages are limited to their last 6000 characters.

## API example

See [examples/api_workflow.json](examples/api_workflow.json). Put `fly001.mp4` in ComfyUI's input directory
before running this sample. The loader converts the clip to 24 FPS; both source and target FPS are 24.
The save node requests 300 frames with FullHD center crop. For a different source, change the filename;
if changing the loader FPS, update `source_fps` to match it.

To preserve frame count and resolution, use:

```json
{
    "target_frames": "",
    "target_fps": "",
    "width": "",
    "height": ""
}
```

Omit `audio` or set `include_audio=false` for video-only output. `class_type` must be `VHSE_VideoSave`.
To use CPU encoding, add `"encoder": "libx264"` and optionally `"cpu_preset": "medium"`.

## Compatibility and upstream updates

The inspected reference VHS commit is `4d907bee61e92c2e65af3bd6383a4e4d356126d1`.
The adapter resolves `nodes.NODE_CLASS_MAPPINGS["VHS_VideoCombine"]` at execution time, allowing either load order.
It registers its own format directory through `VHS_video_formats`, then calls `combine_video` with format widgets.
No VHS files or node mappings are replaced. A missing/incompatible VHS fails this node without blocking import.

Update VideoHelperSuite independently using its normal update mechanism. After updating, rerun the tests and
one real ComfyUI workflow. The integration depends on `combine_video`, additional-format filename lookup,
Fraction frame rates, sequence iteration, metadata controls and the `ui.gifs`/`VHS_FILENAMES` return schema.
These extension interfaces may change upstream; a matching node name alone is not proof of compatibility.

