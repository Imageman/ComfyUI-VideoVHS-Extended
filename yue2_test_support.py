"""Shared immutable fixtures and independent musical checks for Fix9."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
import sys
import traceback
from fractions import Fraction
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "tests" / "raw_abc"
MANIFEST = ROOT / "tests" / "raw_abc_manifest.json"
BASELINE = ROOT / "tests" / "yue2_baseline.json"
LOGGER = logging.getLogger(__name__)


def dependency(name: str) -> Path:
    """Find exactly one reference dependency inside tests, including nested folders."""
    found = sorted((ROOT / "tests").rglob(name))
    if len(found) != 1:
        raise RuntimeError(f"Expected one tests/**/{name}; found {[str(p) for p in found]}")
    return found[0]


def load_checker() -> Any:
    """Import the required independent checker, failing on missing configuration."""
    path = dependency("upstream_abc_tools.py")
    spec = importlib.util.spec_from_file_location("fix9_upstream", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load checker: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def current_manifest() -> dict[str, Any]:
    """Calculate source hashes without changing the corpus or saved manifest."""
    files = sorted(CORPUS.iterdir())
    unexpected = [p for p in files if not p.is_file() or not p.match("YuE2_ABC_*.txt")]
    if unexpected:
        raise RuntimeError(f"Unexpected corpus entries at {CORPUS}: {unexpected}")
    return {"count": len(files), "sha256": {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files
    }}


def verify_manifest() -> list[Path]:
    """Require an exact file-set and byte-level match to the explicit manifest."""
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))
    actual = current_manifest()
    if actual != expected:
        raise AssertionError("Corpus manifest mismatch; explicit review/update required")
    return [CORPUS / name for name in expected["sha256"]]


def notes(score: Any, voice: str = "ins") -> tuple[tuple[Fraction, Fraction, int], ...]:
    """Normalize exact sounding intervals without depending on serialized tie spelling."""
    return tuple(sorted((e.onset, e.duration, e.pitch) for e in score.voices[voice].events if e.kind == "note"))


def expected_notes(score: Any, policy: str) -> tuple[tuple[Fraction, Fraction, int], ...]:
    """Compute overlap policy using a separate interval subtraction implementation."""
    ins, vocal = list(notes(score)), list(notes(score, "vocal"))
    if policy == "mute_vocal_only":
        return tuple(ins)
    primary, secondary = (ins, vocal) if policy == "instrumental_priority" else (vocal, ins)
    result = list(primary)
    for onset, duration, pitch in secondary:
        pieces = [(onset, onset + duration)]
        for start, length, _ in primary:
            stop = start + length
            next_pieces = []
            for left, right in pieces:
                if stop <= left or start >= right:
                    next_pieces.append((left, right))
                else:
                    if left < start:
                        next_pieces.append((left, start))
                    if stop < right:
                        next_pieces.append((stop, right))
            pieces = next_pieces
        result.extend((left, right - left, pitch) for left, right in pieces)
    return tuple(sorted(result))


def fingerprint(score: Any) -> dict[str, Any]:
    """Describe all notes, grids, chords and tempo for mutation ground-truth comparisons."""
    return {
        "tempos": tuple((t.onset, t.beat_unit, t.bpm) for t in score.tempos),
        "sections": tuple(score.sections),
        "notes": {v: notes(score, v) for v in ("vocal", "ins")},
        "grids": {v: tuple((m.onset, m.duration, m.meter, m.key) for m in score.voices[v].measures)
                  for v in ("vocal", "ins")},
        "chords": tuple(sorted((e.onset, e.chord) for v in score.voices.values()
                               for e in v.events if e.kind == "chord")),
    }


def assert_ground_truth(expected: Any, actual: Any) -> None:
    """Reject musical mutations even when both scores remain syntactically valid."""
    assert fingerprint(expected) == fingerprint(actual), "Musical ground-truth mismatch"


def main() -> None:
    """Expose explicit manifest creation; ordinary test runs never refresh fixtures."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["update-manifest", "capture-baseline"])
    parser.add_argument("--error-run", type=Path)
    parser.add_argument("--smart-run", type=Path)
    arguments = parser.parse_args()
    try:
        if arguments.command == "capture-baseline":
            import csv
            verify_manifest()
            manifest = current_manifest()
            baseline = {}
            for mode, path in (("error", arguments.error_run), ("smart", arguments.smart_run)):
                if path is None:
                    raise ValueError("Both explicit audit run directories are required")
                with (path / "summary.csv").open(encoding="utf-8", newline="") as handle:
                    rows = list(csv.DictReader(handle))
                baseline[mode] = {row["file_name"]: row for row in rows}
                assert len(rows) == manifest["count"]
                for row in rows:
                    assert row["sha256"] == manifest["sha256"][row["file_name"]]
                    for key in ("vocal_duration_extensions", "added_output_measures", "notes_in_source_silence"):
                        row[key] = int(row[key])
                    validation_path = path / row["artifact_directory"] / "validation.json"
                    if validation_path.is_file():
                        validation = json.loads(validation_path.read_text(encoding="utf-8"))
                        row["duration"] = validation.get("duration", {})
                        row["musical_preservation"] = validation.get("musical_preservation", {})
                        row["internal_validation"] = validation.get("internal_validator", {})
                    after_path = path / row["artifact_directory"] / "events_after.json"
                    before_path = path / row["artifact_directory"] / "events_before.json"
                    row["bars"] = None
                    row["removed_output_measures"] = None
                    if after_path.is_file():
                        after = json.loads(after_path.read_text(encoding="utf-8"))
                        row["bars"] = len(after["voices"]["Ins"]["measures"])
                        if before_path.is_file():
                            before = json.loads(before_path.read_text(encoding="utf-8"))
                            if before.get("parse_complete"):
                                before_bars = len(before["voices"]["Ins"]["measures"])
                                row["removed_output_measures"] = max(0, before_bars - row["bars"])
            BASELINE.write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            LOGGER.info(f"Explicitly captured {BASELINE}")
            return
        payload = current_manifest()
        if payload["count"] == 0:
            raise RuntimeError(f"No corpus sources at {CORPUS}")
        MANIFEST.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        LOGGER.info(f"Explicitly updated {MANIFEST} with {payload['count']} sources")
    except Exception:
        LOGGER.error(f"Fixture operation failed at {MANIFEST}: {traceback.format_exc()}")
        raise


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()


def silence_metrics(score: Any) -> dict[str, Any]:
    """Measure silence using sounding events and the complete tempo timeline."""
    from yue2_auto_instrumental import _score_end
    intervals = sorted((a, a + b) for v in ("vocal", "ins") for a, b, _ in notes(score, v))
    cursor = Fraction(0)
    gaps = []
    for start, end in intervals:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    last = cursor
    end = _score_end(score)
    if cursor < end:
        gaps.append((cursor, end))

    def seconds(left: Fraction, right: Fraction) -> float:
        """Integrate one interval across tempo changes independently."""
        total = 0.0
        tempos = sorted(score.tempos, key=lambda t: t.onset)
        for index, tempo in enumerate(tempos):
            stop = tempos[index + 1].onset if index + 1 < len(tempos) else end
            length = max(Fraction(0), min(stop, right) - max(tempo.onset, left))
            total += float(length / tempo.beat_unit) * 60 / tempo.bpm
        return total

    return {
        "silent_bars": sum(any(a <= m.onset and m.onset + m.duration <= b for a, b in gaps)
                           for m in score.voices["ins"].measures),
        "longest_silent_run_seconds": max((seconds(a, b) for a, b in gaps), default=0.0),
        "trailing_silence_seconds": seconds(last, end),
        "last_sounding_note_end": str(last),
        "intervals": gaps,
    }
