"""API validation, dependency failures and safe output ownership checks."""

from pathlib import Path
from typing import Any
import shutil
import sys
import types

import pytest

from test_support import ArrayTensor, load_package, main_test, reference_harness
import numpy as np


def test_registration() -> None:
    """Register only VHSE and permit omitted target_frames in ComfyUI API validation."""
    package = load_package()
    assert set(package.NODE_CLASS_MAPPINGS) == {"VHSE_VideoSave"}
    assert "target_frames" in package.VHSVideoSaveExtended.INPUT_TYPES()["optional"]


def test_missing_vhs(monkeypatch: Any) -> None:
    """Import without VHS, then fail clearly only at execution."""
    package = load_package()
    nodes = types.ModuleType("nodes")
    nodes.NODE_CLASS_MAPPINGS = {}
    monkeypatch.setitem(sys.modules, "nodes", nodes)
    monkeypatch.setitem(sys.modules, "folder_paths", types.ModuleType("folder_paths"))
    with pytest.raises(RuntimeError, match="Install and enable"):
        package.VHSVideoSaveExtended().save_video(ArrayTensor(np.zeros((2, 128, 192, 3))))


@pytest.mark.parametrize("kwargs", [{"target_frames": "1"}, {"target_fps": "30"}, {"width": "191"},
                                    {"cq": 52}, {"preset": "p1"}, {"resize_mode": "bad"},
                                    {"encoder": "bad"}, {"encoder": "libx264", "cpu_preset": "p5"}])
def test_validation(monkeypatch: Any, kwargs: dict[str, Any]) -> None:
    """Reject bad requests before FFmpeg execution."""
    package = load_package()
    monkeypatch.setitem(sys.modules, "folder_paths", types.ModuleType("folder_paths"))
    with pytest.raises(ValueError):
        package.VHSVideoSaveExtended().save_video(ArrayTensor(np.zeros((2, 128, 192, 3))), **kwargs)


def test_missing_nvenc(monkeypatch: Any) -> None:
    """Never substitute another codec when NVENC is absent."""
    import media_utils

    monkeypatch.setattr(media_utils, "find_ffprobe", lambda binary: "ffprobe")
    monkeypatch.setattr(media_utils, "run", lambda *args, **kwargs: b"libx264")
    with pytest.raises(RuntimeError, match="no h264_nvenc"):
        media_utils.check_nvenc("ffmpeg", "tmp")


def test_failure_cleanup(monkeypatch: Any) -> None:
    """Remove failed operation files and keep preexisting user output intact."""
    import tempfile
    from test_support import ROOT

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg is absent")
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
        output = Path(directory)
        existing = output / "user.mp4"
        existing.write_bytes(b"keep")
        package = reference_harness(monkeypatch, output, ffmpeg)
        module = sys.modules["vhse_test.video_save"]
        monkeypatch.setattr(module, "check_nvenc", lambda *args: "ffprobe")

        def failed(self: Any, **kwargs: Any) -> None:
            """Create a partial file in the owned stage then fail."""
            stage = output / Path(kwargs["filename_prefix"]).parent
            (stage / "partial.mp4").write_bytes(b"partial")
            raise RuntimeError("deliberate encode failure")

        monkeypatch.setattr(sys.modules["nodes"].NODE_CLASS_MAPPINGS["VHS_VideoCombine"], "combine_video", failed)
        # Preserve the signature check while deliberately failing the actual delegate.
        monkeypatch.setattr(module, "upstream", lambda: (
            sys.modules["nodes"].NODE_CLASS_MAPPINGS["VHS_VideoCombine"], ffmpeg))
        with pytest.raises(RuntimeError, match="deliberate"):
            package.VHSVideoSaveExtended().save_video(ArrayTensor(np.zeros((2, 128, 192, 3))))
        assert list(output.iterdir()) == [existing]
        assert existing.read_bytes() == b"keep"


def test_format_registration(monkeypatch: Any) -> None:
    """Normalize VHS tuple paths without losing existing formats or registering duplicates."""
    import tempfile
    from test_support import ROOT

    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp") as directory:
        reference_harness(monkeypatch, Path(directory), "ffmpeg")
        folder = sys.modules["folder_paths"]
        extensions = {".json"}
        folder.folder_names_and_paths["VHS_video_formats"] = (("existing",), extensions)
        adapter = sys.modules["vhse_test.video_save"]
        adapter.upstream()
        adapter.upstream()
        paths, actual_extensions = folder.folder_names_and_paths["VHS_video_formats"]
        assert paths == ["existing", str(adapter.PROFILE_DIR)]
        assert actual_extensions is extensions


if __name__ == "__main__":
    main_test(__file__)
