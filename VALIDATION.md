# Validation record — 2026-10-21

## Local environment

- Windows, NVIDIA GeForce RTX 4060 Ti, NVIDIA driver 595.79.
- Project `.venv`: Python 3.13.15, pytest 9.1.1, numpy 2.5.4, Pillow 13.0.0.
- FFmpeg: `n7.0.1-4-gb059dc4ced-g49c3918c1a+149`, actual `h264_nvenc` runtime probe passed.
- Reference VHS commit: `4d907bee61e92c2e65af3bd6383a4e4d356126d1`.
- Reference VHS working tree remained unchanged.

## Executed checks

`python -m pytest -q` in the project `.venv`: **80 passed, 2 skipped** (34.85 seconds).
The two skipped tests require actual torch in the test interpreter and an explicit live ComfyUI URL.
Both were executed separately in suitable environments:

- System Python 3.11.9 with torch 2.7.1+cu128: actual-torch adapter test **1 passed**.
- Live ComfyUI at `http://127.0.0.1:8188`: HTTP API integration **1 passed**.
- `node --input-type=module --check` on the frontend source: passed.

The reference harness executes original VHS methods and its actual filter-argument merge helper.
It replaces ComfyUI infrastructure only; most local tests use a minimal CPU tensor protocol.
The separate torch check uses genuine torch IMAGE and AUDIO tensors with the original VHS encoder methods.

Coverage includes parsing, exact integer mapping, endpoint preservation, compressed endpoint PSNR >30 dB,
FullHD default crop, decoded crop/pad/stretch content, derived dimensions, odd-source padding,
integer/rational FPS, absent/disabled audio, short/long audio, tempo extremes, audio pitch and initial sound,
decoded audio duration, CFR presentation timestamps, existing-file protection and failure cleanup.

### Real sample: tests/fly001.mp4

Input: 856×480, 243 frames, 16 FPS, video duration 15.1875 seconds, AAC stereo at 44100 Hz.
Three actual NVENC FullHD/audio saves passed:

| Operation | Decoded frames | FPS | Video duration |
| --- | --- | --- | --- |
| Preserve original timing | 243 | 16 | 15.1875 s |
| Retime and change FPS | 300 | 24 | 12.5 s |
| Reduce frame count | 180 | 16 | 11.25 s |

Each save passed frame, resolution, H.264/yuv420p, SAR, CFR and decoded audio synchronization verification.
These test outputs were temporary and were removed by the tests.

## Live ComfyUI API check

Command: set `VHSE_COMFY_URL=http://127.0.0.1:8188`, then run `python -m pytest test_comfy_api.py -q`.
Successful prompt ID: `a41be7ce-3f70-4135-912f-ff9b496f37a1`.

Confirmed:

- Independent registration of `VHSE_VideoSave` and original `VHS_VideoCombine`.
- Serving the new frontend extension through `/extensions`.
- Uploading the sample and decoding its first 24 frames through `VHS_LoadVideo`.
- VHSE FullHD output: 37 decoded frames at 24 FPS, H.264/yuv420p, square pixels and AAC.
- VHSE video duration: 1.541667 seconds; decoded audio: 1.555737 seconds.
- AAC tolerance at 44100 Hz: 0.048440 seconds; synchronization passed.
- VHSE with omitted `target_frames`, empty dimensions and no audio: 24 frames, 16 FPS, 856×480.
- Original VHS separately encoded 24 frames at 16 FPS, preserving its normal behavior.
- All three files were fetched through `/view` and verified locally, not just checked in API metadata.

The first live check found VHS's tuple-valued format-folder paths incompatible with ComfyUI's append helper.
The adapter now converts those paths to a list while preserving existing paths and extensions.
A regression test covers this case and repeated registration. Original VHS source was not edited.

The API test leaves its uploaded input and generated files in ComfyUI input/temp directories.
It does not delete server-side user data. Downloaded validation copies are removed locally.

## Limits of verification

- Browser playback and the canvas preview layout were not visually checked: no browser is exposed to the agent.
  The frontend module passes syntax validation and is served by the running ComfyUI.
- Linux, Docker and RunPod were not available for execution; Windows success does not establish those runtimes.
- Python 3.12 was not executed. Python 3.13 local checks and the running ComfyUI API were exercised.
- The inspected upstream commit is pinned above; future VHS updates can change adapter interfaces.
- File publication copies an already verified MP4; it does not re-encode video, but briefly needs duplicate disk space.

## CPU H.264 and GUI hints update

After adding `encoder=libx264`, `cpu_preset` and English input tooltips:

- Full project `.venv` suite: **86 passed, 2 skipped** in 35.23 seconds.
- The skipped cases remain actual torch in this interpreter and the opt-in live API check.
- Actual libx264 encoding through the original VHS passed with medium, veryfast and ultrafast presets.
- CPU saves verified exact frames, resize, CFR, rational FPS, AAC audio and decoded audio synchronization.
- Encoded stream metadata confirms libx264. CPU tests explicitly reject any call to the NVIDIA runtime probe.
- Missing libx264 and invalid encoder/preset requests produce errors; there is no automatic codec fallback.
- Updated live API coverage includes CPU output and presence of GUI tooltips in the server input schema.
  The installed ComfyUI copy needs updating before this additional live check can run.

## Embedded metadata update

- Full local suite: **94 passed, 2 skipped** in 41.21 seconds.
- Eight actual MP4 saves covered CPU/NVENC, audio present/absent and metadata enabled/disabled.
- Enabled-by-default saves round-tripped JSON workflow, prompt, extra fields, Unicode and escaped characters.
- Disabled saves contained none of the supplied workflow, prompt, comment or custom fields.
- Metadata is verified on the final muxed output before publication. Video is copied without re-encoding.
- These metadata changes have not yet been tested in the user's installed ComfyUI copy.
- The example API workflow now uses loader `force_rate=24`, `source_fps="24"` and `target_fps="24"`.
