"""Real software H.264 saves independent of NVIDIA GPU availability."""

from fractions import Fraction
from pathlib import Path
from typing import Any
import os
import shutil
import sys
import tempfile

import pytest

from media_utils import check_cpu_h264, run, verify_output
from test_support import ROOT, main_test, reference_harness
from test_video_integration import batch, tone


@pytest.fixture(scope="module")
def cpu_ffmpeg() -> str:
    """Require libx264 without probing or requiring NVENC."""
    binary = os.environ.get("VHS_FORCE_FFMPEG_PATH") or shutil.which("ffmpeg")
    if not binary:
        pytest.skip("FFmpeg is absent")
    encoders = run([binary, "-hide_banner", "-encoders"])
    if b"libx264" not in encoders:
        pytest.skip("FFmpeg has no libx264 encoder")
    return binary


@pytest.mark.parametrize("preset,fps,audio", [("medium", "24", False), ("veryfast", "24000/1001", True),
                                            ("ultrafast", "30", True)])
def test_cpu_save(monkeypatch: Any, cpu_ffmpeg: str, preset: str, fps: str, audio: bool) -> None:
    """Retime, resize, mux and verify actual CPU output without invoking the NVIDIA probe."""
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="cpu_test_") as directory:
        output = Path(directory)
        package = reference_harness(monkeypatch, output, cpu_ffmpeg)
        adapter = sys.modules["vhse_test.video_save"]

        def unexpected_nvenc(*args: Any, **kwargs: Any) -> None:
            """Fail if software encoding attempts to access NVENC."""
            raise AssertionError("CPU path must not check NVENC")

        monkeypatch.setattr(adapter, "check_nvenc", unexpected_nvenc)
        result = package.VHSVideoSaveExtended().save_video(
            batch(24), encoder="libx264", cpu_preset=preset, target_frames="31", target_fps=fps,
            width="320", height="180", audio=tone(1) if audio else None,
        )
        path = Path(result["result"][0][1][0])
        probe = check_cpu_h264(cpu_ffmpeg, str(output))
        info = verify_output(probe, cpu_ffmpeg, path, 31, Fraction(fps), (320, 180), audio)
        assert "libx264" in info["streams"][0]["tags"]["encoder"]
        assert len(list(output.iterdir())) == 1
        assert "cpu_crf" in result["ui"]["gifs"][0]["format"]


def test_missing_libx264(monkeypatch: Any) -> None:
    """Return an actionable error rather than switching codecs."""
    import media_utils

    monkeypatch.setattr(media_utils, "find_ffprobe", lambda binary: "ffprobe")
    monkeypatch.setattr(media_utils, "run", lambda *args, **kwargs: b"h264_nvenc")
    with pytest.raises(RuntimeError, match="no libx264"):
        media_utils.check_cpu_h264("ffmpeg", "tmp")


if __name__ == "__main__":
    main_test(__file__)
