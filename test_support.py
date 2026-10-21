"""Isolated adapter harness executing the unmodified reference VHS encoding methods."""

from pathlib import Path
from typing import Any
import ast
import datetime
import importlib.util
import itertools
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import types
from string import Template

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

ROOT = Path(__file__).resolve().parent


def load_package() -> Any:
    """Load this extension under a stable package name outside ComfyUI."""
    if "vhse_test" not in sys.modules:
        spec = importlib.util.spec_from_file_location("vhse_test", ROOT / "__init__.py",
                                                      submodule_search_locations=[str(ROOT)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return sys.modules["vhse_test"]


class ArrayTensor:
    """Minimal CPU tensor protocol for testing without installing another torch build."""

    def __init__(self, array: np.ndarray) -> None:
        """Wrap an array without copying it."""
        self.array = array
        self.shape = array.shape

    def __getitem__(self, index: Any) -> "ArrayTensor":
        """Return a view of a batch or frame."""
        return ArrayTensor(self.array[index])

    def __len__(self) -> int:
        """Return the first axis size."""
        return len(self.array)

    def cpu(self) -> "ArrayTensor":
        """Return this CPU tensor."""
        return self

    def detach(self) -> "ArrayTensor":
        """Return this tensor, which has no gradients."""
        return self

    def numpy(self) -> np.ndarray:
        """Expose the underlying array."""
        return self.array

    def squeeze(self, axis: int) -> "ArrayTensor":
        """Remove one singleton axis."""
        return ArrayTensor(self.array.squeeze(axis))

    def transpose(self, first: int, second: int) -> "ArrayTensor":
        """Exchange two axes using tensor semantics."""
        return ArrayTensor(self.array.swapaxes(first, second))

    def contiguous(self) -> "ArrayTensor":
        """Return contiguous storage."""
        return ArrayTensor(np.ascontiguousarray(self.array))


class ProgressBar:
    """No-op progress replacement for isolated upstream tests."""

    def __init__(self, total: int) -> None:
        """Keep the total frame count."""
        self.total = total

    def update(self, count: int) -> None:
        """Accept an upstream progress notification."""


def reference_harness(monkeypatch: Any, output: Path, ffmpeg: str) -> Any:
    """Execute original VHS functions with only ComfyUI infrastructure replaced."""
    package = load_package()
    folder = types.ModuleType("folder_paths")
    folder.folder_names_and_paths = {"VHS_video_formats": ((), {".json"})}
    folder.get_output_directory = lambda: str(output)
    folder.get_temp_directory = lambda: str(output)

    def save_path(prefix: str, base: str) -> tuple[str, str, int, str, str]:
        """Implement the path allocation portion of ComfyUI for the harness."""
        value = Path(base) / prefix
        value.parent.mkdir(parents=True, exist_ok=True)
        return str(value.parent), value.name, 1, str(value.parent.relative_to(base)), prefix

    folder.get_save_image_path = save_path
    def add_folder(category: str, path: str) -> None:
        """Reproduce ComfyUI's list append, including its incompatibility with VHS tuple defaults."""
        paths = folder.folder_names_and_paths[category][0]
        if path not in paths:
            paths.append(path)

    folder.add_model_folder_path = add_folder
    folder.get_full_path = lambda category, name: str(ROOT / "video_formats" / name)
    folder.get_filename_list = lambda category: ["vhse_h264_nvenc_cq-mp4.json"]
    monkeypatch.setitem(sys.modules, "folder_paths", folder)
    source = ROOT / "reference/ComfyUI-VideoHelperSuite/videohelpersuite/nodes.py"
    tree = ast.parse(source.read_text(encoding="utf-8-sig"))
    names = {"flatten_list", "iterate_format", "apply_format_widgets", "tensor_to_int", "tensor_to_shorts",
             "tensor_to_bytes", "ffmpeg_process", "to_pingpong", "VideoCombine"}
    selected = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names]
    module = types.ModuleType("vhse_reference_nodes")
    module.__dict__.update(
        os=os, sys=sys, json=json, subprocess=subprocess, np=np, re=re, datetime=datetime, Image=Image,
        PngInfo=PngInfo, Path=Path, Template=Template, itertools=itertools, folder_paths=folder,
        logger=logging.getLogger("reference_vhs"), ffmpeg_path=ffmpeg, ProgressBar=ProgressBar,
        torch=types.SimpleNamespace(Tensor=type("UnusedTensor", (), {})), ENCODE_ARGS=("utf-8", "replace"),
        base_formats_dir=str(source.parent.parent / "video_formats"),
    )
    utils = source.with_name("utils.py")
    utils_tree = ast.parse(utils.read_text(encoding="utf-8-sig"))
    selected.extend(node for node in utils_tree.body
                    if isinstance(node, ast.FunctionDef) and node.name == "merge_filter_args")
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), module.__dict__)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    nodes = types.ModuleType("nodes")
    nodes.NODE_CLASS_MAPPINGS = {"VHS_VideoCombine": module.VideoCombine}
    monkeypatch.setitem(sys.modules, "nodes", nodes)
    return package


def main_test(path: str) -> None:
    """Allow each test script to run directly as requested by repository conventions."""
    import pytest

    raise SystemExit(pytest.main([path, "-q"]))
