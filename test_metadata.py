"""Actual MP4 workflow metadata checks for CPU/NVENC and optional audio."""

from pathlib import Path
from typing import Any
import json
import tempfile

import pytest

from media_utils import find_ffprobe, run
from test_cpu_encoding import cpu_ffmpeg
from test_support import ROOT, main_test, reference_harness
from test_video_integration import batch, ffmpeg, tone


@pytest.mark.parametrize("encoder", ["libx264", "h264_nvenc"])
@pytest.mark.parametrize("audio", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_metadata(monkeypatch: Any, request: Any, encoder: str, audio: bool, enabled: bool) -> None:
    """Round-trip workflow/prompt and escaped Unicode tags through the final MP4."""
    binary = request.getfixturevalue("cpu_ffmpeg" if encoder == "libx264" else "ffmpeg")
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="metadata_test_") as directory:
        package = reference_harness(monkeypatch, Path(directory), binary)
        prompt = {"1": {"class_type": "Example", "inputs": {"text": "test; # = \\ \n тест"}}}
        workflow = {"version": 0.4, "nodes": [], "extra": {"VHS_MetadataImage": True}}
        extra = {"workflow": workflow, "custom_note": "test; # = \\ \n тест"}
        # Omit the argument in the enabled case to verify that metadata is on by default.
        options = {} if enabled else {"save_metadata": False}
        result = package.VHSVideoSaveExtended().save_video(
            batch(3), encoder=encoder, width="", height="", prompt=prompt, extra_pnginfo=extra,
            audio=tone(3 / 24) if audio else None, **options,
        )
        path = Path(result["result"][0][1][0])
        info = json.loads(run([find_ffprobe(binary), "-v", "error", "-show_entries", "format_tags",
                               "-of", "json", str(path)]))
        tags = info["format"]["tags"]
        if enabled:
            assert json.loads(tags["prompt"]) == prompt
            assert json.loads(tags["workflow"]) == workflow
            assert json.loads(tags["custom_note"]) == extra["custom_note"]
            comment = json.loads(tags["comment"])
            assert json.loads(comment["prompt"]) == prompt
            assert comment["workflow"] == workflow
        else:
            assert not {"prompt", "workflow", "comment", "custom_note"}.intersection(tags)
        assert workflow["extra"]["VHS_MetadataImage"] is True
        assert len(list(Path(directory).iterdir())) == 1


if __name__ == "__main__":
    main_test(__file__)
