"""Real FFmpeg/NVENC checks through the original VHS VideoCombine implementation."""

from fractions import Fraction
from pathlib import Path
from typing import Any
import json
import logging
import os
import shutil
import tempfile

import numpy as np
import pytest

from media_utils import check_nvenc, find_ffprobe, run, verify_output
from test_support import ArrayTensor, ROOT, main_test, reference_harness


@pytest.fixture(scope="module")
def ffmpeg() -> str:
    """Skip real GPU tests cleanly if this host cannot encode NVENC."""
    path = os.environ.get("VHS_FORCE_FFMPEG_PATH") or shutil.which("ffmpeg")
    if not path:
        pytest.skip("FFmpeg is not installed")
    (ROOT / "tmp").mkdir(exist_ok=True)
    try:
        check_nvenc(path, str(ROOT / "tmp"))
    except RuntimeError as exc:
        pytest.skip(f"NVENC unavailable: {exc}")
    return path


@pytest.fixture
def output() -> Any:
    """Keep all integration output in the repository tmp directory."""
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="test_") as directory:
        yield Path(directory)


def batch(count: int, width: int = 192, height: int = 128) -> ArrayTensor:
    """Create distinct first and last frames with a spatial gradient."""
    array = np.zeros((count, height, width, 3), dtype=np.float32)
    array[..., 0] = np.linspace(0.05, 0.85, count)[:, None, None]
    array[..., 1] = np.linspace(0, 1, width)[None, None, :]
    array[..., 2] = np.linspace(0, 1, height)[None, :, None]
    return ArrayTensor(array)


def tone(duration: float, rate: int = 48000) -> dict[str, Any]:
    """Generate two-channel reference audio with a tone starting at time zero."""
    values = 0.2 * np.sin(2 * np.pi * 440 * np.arange(round(duration * rate)) / rate)
    return {"waveform": ArrayTensor(np.stack([values, values])[None].astype(np.float32)), "sample_rate": rate}


@pytest.mark.parametrize("source,target", [(90, 97), (107, 97), (141, 134), (192, 186), (73, 81),
                                          (22, 5), (5, 22), (1, 24), (24, 24), (1, 1)])
def test_exact_frames(monkeypatch: Any, output: Path, ffmpeg: str, source: int, target: int) -> None:
    """Encode all requested retiming pairs and compare decoded endpoint PSNR."""
    package = reference_harness(monkeypatch, output, ffmpeg)
    images = batch(source)
    result = package.VHSVideoSaveExtended().save_video(images, target_frames=str(target), width="", height="")
    path = Path(result["result"][0][1][0])
    assert list(output.glob("*.mp4")) == [path]
    assert len(list(output.iterdir())) == 1
    assert result["ui"]["gifs"][0]["filename"] == path.name
    decoded = np.frombuffer(run([ffmpeg, "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt",
                                 "rgb24", "-"], timeout=120), dtype=np.uint8).reshape(target, 128, 192, 3)
    for original, encoded in ((images.array[0], decoded[0]), (images.array[-1], decoded[-1])):
        mse = np.mean((original * 255 - encoded.astype(np.float32)) ** 2)
        psnr = 10 * np.log10(255 ** 2 / max(mse, 1e-12))
        assert psnr > 30, psnr


@pytest.mark.parametrize("settings,size", [
    ({}, (1920, 1080)),
    ({"width": "320", "height": "", "resize_method": "lanczos"}, (320, 214)),
    ({"width": "", "height": "256", "resize_method": "bilinear"}, (384, 256)),
    ({"width": "320", "height": "180", "resize_mode": "pad"}, (320, 180)),
    ({"width": "320", "height": "180", "resize_mode": "stretch"}, (320, 180)),
    ({"width": "", "height": ""}, (194, 130)),
])
def test_geometry(monkeypatch: Any, output: Path, ffmpeg: str, settings: dict[str, Any],
                  size: tuple[int, int]) -> None:
    """Test FullHD defaults, derived sizes, all modes and minimal odd-source padding."""
    package = reference_harness(monkeypatch, output, ffmpeg)
    odd = settings == {"width": "", "height": ""}
    images = batch(4, 193, 129) if odd else batch(4)
    result = package.VHSVideoSaveExtended().save_video(images, **settings)
    path = Path(result["result"][0][1][0])
    verify_output(find_ffprobe(ffmpeg), ffmpeg, path, 4, Fraction(24), size, False)


@pytest.mark.parametrize("duration,target,rate,include", [
    (1.0, "", "", True), (0.4, "", "", True), (1.6, "", "", True),
    (1.0, "12", "", True), (1.0, "48", "", True), (1.0, "24", "30", True),
    (1.0, "2", "", True), (1.0, "192", "", True), (1.0, "", "", False),
])
def test_audio(monkeypatch: Any, output: Path, ffmpeg: str, duration: float, target: str,
               rate: str, include: bool, caplog: Any) -> None:
    """Verify audio mismatch normalization, tempo factors, pitch, start and sync."""
    package = reference_harness(monkeypatch, output, ffmpeg)
    with caplog.at_level(logging.WARNING):
        result = package.VHSVideoSaveExtended().save_video(
            batch(24), target_frames=target, target_fps=rate, width="", height="",
            audio=tone(duration), include_audio=include,
        )
    if abs(duration - 1) > 0.2:
        assert "differs from source video" in caplog.text
    if include:
        path = result["result"][0][1][0]
        decoded = np.frombuffer(run([ffmpeg, "-v", "error", "-i", path, "-map", "0:a:0", "-f", "f32le",
                                     "-ac", "1", "-ar", "48000", "-" ]), dtype="<f4")
        assert np.max(np.abs(decoded[:2400])) > 0.05
        spectrum = np.abs(np.fft.rfft(decoded[:min(len(decoded), 12000)]))
        peak = np.argmax(spectrum) * 48000 / min(len(decoded), 12000)
        assert abs(peak - 440) < 20


def test_rational_fps(monkeypatch: Any, output: Path, ffmpeg: str) -> None:
    """Confirm VHS passes a Fraction to FFmpeg without decimal approximation."""
    package = reference_harness(monkeypatch, output, ffmpeg)
    result = package.VHSVideoSaveExtended().save_video(batch(24), source_fps="24000/1001", width="", height="")
    info = verify_output(find_ffprobe(ffmpeg), ffmpeg, Path(result["result"][0][1][0]), 24,
                         Fraction(24000, 1001), (192, 128), False)
    assert info["streams"][0]["avg_frame_rate"] == "24000/1001"


def test_real_torch(monkeypatch: Any, output: Path, ffmpeg: str) -> None:
    """Exercise actual torch tensors when the test interpreter provides torch."""
    torch = pytest.importorskip("torch")
    import sys

    package = reference_harness(monkeypatch, output, ffmpeg)
    sys.modules["vhse_reference_nodes"].torch = torch
    images = torch.from_numpy(batch(5).array)
    audio = tone(5 / 24)
    audio["waveform"] = torch.from_numpy(audio["waveform"].array)
    result = package.VHSVideoSaveExtended().save_video(images, target_frames="9", width="", height="", audio=audio)
    assert Path(result["result"][0][1][0]).is_file()


def test_safe_publication(monkeypatch: Any, output: Path, ffmpeg: str) -> None:
    """Keep existing outputs, isolate metadata changes, and honor temporary output mode."""
    package = reference_harness(monkeypatch, output, ffmpeg)
    existing = output / "video_extended_00001.mp4"
    existing.write_bytes(b"unrelated")
    metadata = {"workflow": {"extra": {"VHS_MetadataImage": True}}}
    result = package.VHSVideoSaveExtended().save_video(batch(2), width="", height="", save_output=False,
                                                      extra_pnginfo=metadata)
    assert existing.read_bytes() == b"unrelated"
    assert metadata["workflow"]["extra"]["VHS_MetadataImage"] is True
    assert result["result"][0][0] is False
    assert result["ui"]["gifs"][0]["type"] == "temp"
    assert len(list(output.iterdir())) == 2


def test_verification_failure(monkeypatch: Any, output: Path, ffmpeg: str) -> None:
    """Do not publish or retain output rejected by post-encode verification."""
    import sys

    package = reference_harness(monkeypatch, output, ffmpeg)

    def reject(*args: Any, **kwargs: Any) -> None:
        """Simulate a post-encode verification failure."""
        raise RuntimeError("verification deliberately failed")

    monkeypatch.setattr(sys.modules["vhse_test.video_save"], "verify_output", reject)
    with pytest.raises(RuntimeError, match="verification deliberately"):
        package.VHSVideoSaveExtended().save_video(batch(2), width="", height="")
    assert not list(output.iterdir())


@pytest.mark.parametrize("mode", ["crop", "pad", "stretch"])
def test_aspect_content(monkeypatch: Any, output: Path, ffmpeg: str, mode: str) -> None:
    """Check actual decoded geometry content, rather than only resolution metadata."""
    package = reference_harness(monkeypatch, output, ffmpeg)
    array = np.zeros((2, 256, 256, 3), dtype=np.float32)
    array[..., 1] = 0.8
    array[:, :48, :, :] = [0.8, 0, 0]
    array[:, -48:, :, :] = [0.8, 0, 0]
    result = package.VHSVideoSaveExtended().save_video(ArrayTensor(array), width="320", height="180",
                                                      resize_mode=mode)
    path = result["result"][0][1][0]
    data = run([ffmpeg, "-v", "error", "-i", path, "-frames:v", "1", "-f", "rawvideo",
                "-pix_fmt", "rgb24", "-"])
    frame = np.frombuffer(data, dtype=np.uint8).reshape(180, 320, 3)
    assert frame[90, 160, 1] > 180
    if mode == "crop":
        assert frame[5, 5, 1] > 180 and frame[175, 315, 1] > 180
    elif mode == "pad":
        assert np.max(frame[90, 5]) < 10 and frame[5, 160, 0] > 180
    else:
        assert frame[5, 5, 0] > 180 and frame[175, 315, 0] > 180


def test_incorrect_expected_count(monkeypatch: Any, output: Path, ffmpeg: str) -> None:
    """Reject a real encoded file when its decoded count differs from the contract."""
    package = reference_harness(monkeypatch, output, ffmpeg)
    result = package.VHSVideoSaveExtended().save_video(batch(3), width="", height="")
    path = Path(result["result"][0][1][0])
    with pytest.raises(RuntimeError, match="verification failed"):
        verify_output(find_ffprobe(ffmpeg), ffmpeg, path, 4, Fraction(24), (192, 128), False)


def test_fly001(monkeypatch: Any, output: Path, ffmpeg: str) -> None:
    """Encode the user-provided real clip with source audio and FullHD retiming."""
    source = ROOT / "tests/fly001.mp4"
    if not source.is_file():
        pytest.skip("Optional user sample tests/fly001.mp4 is absent")
    info = json.loads(run([find_ffprobe(ffmpeg), "-v", "error", "-show_streams", "-of", "json", str(source)]))
    video = next(stream for stream in info["streams"] if stream["codec_type"] == "video")
    w, h = video["width"], video["height"]
    data = run([ffmpeg, "-v", "error", "-i", str(source), "-map", "0:v:0", "-f", "rawvideo",
                "-pix_fmt", "rgb24", "-"], timeout=120)
    # The input IMAGE batch itself is required; the node does not duplicate it for retiming or resize.
    frames = ArrayTensor(np.frombuffer(data, dtype=np.uint8).reshape(-1, h, w, 3).astype(np.float32) / 255)
    del data
    audio_info = next(stream for stream in info["streams"] if stream["codec_type"] == "audio")
    audio_data = run([ffmpeg, "-v", "error", "-i", str(source), "-map", "0:a:0", "-f", "f32le", "-"])
    waveform = np.frombuffer(audio_data, dtype="<f4").reshape(-1, audio_info["channels"]).T[None]
    audio = {"waveform": ArrayTensor(waveform), "sample_rate": int(audio_info["sample_rate"])}
    package = reference_harness(monkeypatch, output, ffmpeg)
    for target, rate in (("", ""), ("300", "24"), ("180", "")):
        result = package.VHSVideoSaveExtended().save_video(
            frames, source_fps=video["avg_frame_rate"], target_frames=target, target_fps=rate,
            filename_prefix="fly001", audio=audio,
        )
        assert Path(result["result"][0][1][0]).is_file()
    assert len(list(output.iterdir())) == 3


if __name__ == "__main__":
    main_test(__file__)
