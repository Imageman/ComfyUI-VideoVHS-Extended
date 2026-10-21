"""Opt-in end-to-end validation against a running ComfyUI HTTP API."""

from pathlib import Path
from typing import Any
from fractions import Fraction
import json
import logging
import os
import shutil
import tempfile
import time
import urllib.parse
import urllib.request
import uuid

import pytest

from media_utils import find_ffprobe, run, verify_output
from test_support import ROOT, main_test

LOGGER = logging.getLogger(__name__)
BASE = os.environ.get("VHSE_COMFY_URL", "").rstrip("/")
pytestmark = pytest.mark.skipif(not BASE, reason="Set VHSE_COMFY_URL to enable a live ComfyUI test")


def request(route: str, payload: dict[str, Any] | None = None) -> Any:
    """Read or post JSON to the explicit local ComfyUI server."""
    body = None if payload is None else json.dumps(payload).encode()
    headers = {} if body is None else {"Content-Type": "application/json"}
    with urllib.request.urlopen(urllib.request.Request(f"{BASE}{route}", data=body, headers=headers),
                                timeout=30) as response:
        return json.load(response)


def upload(path: Path) -> str:
    """Upload the supplied sample with a unique filename and overwrite disabled."""
    boundary = f"vhse_{uuid.uuid4().hex}"
    filename = f"vhse_test_{uuid.uuid4().hex}.mp4"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"image\"; "
            f"filename=\"{filename}\"\r\nContent-Type: video/mp4\r\n\r\n").encode()
    body += path.read_bytes()
    body += (f"\r\n--{boundary}\r\nContent-Disposition: form-data; name=\"overwrite\"\r\n\r\nfalse"
             f"\r\n--{boundary}--\r\n").encode()
    req = urllib.request.Request(f"{BASE}/upload/image", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    with urllib.request.urlopen(req, timeout=30) as response:
        info = json.load(response)
    return f"{info['subfolder']}/{info['name']}".lstrip("/")


def test_live_comfy() -> None:
    """Load and execute both nodes, then verify downloaded final media and preview assets."""
    info = request("/object_info")
    for name in ("VHSE_VideoSave", "VHS_VideoCombine", "VHS_LoadVideo"):
        assert name in info, f"Missing ComfyUI node: {name}"
    assert info["VHSE_VideoSave"]["python_module"] != info["VHS_VideoCombine"]["python_module"]
    assert "encoder" in info["VHSE_VideoSave"]["input"]["optional"], "Install the updated VHSE node"
    for group in ("required", "optional"):
        for field, declaration in info["VHSE_VideoSave"]["input"][group].items():
            assert len(declaration) > 1 and declaration[1].get("tooltip"), f"Missing GUI hint: {field}"
    extensions = request("/extensions")
    preview = next((item for item in extensions if item.endswith("/video_preview.js")), None)
    assert preview is not None, "VHSE frontend extension is not served"
    with urllib.request.urlopen(f"{BASE}{preview}", timeout=10) as response:
        assert b"VHSE.VideoPreview" in response.read()
    queue = request("/queue")
    assert not queue["queue_running"], "Wait until the current ComfyUI execution finishes before running this test"
    filename = upload(ROOT / "tests/fly001.mp4")
    workflow = {
        "1": {"class_type": "VHS_LoadVideo", "inputs": {
            "video": filename, "force_rate": 0, "custom_width": 0, "custom_height": 0,
            "frame_load_cap": 24, "skip_first_frames": 0, "select_every_nth": 1, "format": "AnimateDiff",
        }},
        "2": {"class_type": "VHSE_VideoSave", "inputs": {
            "images": ["1", 0], "audio": ["1", 2], "source_fps": "16", "target_fps": "24",
            "target_frames": "37", "width": "1920", "height": "1080", "resize_method": "bicubic",
            "resize_mode": "crop", "cq": 19, "preset": "p5", "filename_prefix": "vhse_api_test",
            "save_output": False, "include_audio": True,
        }},
        "3": {"class_type": "VHS_VideoCombine", "inputs": {
            "images": ["1", 0], "frame_rate": 16, "loop_count": 0, "filename_prefix": "vhs_coexist_test",
            "format": "video/h264-mp4", "pingpong": False, "save_output": False,
            "pix_fmt": "yuv420p", "crf": 19, "save_metadata": False, "trim_to_audio": False,
        }},
        # Missing target_frames must be accepted by ComfyUI validation as well as by Python.
        "4": {"class_type": "VHSE_VideoSave", "inputs": {
            "images": ["1", 0], "source_fps": "16", "target_fps": "", "width": "", "height": "",
            "resize_method": "bicubic", "resize_mode": "crop", "cq": 19, "preset": "p5",
            "filename_prefix": "vhse_api_preserve", "save_output": False, "include_audio": True,
        }},
        "5": {"class_type": "VHSE_VideoSave", "inputs": {
            "images": ["1", 0], "audio": ["1", 2], "source_fps": "16", "target_fps": "",
            "target_frames": "13", "width": "320", "height": "180", "resize_method": "bicubic",
            "resize_mode": "crop", "cq": 19, "preset": "p5", "filename_prefix": "vhse_api_cpu",
            "save_output": False, "include_audio": True, "encoder": "libx264", "cpu_preset": "veryfast",
        }},
    }
    submitted = request("/prompt", {"prompt": workflow, "client_id": f"vhse_test_{uuid.uuid4().hex}"})
    prompt_id = submitted["prompt_id"]
    deadline = time.monotonic() + 180
    record = None
    while time.monotonic() < deadline:
        history = request(f"/history/{prompt_id}")
        if prompt_id in history:
            record = history[prompt_id]
            break
        time.sleep(0.5)
    assert record is not None, f"ComfyUI execution timed out: {prompt_id}"
    errors = [message for kind, message in record["status"]["messages"] if kind == "execution_error"]
    brief_errors = [{key: error.get(key) for key in ("node_id", "exception_message", "traceback")}
                    for error in errors]
    assert record["status"]["status_str"] == "success", json.dumps(brief_errors, ensure_ascii=True)
    ffmpeg = os.environ.get("VHS_FORCE_FFMPEG_PATH") or shutil.which("ffmpeg")
    assert ffmpeg
    (ROOT / "tmp").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT / "tmp", prefix="comfy_api_") as directory:
        for node, count, fps, size, audio in (("2", 37, 24, (1920, 1080), True),
                                              ("3", 24, 16, (856, 480), False),
                                              ("4", 24, 16, (856, 480), False),
                                              ("5", 13, 16, (320, 180), True)):
            descriptor = record["outputs"][node]["gifs"][0]
            query = urllib.parse.urlencode({key: descriptor[key] for key in ("filename", "subfolder", "type")})
            path = Path(directory) / f"node_{node}.mp4"
            with urllib.request.urlopen(f"{BASE}/view?{query}", timeout=30) as response, path.open("wb") as sink:
                shutil.copyfileobj(response, sink)
            if node == "3":
                # Original VHS need not satisfy the stricter VHSE SAR contract.
                original = json.loads(run([find_ffprobe(ffmpeg), "-v", "error", "-count_frames",
                                           "-show_streams", "-of", "json", str(path)]))["streams"]
                assert len(original) == 1
                stream = original[0]
                assert stream["codec_name"] == "h264" and int(stream["nb_read_frames"]) == count
                assert Fraction(stream["avg_frame_rate"]) == fps
                assert (stream["width"], stream["height"]) == size
            else:
                checked = verify_output(find_ffprobe(ffmpeg), ffmpeg, path, count, Fraction(fps), size, audio)
                if node == "5":
                    assert "libx264" in checked["streams"][0]["tags"]["encoder"]
            LOGGER.info(f"Live ComfyUI node {node} passed: {descriptor['filename']}")
    LOGGER.info(f"ComfyUI API execution passed; prompt_id={prompt_id}")


if __name__ == "__main__":
    main_test(__file__)
