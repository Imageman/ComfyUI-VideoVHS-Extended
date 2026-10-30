"""Run a reproducible, artifact-preserving audit over every local raw YuE2 ABC file."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import logging
import re
import subprocess
import sys
import textwrap
import traceback
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None

from yue2_auto_instrumental import (
    ABCScoreParser,
    ConversionResult,
    Diagnostic,
    ParsedScore,
    PartialParseState,
    ScoreError,
    ScoreEvent,
    _build_grid,
    _full_measure_silence_intervals,
    _score_end,
    _score_seconds,
    _transfer_melody,
    convert_abc,
)


ROOT = Path(__file__).resolve().parent
DEFAULT_CORPUS = ROOT / "tests" / "raw_abc"
DEFAULT_CHECKER = ROOT / "tests" / "upstream_abc_tools.py"
DEFAULT_OUTPUT_ROOT = ROOT / "tmp" / "yue2_audit"
DEFAULT_BASELINE = ROOT / "tmp" / "yue2_auto_instrumental_corpus_report_pre_329.md"
DEFAULT_REPAIR_MODE = "extended"
DEFAULT_MELODY_POLICY = "instrumental_priority"
DEFAULT_LYRICS_MODE = "sections_only"
DEFAULT_ON_UNRECOVERABLE = "error"
DEFAULT_MAX_REPAIR_UNITS = 4
DEFAULT_PADDING_SECONDS = 0.0
MAX_DIFF_ITEMS = 20
LOGGER = logging.getLogger(__name__)
EXACT_REPAIR_PREFIXES = (
    "Remove the quote delimiters at token level",
    "Insert a missing opening quote around the chord symbol",
    "Drop the orphan quote as a token-level repair",
    "Drop the empty chord label",
    "Keep the valid metadata prefix and preserve the trailing text as a comment",
    "Split the valid metadata prefix and parse the remaining music tokens",
)
SUMMARY_FIELDS = (
    "file_name",
    "status",
    "category",
    "conversion_status",
    "source_conversion_status",
    "repair_count",
    "upstream_status",
    "vocal_absence_confirmed",
    "verbatim_span_count",
    "source_events_removed",
    "source_events_changed",
    "events_preserved",
    "events_modified",
    "converted_measures",
    "raw_preserved_measures",
    "resynchronized_segments",
    "source_music_coverage",
    "output_contains_full_source_music",
    "duration_known",
    "vocal_duration_extensions",
    "added_output_measures",
    "notes_in_source_silence",
    "exact_transfer_timeline",
    "ins_preservation",
    "vocal_notes",
    "vocal_transferred",
    "vocal_blocked",
    "ins_changed",
    "duration_drift_seconds",
    "musical_uncertainty",
    "sha256",
    "artifact_directory",
    "error",
)


@dataclass
class AuditRecord:
    """Hold the compact per-file status written to the audit summaries."""

    file_name: str
    status: str = "failed"
    category: str = "FAILED"
    conversion_status: str = "FAILED"
    source_conversion_status: str = ""
    repair_count: int = 0
    upstream_status: str = "not_run"
    vocal_absence_confirmed: str = "unknown"
    verbatim_span_count: int = 0
    source_events_removed: int | None = None
    source_events_changed: int | None = None
    events_preserved: int | None = None
    events_modified: int | None = None
    converted_measures: int = 0
    raw_preserved_measures: int = 0
    resynchronized_segments: int = 0
    source_music_coverage: float = 0.0
    output_contains_full_source_music: bool = False
    duration_known: bool = False
    vocal_duration_extensions: int = 0
    added_output_measures: int = 0
    notes_in_source_silence: int = 0
    exact_transfer_timeline: str = "unknown"
    ins_preservation: str = "unknown"
    vocal_notes: int = 0
    vocal_transferred: int = 0
    vocal_blocked: int = 0
    ins_changed: int = 0
    duration_drift_seconds: str = "unknown"
    musical_uncertainty: bool = True
    sha256: str = ""
    artifact_directory: str = ""
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)


def _write_text(path: Path, content: str) -> None:
    """Write UTF-8 text and log the full path if the file operation fails."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
    except Exception as exc:
        LOGGER.error(f"Unable to write file {path}: {exc}\n{traceback.format_exc()}")
        raise


def _write_json(path: Path, payload: object) -> None:
    """Write stable, readable JSON and log the full path on failure."""
    _write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def _load_upstream_checker(path: Path) -> ModuleType:
    """Load the checked-in YuE ABC checker snapshot as an isolated module."""
    spec = importlib.util.spec_from_file_location("yue2_audit_upstream_checker", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load the YuE checker at {path}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _score_voices(score: ParsedScore | None, parser: ABCScoreParser) -> dict[str, Any]:
    """Return parsed voices or the parser's last trusted partial state."""
    if score is not None:
        return score.voices
    return parser.partial_state.voices


def _event_segments(
    voice_name: str,
    voice: Any,
    partial_state: PartialParseState,
) -> list[ScoreEvent]:
    """Collect source measure segments and the active partial measure without duplicates."""
    events = [event for measure in voice.measures for event in measure.events]
    if partial_state.active_voice == voice_name and partial_state.measure_events:
        events.extend(partial_state.measure_events)
    if not events:
        events.extend(voice.events)
    unique: dict[tuple[int, int, int, str], ScoreEvent] = {}
    for event in events:
        unique[(event.source_order, event.line_number, event.measure_index, event.kind)] = event
    return sorted(unique.values(), key=lambda event: (event.onset, event.source_order))


def _event_payload(
    state_name: str,
    score: ParsedScore | None,
    parser: ABCScoreParser,
    source_sha256: str,
    complete: bool,
    parse_error: str,
) -> dict[str, Any]:
    """Serialize exact rational events, measure metadata, chords, and tie segments."""
    voices = _score_voices(score, parser)
    state = parser.partial_state
    voice_payloads: dict[str, Any] = {}
    all_chords: list[dict[str, Any]] = []
    for voice_name in ("vocal", "ins"):
        voice = voices.get(voice_name)
        if voice is None:
            continue
        segments = _event_segments(voice_name, voice, state)
        notes = [event for event in segments if event.kind == "note"]
        notes.sort(key=lambda event: (event.onset, event.source_order))
        tied_previous: set[int] = set()
        for previous, current in zip(notes, notes[1:]):
            if (
                previous.tied_after
                and previous.pitch == current.pitch
                and previous.onset + previous.duration == current.onset
            ):
                tied_previous.add(current.source_order)
        serialized_events: list[dict[str, Any]] = []
        for event in segments:
            item: dict[str, Any] = {
                "kind": event.kind,
                "voice": "Vocal" if voice_name == "vocal" else "Ins",
                "onset": str(event.onset),
                "duration": str(event.duration),
                "source_bar": event.measure_index + 1,
                "source_line": event.line_number,
            }
            if event.kind == "note":
                item.update(
                    {
                        "pitch_midi": event.pitch,
                        "tied_from_previous": event.source_order in tied_previous,
                        "tied_to_next": event.tied_after,
                    }
                )
            if event.kind == "chord":
                item["text"] = event.chord
                item["source_order"] = event.source_order
                all_chords.append(item.copy())
            serialized_events.append(item)
        measure_payloads = [
            {
                "bar": index + 1,
                "onset": str(measure.onset),
                "duration": str(measure.duration),
                "end": str(measure.onset + measure.duration),
                "meter": f"{measure.meter[0]}/{measure.meter[1]}",
                "note_length": str(measure.note_length),
                "key": measure.key,
                "source_line": measure.line_number,
            }
            for index, measure in enumerate(voice.measures)
        ]
        voice_payloads["Vocal" if voice_name == "vocal" else "Ins"] = {
            "measures": measure_payloads,
            "events": serialized_events,
        }
    tempo_changes = score.tempos if score is not None else parser.tempo_changes
    return {
        "state": state_name,
        "source_sha256": source_sha256,
        "parse_complete": complete,
        "parse_error": parse_error,
        "interpretation": (
            f"{parser.repair_mode}_complete_parse" if complete else "trusted_partial_only"
        ),
        "voices": voice_payloads,
        "chords": sorted(
            all_chords,
            key=lambda item: (Fraction(item["onset"]), item["source_order"]),
        ),
        "tempo_changes": [
            {
                "onset": str(change.onset),
                "beat_unit": str(change.beat_unit),
                "bpm": change.bpm,
                "voice": change.voice,
            }
            for change in tempo_changes
        ],
        "diagnostics": [item.to_dict() for item in parser.partial_state.diagnostics],
        "active_voice": state.active_voice,
        "active_line": state.line_number,
    }


def _note_signature(score: ParsedScore, voice_name: str) -> tuple[tuple[Any, ...], ...]:
    """Return merged note events for exact semantic comparisons."""
    events = [event for event in score.voices[voice_name].events if event.kind == "note"]
    return tuple(
        (event.onset, event.duration, event.pitch, event.tied_after)
        for event in sorted(events, key=lambda item: (item.onset, item.source_order))
    )


def _chord_signature(score: ParsedScore) -> tuple[tuple[Fraction, str], ...]:
    """Return chord text and onset in source order."""
    events = [
        event
        for voice in score.voices.values()
        for event in voice.events
        if event.kind == "chord"
    ]
    return tuple(
        (event.onset, event.chord)
        for event in sorted(events, key=lambda item: (item.onset, item.source_order))
    )


def _score_signature(score: ParsedScore) -> dict[str, Any]:
    """Return exact notes, chords, meter/key grids, and tempos for comparisons."""
    return {
        "vocal": _note_signature(score, "vocal"),
        "ins": _note_signature(score, "ins"),
        "chords": _chord_signature(score),
        "grid": {
            name: tuple(
                (item.onset, item.duration, item.meter, item.key)
                for item in score.voices[name].measures
            )
            for name in ("vocal", "ins")
        },
        "tempos": tuple(
            (item.onset, item.beat_unit, item.bpm) for item in score.tempos
        ),
        "end": _score_end(score),
    }


def _delta_summary(before: ParsedScore, after: ParsedScore) -> dict[str, Any]:
    """Summarize per-voice additions, removals, and changes between exact event states."""
    changes: dict[str, Any] = {}
    for voice_name in ("vocal", "ins"):
        before_notes = Counter(_note_signature(before, voice_name))
        after_notes = Counter(_note_signature(after, voice_name))
        removed_counts = before_notes - after_notes
        added_counts = after_notes - before_notes
        removed = list(removed_counts.elements())
        added = list(added_counts.elements())
        indexes: dict[str, dict[tuple[Any, ...], list[tuple[Any, ...]]]] = {
            "onset_pitch": defaultdict(list),
            "pitch_duration": defaultdict(list),
            "onset_duration": defaultdict(list),
            "onset": defaultdict(list),
        }
        for event in added:
            indexes["onset_pitch"][(event[0], event[2])].append(event)
            indexes["pitch_duration"][(event[2], event[1])].append(event)
            indexes["onset_duration"][(event[0], event[1])].append(event)
            indexes["onset"][(event[0],)].append(event)
        changed: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for event in removed:
            options = (
                ("onset_pitch", (event[0], event[2])),
                ("pitch_duration", (event[2], event[1])),
                ("onset_duration", (event[0], event[1])),
                ("onset", (event[0],)),
            )
            replacement = next(
                (
                    candidate
                    for index_name, index_key in options
                    for candidate in indexes[index_name][index_key]
                    if added_counts[candidate] > 0
                ),
                None,
            )
            if replacement is None:
                continue
            added_counts[replacement] -= 1
            removed_counts[event] -= 1
            fields = [
                name
                for index, name in ((0, "onset"), (1, "duration"), (2, "pitch"), (3, "tie"))
                if event[index] != replacement[index]
            ]
            label = "+".join(fields) or "other"
            changed[label].append({"before": event, "after": replacement, "fields": fields})
        remaining_added = list(added_counts.elements())
        remaining_removed = list(removed_counts.elements())
        changes[voice_name] = {
            "removed": remaining_removed[:MAX_DIFF_ITEMS],
            "removed_count": len(remaining_removed),
            "added": remaining_added[:MAX_DIFF_ITEMS],
            "added_count": len(remaining_added),
            "changed": {key: value[:MAX_DIFF_ITEMS] for key, value in changed.items()},
            "changed_counts": {key: len(value) for key, value in changed.items()},
        }
    before_chords = Counter(_chord_signature(before))
    after_chords = Counter(_chord_signature(after))
    chords_removed = list((before_chords - after_chords).elements())
    chords_added = list((after_chords - before_chords).elements())
    grid_changes: list[dict[str, Any]] = []
    for voice_name in ("vocal", "ins"):
        before_grid = before.voices[voice_name].measures
        after_grid = after.voices[voice_name].measures
        for index in range(max(len(before_grid), len(after_grid))):
            left = before_grid[index] if index < len(before_grid) else None
            right = after_grid[index] if index < len(after_grid) else None
            left_data = (
                (str(left.onset), str(left.duration), left.meter, left.key)
                if left is not None
                else None
            )
            right_data = (
                (str(right.onset), str(right.duration), right.meter, right.key)
                if right is not None
                else None
            )
            if left_data != right_data:
                grid_changes.append(
                    {
                        "voice": voice_name,
                        "bar": index + 1,
                        "before": left_data,
                        "after": right_data,
                    }
                )
    before_tempos = tuple((item.onset, item.beat_unit, item.bpm) for item in before.tempos)
    after_tempos = tuple((item.onset, item.beat_unit, item.bpm) for item in after.tempos)
    return {
        "voices": changes,
        "chords_removed": chords_removed[:MAX_DIFF_ITEMS],
        "chords_removed_count": sum((before_chords - after_chords).values()),
        "chords_added": chords_added[:MAX_DIFF_ITEMS],
        "chords_added_count": sum((after_chords - before_chords).values()),
        "grid_changes": grid_changes[:MAX_DIFF_ITEMS],
        "grid_change_count": len(grid_changes),
        "tempo_before": before_tempos,
        "tempo_after": after_tempos,
        "tempo_changed": before_tempos != after_tempos,
        "duration_before_whole_notes": str(_score_end(before)),
        "duration_after_whole_notes": str(_score_end(after)),
        "duration_drift_seconds": _score_seconds(after) - _score_seconds(before),
    }


def _line_excerpt(text: str, line_number: int | None) -> str:
    """Return one source or repaired line when a diagnostic identifies it."""
    if line_number is None or line_number < 1:
        return ""
    lines = text.splitlines()
    if line_number > len(lines):
        return ""
    return lines[line_number - 1]


def _repair_line_number(diagnostic: Diagnostic, source_score: ParsedScore | None) -> int | None:
    """Resolve a repair's source line from its location or measure metadata."""
    match = re.search(r"line (\d+)", diagnostic.location)
    if match:
        return int(match.group(1))
    if source_score is not None and diagnostic.voice in source_score.voices:
        measures = source_score.voices[diagnostic.voice].measures
        if diagnostic.measure and diagnostic.measure <= len(measures):
            return measures[diagnostic.measure - 1].line_number
    return None


def _repair_category(diagnostic: Diagnostic) -> str:
    """Classify a repair conservatively from its documented strategy."""
    if diagnostic.severity == "error":
        return "FAILED"
    if diagnostic.code == "unsupported_chord_quality":
        return "EXACT"
    if diagnostic.repair.startswith(EXACT_REPAIR_PREFIXES):
        return "EXACT"
    return "HEURISTIC"


def _delta_counts(delta: dict[str, Any] | None) -> dict[str, Any] | None:
    """Reduce a semantic diff to JSON-safe counts for a repair journal entry."""
    if delta is None:
        return None
    return {
        "voices": {
            voice_name: {
                "removed": voice["removed_count"],
                "added": voice["added_count"],
                "changed": voice["changed_counts"],
            }
            for voice_name, voice in delta["voices"].items()
        },
        "chords_removed": delta["chords_removed_count"],
        "chords_added": delta["chords_added_count"],
        "grid_changes": delta["grid_change_count"],
        "tempo_changed": delta["tempo_changed"],
        "duration_drift_seconds": delta["duration_drift_seconds"],
    }


def _repairs_payload(
    diagnostics: list[Diagnostic],
    candidates: list[dict[str, object]],
    source_text: str,
    repaired_text: str,
    source_score: ParsedScore | None,
    repaired_score: ParsedScore | None,
) -> dict[str, Any]:
    """Build the repair journal with original/repaired excerpts and candidate rationale."""
    repairs: list[dict[str, Any]] = []
    stage_delta = (
        _delta_counts(_delta_summary(source_score, repaired_score))
        if source_score is not None and repaired_score is not None
        else None
    )
    for diagnostic in diagnostics:
        line_number = _repair_line_number(diagnostic, source_score)
        repaired_line = line_number
        if repaired_score is not None and diagnostic.voice in repaired_score.voices and diagnostic.measure:
            measures = repaired_score.voices[diagnostic.voice].measures
            if diagnostic.measure <= len(measures):
                repaired_line = measures[diagnostic.measure - 1].line_number
        related_candidates = [
            candidate
            for candidate in candidates
            if candidate.get("voice") == diagnostic.voice
            and (
                candidate.get("line_number") == line_number
                or candidate.get("measure") == diagnostic.measure
            )
        ]
        repairs.append(
            {
                **diagnostic.to_dict(),
                "problem_type": diagnostic.code or diagnostic.problem.split(" ", maxsplit=1)[0],
                "source_fragment": _line_excerpt(source_text, line_number),
                "repaired_fragment": _line_excerpt(repaired_text, repaired_line),
                "strategy": diagnostic.repair,
                "alternatives": related_candidates,
                "change_category": _repair_category(diagnostic),
                "musical_delta": {
                    "scope": "whole raw-to-repaired stage; see diff.md for note-level changes",
                    "summary": stage_delta,
                },
                "confidence": "not_calibrated",
                "ambiguity": "unknown unless strict semantic comparison proves equivalence",
            }
        )
    return {"repairs": repairs, "candidate_search": candidates}


def _run_checker_cli(checker_path: Path, arguments: list[str]) -> tuple[int, Any, str, str]:
    """Execute one checker command and return its decoded result and raw streams."""
    command = [sys.executable, str(checker_path), *arguments]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            check=False,
            encoding="utf-8",
            text=True,
        )
    except Exception as exc:
        LOGGER.error(f"Unable to run checker command {command}: {exc}\n{traceback.format_exc()}")
        raise
    payload: Any = {}
    try:
        if completed.stdout.strip().startswith(("{", "[")):
            payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        payload = {}
    return completed.returncode, payload, completed.stdout, completed.stderr


def _check_upstream_output(
    checker: ModuleType,
    checker_path: Path,
    output_text: str,
    source_path: Path,
    scratch_path: Path,
    source_accepted: bool,
) -> dict[str, Any]:
    """Check output with both the official Python parser and its inspect command."""
    result: dict[str, Any] = {
        "python_parser": "not_run",
        "python_parser_error": "",
        "inspect_status": "not_run",
        "inspect_return_code": None,
        "inspect_payload": {},
        "inspect_stdout": "",
        "inspect_stderr": "",
        "ins_compare_status": "not_run",
        "ins_compare_payload": {},
        "ins_compare_stdout": "",
        "ins_compare_stderr": "",
        "compatibility_classification": "unknown",
    }
    try:
        checker.parse_abc(output_text)
        result["python_parser"] = "accepted"
    except checker.AbcError as exc:
        result["python_parser"] = "rejected"
        result["python_parser_error"] = str(exc)
        result["compatibility_classification"] = (
            "unsupported_dialect_or_chord" if "unsupported" in str(exc).lower()
            or "chord" in str(exc).lower()
            else "upstream_syntax_or_structure_error"
        )
        return result

    try:
        _write_text(scratch_path, output_text)
        inspect_code, inspect_payload, inspect_stdout, inspect_stderr = _run_checker_cli(
            checker_path,
            ["inspect", str(scratch_path)],
        )
        result.update(
            {
                "inspect_status": "accepted" if inspect_code == 0 else "rejected",
                "inspect_return_code": inspect_code,
                "inspect_payload": inspect_payload,
                "inspect_stdout": inspect_stdout,
                "inspect_stderr": inspect_stderr,
            }
        )
    except Exception as exc:
        result.update(
            {
                "inspect_status": "error",
                "inspect_stderr": str(exc),
                "compatibility_classification": "checker_execution_error",
            }
        )
        return result

    if result["inspect_status"] == "accepted":
        result["compatibility_classification"] = "accepted_by_official_checker_snapshot"
        if source_accepted:
            compare_code, compare_payload, compare_stdout, compare_stderr = _run_checker_cli(
                checker_path,
                ["compare", str(source_path), str(scratch_path), "--voices", "Ins"],
            )
            result.update(
                {
                    "ins_compare_status": "exact" if compare_payload.get("match") is True else "different",
                    "ins_compare_return_code": compare_code,
                    "ins_compare_payload": compare_payload,
                    "ins_compare_stdout": compare_stdout,
                    "ins_compare_stderr": compare_stderr,
                }
            )
    else:
        result["compatibility_classification"] = "official_inspect_rejection_unclassified"
    return result


def _parse_score(text: str, mode: str) -> tuple[ParsedScore | None, ABCScoreParser, str]:
    """Attempt one parser mode and retain its partial state on failure."""
    parser = ABCScoreParser(mode, DEFAULT_MAX_REPAIR_UNITS)
    try:
        return parser.parse(text), parser, ""
    except ScoreError as exc:
        return None, parser, str(exc)


def _score_excerpt(score: ParsedScore | None, voice_name: str, measure: int | None) -> str:
    """Find a canonical source line for one voice measure in a parsed score."""
    if score is None or measure is None or voice_name not in score.voices:
        return ""
    measures = score.voices[voice_name].measures
    if measure < 1 or measure > len(measures):
        return ""
    return _line_excerpt("\n".join(score.source_lines), measures[measure - 1].line_number)


def _append_voice_delta(lines: list[str], label: str, delta: dict[str, Any]) -> None:
    """Append note additions, removals, and matched pitch/time/tie changes to a diff."""
    lines.append(f"- {label}: removed {delta['removed_count']} notes; added {delta['added_count']} notes.")
    for change_type, count in sorted(delta["changed_counts"].items()):
        lines.append(f"- {label}: {change_type} changed on {count} matched notes.")
        for change in delta["changed"].get(change_type, []):
            before = change["before"]
            after = change["after"]
            lines.append(
                f"  - {before[0]}/{before[1]}/{before[2]}/{before[3]} "
                f"-> {after[0]}/{after[1]}/{after[2]}/{after[3]}."
            )
    for event in delta["removed"]:
        lines.append(
            f"  - removed onset={event[0]}, duration={event[1]}, pitch={event[2]}, tie={event[3]}."
        )
    for event in delta["added"]:
        lines.append(
            f"  - added onset={event[0]}, duration={event[1]}, pitch={event[2]}, tie={event[3]}."
        )


def _build_diff(
    source_score: ParsedScore | None,
    source_complete: bool,
    repaired_score: ParsedScore,
    output_score: ParsedScore,
    transfer: dict[str, Any],
) -> str:
    """Create a concise semantic diff for repair and Vocal-to-Ins stages."""
    lines = ["# Musical diff", ""]
    if source_score is None or not source_complete:
        lines.extend(
            [
                "Raw parsing is incomplete. Raw events are limited to strict-parser evidence; no exact raw-to-repaired",
                "preservation claim is made.",
                "",
            ]
        )
    else:
        repair_delta = _delta_summary(source_score, repaired_score)
        lines.extend(["## Raw to repaired", ""])
        for voice_name, label in (("vocal", "Vocal"), ("ins", "Ins")):
            _append_voice_delta(lines, label, repair_delta["voices"][voice_name])
        lines.append(
            f"- Chords: removed {repair_delta['chords_removed_count']}; "
            f"added {repair_delta['chords_added_count']}."
        )
        lines.append(
            f"- Duration: {repair_delta['duration_before_whole_notes']} to "
            f"{repair_delta['duration_after_whole_notes']} whole notes; "
            f"{repair_delta['duration_drift_seconds']:+.9f} seconds."
        )
        lines.append(f"- Meter/key/bar grid changes: {repair_delta['grid_change_count']}.")
        if repair_delta["tempo_changed"]:
            lines.append(
                f"- Tempo: {repair_delta['tempo_before']} to {repair_delta['tempo_after']}."
            )
        for onset, chord in repair_delta["chords_removed"]:
            lines.append(f"- Removed chord at {onset}: {chord}.")
        for onset, chord in repair_delta["chords_added"]:
            lines.append(f"- Added chord at {onset}: {chord}.")
        lines.append("")

    transfer_delta = _delta_summary(repaired_score, output_score)
    lines.extend(["## Repaired to converted", ""])
    for voice_name, label in (("vocal", "Vocal"), ("ins", "Ins")):
        _append_voice_delta(lines, label, transfer_delta["voices"][voice_name])
    lines.extend(
        [
            f"- Vocal transfer: fully {transfer.get('fully_transferred', 0)}, "
            f"partially {transfer.get('partially_transferred', 0)}, "
            f"blocked {transfer.get('not_transferred', 0)}.",
            f"- Original Ins notes changed by repair or policy: "
            f"{transfer.get('ins_notes_changed', 0) + transfer.get('ins_notes_repaired', 0)}.",
            f"- Chords: removed {transfer_delta['chords_removed_count']}; "
            f"added {transfer_delta['chords_added_count']}.",
            f"- Meter/key/bar grid changes: {transfer_delta['grid_change_count']}.",
            f"- Tempo unchanged: {not transfer_delta['tempo_changed']}.",
            f"- Duration drift during conversion: {transfer_delta['duration_drift_seconds']:+.9f} seconds.",
            "",
        ]
    )
    return "\n".join(lines)


def _failure_artifacts(
    directory: Path,
    source_bytes: bytes,
    diagnostics: dict[str, Any],
    candidates: list[dict[str, object]],
    candidate_text: str,
    failure_reason: str,
) -> None:
    """Persist exact source bytes and refusal evidence without labeling a candidate as converted."""
    directory.mkdir(parents=True, exist_ok=False)
    try:
        (directory / "source.abc").write_bytes(source_bytes)
    except Exception as exc:
        path = directory / "source.abc"
        LOGGER.error(f"Unable to write source file {path}: {exc}\n{traceback.format_exc()}")
        raise
    _write_json(directory / "diagnostics.json", diagnostics)
    _write_json(
        directory / "candidates.json",
        {
            "status": "unverified_candidates_only",
            "candidates": candidates,
            "candidate_search_reason": failure_reason,
            "best_candidate": "candidate_unverified.abc" if candidate_text else None,
            "converted_abc_written": False,
        },
    )
    if candidate_text:
        _write_text(directory / "candidate_unverified.abc", candidate_text)


def _partial_raw_artifacts(
    directory: Path,
    source_bytes: bytes,
    source_text: str,
    partial_text: str,
    repaired_text: str,
    spans: list[dict[str, object]],
    validation: dict[str, Any],
    events_before: dict[str, Any],
    candidate_log: list[dict[str, object]],
    conversion_status: str,
    events_after: dict[str, Any] | None = None,
    diagnostic_segments: list[dict[str, object]] | None = None,
) -> None:
    """Save raw output, validated diagnostic conversions, source spans, and audit validation."""
    directory.mkdir(parents=True, exist_ok=False)
    source_path = directory / "source.abc"
    try:
        source_path.write_bytes(source_bytes)
    except Exception as exc:
        LOGGER.error(f"Unable to write source file {source_path}: {exc}\n{traceback.format_exc()}")
        raise
    partial_name = "partial_smart.abc" if conversion_status == "SMART_PARTIAL" else "partial_raw.abc"
    _write_text(directory / partial_name, partial_text)
    if conversion_status == "SMART_PARTIAL":
        _write_text(directory / "repaired.abc", repaired_text)
        _write_text(directory / "converted.abc", partial_text)
    elif repaired_text:
        _write_text(directory / "verified_prefix.abc", repaired_text)
    for segment in diagnostic_segments or []:
        segment_id = int(segment["segment"])
        _write_text(directory / f"resynchronized_segment_{segment_id:03d}.abc", str(segment["abc"]))
    _write_json(
        directory / "repairs.json",
        {
            "conversion_status": conversion_status,
            "candidates": candidate_log,
            "repairs": [],
            "verbatim_spans": spans,
            "diagnostic_segments": [
                {key: value for key, value in segment.items() if key != "abc"}
                for segment in diagnostic_segments or []
            ],
        },
    )
    _write_json(directory / "validation.json", validation)
    _write_json(directory / "events_before.json", events_before)
    if events_after is not None:
        _write_json(directory / "events_after.json", events_after)
    _write_text(
        directory / "diff.md",
        "\n".join(
            [
                f"# {conversion_status}",
                "",
                (
                    "Only a complete aligned prefix was converted; the remaining source spans are preserved."
                    if conversion_status == "SMART_PARTIAL"
                    else (
                        "The complete source is the main output; strict prefix and resynchronized conversions "
                        "are diagnostics only."
                    )
                ),
                "Raw syntax and Vocal absence are not fully validated.",
                "",
                f"- Source and partial text identical: {partial_text == source_text}.",
                f"- Verbatim spans: {len(spans)}.",
                "",
            ]
        ),
    )


def _audit_one_file(
    source_path: Path,
    run_directory: Path,
    checker: ModuleType,
    checker_path: Path,
    baseline: dict[str, bool],
    on_unrecoverable: str = DEFAULT_ON_UNRECOVERABLE,
) -> AuditRecord:
    """Audit one file and save success artifacts or complete refusal diagnostics."""
    record = AuditRecord(source_path.name)
    source_bytes = b""
    source_text = ""
    result: ConversionResult | None = None
    source_score: ParsedScore | None = None
    repaired_score: ParsedScore | None = None
    output_score: ParsedScore | None = None
    strict_parser: ABCScoreParser | None = None
    extended_parser: ABCScoreParser | None = None
    strict_error = ""
    extended_error = ""
    conversion_error = ""
    upstream_source: dict[str, Any] = {"status": "not_run", "error": ""}
    upstream_output: dict[str, Any] = {"status": "not_run"}
    internal_status = "not_run"
    try:
        source_bytes = source_path.read_bytes()
        record.sha256 = hashlib.sha256(source_bytes).hexdigest()
        source_text = source_bytes.decode("utf-8-sig")
        source_score, strict_parser, strict_error = _parse_score(source_text, "strict")
        extended_score, extended_parser, extended_error = _parse_score(source_text, "extended")
        before_payload = _event_payload(
            "raw",
            source_score,
            strict_parser,
            record.sha256,
            source_score is not None,
            strict_error,
        )
        try:
            checker.parse_abc(source_text)
            upstream_source = {"status": "accepted", "error": ""}
        except checker.AbcError as exc:
            upstream_source = {"status": "rejected", "error": str(exc)}

        try:
            result = convert_abc(
                source_text,
                repair_mode=DEFAULT_REPAIR_MODE,
                melody_policy=DEFAULT_MELODY_POLICY,
                lyrics_mode=DEFAULT_LYRICS_MODE,
                padding_seconds=DEFAULT_PADDING_SECONDS,
                max_repair_units=DEFAULT_MAX_REPAIR_UNITS,
                on_unrecoverable=on_unrecoverable,
            )
        except ScoreError as exc:
            conversion_error = str(exc)
        if result is not None:
            record.repair_count = result.repair_count
            record.conversion_status = result.conversion_status
            if result.conversion_status in {"PARTIAL_RAW", "SMART_PARTIAL"}:
                warning_details = json.loads(result.warnings) if result.warnings else {}
                conversion = warning_details.get("conversion", {})
                spans = result.verbatim_spans
                smart_partial = result.conversion_status == "SMART_PARTIAL"
                smart_source_fallback = conversion.get("source_conversion_status") == "SMART_PARTIAL"
                has_diagnostic_conversion = smart_partial or smart_source_fallback
                partial_status = "smart_partial" if smart_partial else "partial_raw"
                record.status = partial_status
                record.category = result.conversion_status
                record.source_conversion_status = str(conversion.get("source_conversion_status", ""))
                record.vocal_absence_confirmed = "no"
                record.verbatim_span_count = len(spans)
                record.converted_measures = int(conversion.get("converted_measures", 0))
                record.raw_preserved_measures = int(conversion.get("raw_preserved_measures", 0))
                record.resynchronized_segments = int(conversion.get("resynchronized_segments", 0))
                record.source_music_coverage = float(conversion.get("source_music_coverage", 0.0))
                record.output_contains_full_source_music = bool(
                    conversion.get("output_contains_full_source_music", False)
                )
                record.duration_known = bool(conversion.get("duration_known", False))
                source_changes = conversion.get("source_musical_event_changes", {})
                record.source_events_removed = source_changes.get("removed", 0) if smart_partial else 0
                record.source_events_changed = source_changes.get("changed", 0) if smart_partial else 0
                record.events_preserved = source_changes.get("events_preserved", 0) if smart_partial else None
                record.events_modified = source_changes.get("events_modified", 0) if smart_partial else None
                transfer = warning_details.get("transfer", {})
                record.vocal_notes = transfer.get("vocal_notes", 0)
                record.vocal_transferred = (
                    transfer.get("fully_transferred", 0) + transfer.get("partially_transferred", 0)
                )
                record.vocal_blocked = transfer.get("not_transferred", 0)
                record.ins_changed = (
                    transfer.get("ins_notes_changed", 0) + transfer.get("ins_notes_repaired", 0)
                )
                record.ins_preservation = (
                    "verified" if transfer.get("ins_preservation_verified") == 1 else "unknown"
                )
                partial_reason = next(
                    (
                        str(item.get("reason", ""))
                        for item in result.candidate_log
                        if item.get("type") == "smart_partial" and item.get("status") == "accepted"
                    ),
                    "",
                )
                record.error = (
                    str(spans[0].get("reason", "Unvalidated raw ABC was preserved."))
                    if spans and not smart_partial
                    else str(conversion.get("downstream_risk", "Only part of the source was converted."))
                )
                if smart_partial and partial_reason:
                    record.error = partial_reason
                scratch_directory = run_directory / "checker_candidates"
                scratch_directory.mkdir(parents=True, exist_ok=True)
                scratch_path = scratch_directory / f"{source_path.stem}.abc"
                upstream_output = _check_upstream_output(
                    checker,
                    checker_path,
                    result.abc,
                    source_path,
                    scratch_path,
                    upstream_source.get("status") == "accepted",
                )
                upstream_partial_accepted = (
                    upstream_output.get("python_parser") == "accepted"
                    and upstream_output.get("inspect_status") == "accepted"
                )
                record.upstream_status = (
                    "partial_only_accepted"
                    if smart_partial and upstream_partial_accepted
                    else "partial_only_rejected"
                    if smart_partial
                    else "accepted"
                    if upstream_partial_accepted
                    else str(upstream_output.get("python_parser", "error"))
                    if upstream_output.get("python_parser") != "accepted"
                    else str(upstream_output.get("inspect_status", "error"))
                )
                if scratch_path.exists():
                    try:
                        scratch_path.unlink()
                    except Exception as exc:
                        LOGGER.error(
                            f"Unable to remove temporary checker file {scratch_path}: {exc}\n"
                            f"{traceback.format_exc()}"
                        )
                events_after: dict[str, Any] | None = None
                partial_score: ParsedScore | None = None
                diagnostic_prefix_text = result.abc if smart_partial else result.repaired_abc
                if has_diagnostic_conversion and diagnostic_prefix_text:
                    partial_score, partial_parser, partial_error = _parse_score(
                        diagnostic_prefix_text,
                        "strict",
                    )
                    if partial_score is not None:
                        events_after = _event_payload(
                            "verified_diagnostic_prefix",
                            partial_score,
                            partial_parser,
                            record.sha256,
                            True,
                            "",
                        )
                    else:
                        validation_error = (
                            f"Smart diagnostic prefix did not pass strict validation: {partial_error}"
                        )
                        LOGGER.error(f"{source_path.name}: {validation_error}")
                        record.error = f"{record.error} {validation_error}".strip()
                segment_validation: list[dict[str, Any]] = []
                for segment in result.diagnostic_segments:
                    segment_score, _, segment_error = _parse_score(str(segment["abc"]), "strict")
                    segment_validation.append(
                        {
                            "segment": int(segment["segment"]),
                            "status": "accepted" if segment_score is not None else "rejected",
                            "error": segment_error,
                        }
                    )
                    if segment_score is None:
                        record.error = (
                            f"{record.error} Resynchronized diagnostic segment "
                            f"{segment['segment']} failed strict validation: {segment_error}."
                        ).strip()
                span_error = next(
                    (
                        f"Invalid verbatim span in {source_path.name}."
                        for span in spans
                        if int(span.get("start_offset", -1)) < 0
                        or int(span.get("end_offset", -1)) > len(source_text)
                        or int(span.get("end_offset", -1)) < int(span.get("start_offset", -1))
                        or source_text[int(span["start_offset"]):int(span["end_offset"])] != span.get("text")
                    ),
                    "",
                )
                if span_error:
                    raise ScoreError(span_error)
                ordered_spans = sorted(spans, key=lambda span: int(span["start_offset"]))
                if any(
                    int(previous["end_offset"]) > int(current["start_offset"])
                    for previous, current in zip(ordered_spans, ordered_spans[1:])
                ):
                    raise ScoreError(f"Overlapping verbatim source spans in {source_path.name}.")
                validation = {
                    "file": source_path.name,
                    "source_sha256": record.sha256,
                    "conversion_status": result.conversion_status,
                    "source_conversion_status": record.source_conversion_status,
                    "internal_validator": conversion.get("internal_validator", "not_passed"),
                    "partial_fragment_strict_validation": (
                        "accepted"
                        if partial_score is not None
                        else "not_run"
                        if not has_diagnostic_conversion
                        else "rejected"
                    ),
                    "resynchronized_segments_strict_validation": segment_validation,
                    "independent_yue_checker": upstream_output,
                    "raw_source_preserved": result.abc == source_text,
                    "vocal_absence_confirmed": False,
                    "source_events_removed": record.source_events_removed,
                    "source_events_changed": record.source_events_changed,
                    "events_preserved_in_converted_prefix": record.events_preserved,
                    "events_modified_by_smart_candidates": record.events_modified,
                    "converted_measures": record.converted_measures,
                    "raw_preserved_measures": record.raw_preserved_measures,
                    "resynchronized_segments": record.resynchronized_segments,
                    "source_music_coverage": record.source_music_coverage,
                    "output_contains_full_source_music": record.output_contains_full_source_music,
                    "duration_known": record.duration_known,
                    "unknown_numeric_outputs_use_zero_sentinel": not record.duration_known,
                    "verbatim_spans": spans,
                    "diagnostic_segments": [
                        {key: value for key, value in segment.items() if key != "abc"}
                        for segment in result.diagnostic_segments
                    ],
                    "downstream_risk": conversion.get("downstream_risk", "YuE2 may reject raw ABC."),
                    "yue2_runtime_tested": False,
                }
                artifact_directory = run_directory / partial_status / source_path.stem
                _partial_raw_artifacts(
                    artifact_directory,
                    source_bytes,
                    source_text,
                    result.abc,
                    result.repaired_abc,
                    spans,
                    validation,
                    before_payload,
                    result.candidate_log,
                    result.conversion_status,
                    events_after,
                    result.diagnostic_segments,
                )
                record.artifact_directory = artifact_directory.relative_to(run_directory).as_posix()
                record.details = {
                    "baseline_converted": baseline.get(source_path.name),
                    "internal_status": partial_status,
                    "conversion_status": result.conversion_status,
                    "source_conversion_status": conversion.get("source_conversion_status"),
                    "vocal_absence_confirmed": False,
                    "verbatim_span_count": len(spans),
                    "source_events_removed": record.source_events_removed,
                    "source_events_changed": record.source_events_changed,
                    "events_preserved": record.events_preserved,
                    "events_modified": record.events_modified,
                    "converted_measures": record.converted_measures,
                    "raw_preserved_measures": record.raw_preserved_measures,
                    "resynchronized_segments": record.resynchronized_segments,
                    "source_music_coverage": record.source_music_coverage,
                    "output_contains_full_source_music": record.output_contains_full_source_music,
                    "duration_known": record.duration_known,
                    "independent_checker": upstream_output,
                }
                return record
            if not result.repaired_abc:
                conversion_error = "The repaired pre-transfer score could not be serialized."
            else:
                repaired_score, repaired_parser, repaired_error = _parse_score(
                    result.repaired_abc,
                    "strict",
                )
                if repaired_score is None:
                    conversion_error = f"Repaired score failed strict validation: {repaired_error}"
                else:
                    repaired_payload = _event_payload(
                        "repaired_pre_transfer",
                        repaired_score,
                        repaired_parser,
                        record.sha256,
                        True,
                        "",
                    )
                    output_score, output_parser, output_error = _parse_score(result.abc, "strict")
                    if output_score is None:
                        conversion_error = f"Converted score failed strict validation: {output_error}"
                    else:
                        internal_status = "accepted"
                        after_payload = _event_payload(
                            "converted",
                            output_score,
                            output_parser,
                            record.sha256,
                            True,
                            "",
                        )
                        scratch_directory = run_directory / "checker_candidates"
                        scratch_directory.mkdir(parents=True, exist_ok=True)
                        scratch_path = scratch_directory / f"{source_path.stem}.abc"
                        upstream_output = _check_upstream_output(
                            checker,
                            checker_path,
                            result.abc,
                            source_path,
                            scratch_path,
                            upstream_source.get("status") == "accepted",
                        )
                        record.upstream_status = (
                            "accepted"
                            if upstream_output.get("python_parser") == "accepted"
                            and upstream_output.get("inspect_status") == "accepted"
                            else str(upstream_output.get("python_parser", "error"))
                            if upstream_output.get("python_parser") != "accepted"
                            else str(upstream_output.get("inspect_status", "error"))
                        )
                        if scratch_path.exists():
                            try:
                                scratch_path.unlink()
                            except Exception as exc:
                                LOGGER.error(
                                    f"Unable to remove temporary checker file {scratch_path}: {exc}\n"
                                    f"{traceback.format_exc()}"
                                )

                        warnings = json.loads(result.warnings) if result.warnings else {}
                        transfer = warnings.get("transfer", {})
                        diagnostics = result.diagnostics
                        candidate_log = result.candidate_log
                        record.vocal_notes = sum(
                            1
                            for event in output_score.voices["vocal"].events
                            if event.kind == "note"
                        )
                        record.vocal_transferred = (
                            transfer.get("fully_transferred", 0)
                            + transfer.get("partially_transferred", 0)
                        )
                        record.vocal_blocked = transfer.get("not_transferred", 0)
                        record.ins_changed = (
                            transfer.get("ins_notes_changed", 0)
                            + transfer.get("ins_notes_repaired", 0)
                        )
                        source_changes = warnings.get("conversion", {}).get(
                            "source_musical_event_changes",
                            {},
                        )
                        if record.events_modified is None:
                            record.events_modified = source_changes.get("events_modified")
                        record.ins_preservation = (
                            "verified" if transfer.get("ins_preservation_verified") == 1 else "unknown"
                        )
                        record.vocal_absence_confirmed = str(
                            warnings.get("conversion", {}).get("vocal_absence_confirmed", False)
                        ).lower()
                        record.verbatim_span_count = len(result.verbatim_spans)
                        record.duration_drift_seconds = (
                            f"{_score_seconds(output_score) - _score_seconds(repaired_score):+.9f}"
                        )
                        conversion_details = warnings.get("conversion", {})
                        source_changes = conversion_details.get("source_musical_event_changes", {})
                        duration_changes = source_changes.get("duration_changes", [])
                        vocal_extensions = [
                            change
                            for change in duration_changes
                            if change.get("voice", "").lower() == "vocal"
                            and Fraction(str(change.get("repaired_duration", "0")))
                            > Fraction(str(change.get("original_duration", "0")))
                        ]
                        record.vocal_duration_extensions = (
                            len(vocal_extensions)
                            if result.conversion_status == "SMART_RECOVERED"
                            else 0
                        )
                        output_bars = len(output_score.voices["ins"].measures)
                        source_reference_score = source_score or extended_score or repaired_score
                        source_reference = (
                            "strict_raw"
                            if source_score is not None
                            else "extended_raw"
                            if extended_score is not None
                            else "repaired_candidate"
                        )
                        source_measure_count = len(_build_grid(source_reference_score))
                        record.added_output_measures = max(0, output_bars - source_measure_count)
                        silent_intervals = _full_measure_silence_intervals(source_reference_score)
                        silent_note_hits = [
                            {
                                "onset": str(event.onset),
                                "duration": str(event.duration),
                                "pitch": event.pitch,
                                "silence_interval": [str(start), str(end)],
                            }
                            for event in output_score.voices["ins"].events
                            if event.kind == "note"
                            for start, end in silent_intervals
                            if event.onset < end and event.onset + event.duration > start
                        ]
                        record.notes_in_source_silence = len(silent_note_hits)
                        expected_ins_notes, _ = _transfer_melody(repaired_score, DEFAULT_MELODY_POLICY)
                        expected_timeline = Counter(
                            (event.onset, event.duration, event.pitch)
                            for event in expected_ins_notes
                            if event.kind == "note"
                        )
                        output_timeline = Counter(
                            (event.onset, event.duration, event.pitch)
                            for event in output_score.voices["ins"].events
                            if event.kind == "note"
                        )
                        record.exact_transfer_timeline = (
                            "exact" if output_timeline == expected_timeline else "different"
                        )
                        preservation_errors = []
                        if record.added_output_measures:
                            preservation_errors.append(
                                f"conversion added {record.added_output_measures} output measures"
                            )
                        if record.notes_in_source_silence:
                            preservation_errors.append(
                                f"conversion placed {record.notes_in_source_silence} notes in source silence"
                            )
                        if record.exact_transfer_timeline != "exact":
                            preservation_errors.append("converted note timeline differs from exact Vocal transfer")
                        if preservation_errors:
                            internal_status = "preservation_rejected"
                            record.error = "; ".join(preservation_errors)
                        strict_equal = (
                            source_score is not None
                            and _score_signature(source_score) == _score_signature(repaired_score)
                        )
                        repair_categories = [_repair_category(item) for item in diagnostics]
                        source_repair_delta = (
                            _delta_summary(source_score, repaired_score)
                            if source_score is not None
                            else None
                        )
                        if source_repair_delta is not None:
                            record.source_events_removed = sum(
                                voice["removed_count"]
                                for voice in source_repair_delta["voices"].values()
                            )
                            record.source_events_changed = sum(
                                sum(voice["changed_counts"].values())
                                for voice in source_repair_delta["voices"].values()
                            )
                            source_event_count = sum(len(voice.events) for voice in source_score.voices.values())
                            record.events_preserved = max(
                                0,
                                source_event_count
                                - (record.source_events_removed or 0)
                                - (record.source_events_changed or 0),
                            )
                            record.events_modified = record.source_events_changed
                            ins_delta = source_repair_delta["voices"].get("ins", {})
                            record.ins_changed += ins_delta.get("removed_count", 0) + sum(
                                ins_delta.get("changed_counts", {}).values()
                            )
                        record.category = (
                            "EXACT"
                            if strict_equal and all(item == "EXACT" for item in repair_categories)
                            else "HEURISTIC"
                        )
                        record.musical_uncertainty = record.category != "EXACT" or any(
                            item.code == "unsupported_chord_quality" for item in diagnostics
                        )
                        upstream_accepted = record.upstream_status == "accepted"
                        if internal_status == "accepted" and upstream_accepted:
                            record.status = "accepted"
                        elif internal_status != "accepted":
                            record.status = "rejected_internal"
                        else:
                            record.status = "rejected_upstream"
                            record.error = (
                                upstream_output.get("python_parser_error")
                                or upstream_output.get("inspect_stderr")
                                or "The independent YuE checker rejected the converted score."
                            )
                        validation = {
                            "file": source_path.name,
                            "source_sha256": record.sha256,
                            "parser": {
                                "raw_strict": {
                                    "status": "accepted" if source_score is not None else "rejected",
                                    "error": strict_error,
                                },
                                "raw_extended": {
                                    "status": "accepted" if extended_score is not None else "rejected",
                                    "error": extended_error,
                                },
                                "repaired_strict": "accepted" if repaired_score is not None else "rejected",
                                "converted_strict": "accepted" if output_score is not None else "rejected",
                            },
                            "internal_validator": internal_status,
                            "independent_yue_checker": {
                                "source": upstream_source,
                                "output": upstream_output,
                            },
                            "ins_preservation": {
                                "status": record.ins_preservation,
                                "preserved_notes": transfer.get("ins_notes_preserved", 0),
                                "changed_notes": record.ins_changed,
                                "unknown_changes": transfer.get("ins_changes_unknown", 0),
                            },
                            "vocal": {
                                "sounding_notes_after_conversion": record.vocal_notes,
                                "transferred": record.vocal_transferred,
                                "blocked": record.vocal_blocked,
                            },
                            "raw_to_repaired_event_delta": _delta_counts(source_repair_delta),
                            "duration": {
                                "source_seconds": _score_seconds(source_score) if source_score else None,
                                "repaired_seconds": _score_seconds(repaired_score),
                                "converted_seconds": _score_seconds(output_score),
                                "conversion_drift_seconds": record.duration_drift_seconds,
                                "source_duration_whole_notes": str(_score_end(source_score))
                                if source_score
                                else None,
                                "repaired_duration_whole_notes": str(_score_end(repaired_score)),
                            },
                            "musical_preservation": {
                                "vocal_duration_extensions": vocal_extensions,
                                "added_output_measures": record.added_output_measures,
                                "source_full_measure_silence_intervals": [
                                    [str(start), str(end)] for start, end in silent_intervals
                                ],
                                "source_timeline_reference": source_reference,
                                "notes_in_source_silence": silent_note_hits,
                                "exact_transfer_timeline": record.exact_transfer_timeline,
                                "errors": preservation_errors,
                            },
                            "chords": {
                                "raw": [
                                    [str(onset), chord]
                                    for onset, chord in _chord_signature(source_score)
                                ]
                                if source_score
                                else None,
                                "repaired": [
                                    [str(onset), chord]
                                    for onset, chord in _chord_signature(repaired_score)
                                ],
                                "converted": [
                                    [str(onset), chord]
                                    for onset, chord in _chord_signature(output_score)
                                ],
                            },
                            "errors": [record.error] if record.error else [],
                            "warnings": [item.to_dict() for item in diagnostics],
                            "category": record.category,
                            "offline_suitability": (
                                "ready_for_further_offline_check"
                                if record.status == "accepted"
                                else "musical_preservation_rejected"
                                if record.status == "rejected_internal"
                                else "independent_checker_rejected"
                            ),
                            "yue2_runtime_tested": False,
                        }
                        repairs_payload = _repairs_payload(
                            diagnostics,
                            candidate_log,
                            source_text,
                            result.repaired_abc,
                            source_score,
                            repaired_score,
                        )
                        if record.status == "accepted":
                            artifact_directory = run_directory / "files" / source_path.stem
                            artifact_directory.mkdir(parents=True, exist_ok=False)
                            record.artifact_directory = artifact_directory.relative_to(run_directory).as_posix()
                            try:
                                (artifact_directory / "source.abc").write_bytes(source_bytes)
                            except Exception as exc:
                                path = artifact_directory / "source.abc"
                                LOGGER.error(
                                    f"Unable to write source file {path}: {exc}\n{traceback.format_exc()}"
                                )
                                raise
                            _write_text(artifact_directory / "repaired.abc", result.repaired_abc)
                            _write_text(artifact_directory / "converted.abc", result.abc)
                            _write_json(artifact_directory / "events_before.json", before_payload)
                            _write_json(artifact_directory / "events_repaired.json", repaired_payload)
                            _write_json(artifact_directory / "events_after.json", after_payload)
                            _write_json(artifact_directory / "repairs.json", repairs_payload)
                            _write_json(artifact_directory / "validation.json", validation)
                            _write_text(
                                artifact_directory / "diff.md",
                                _build_diff(
                                    source_score,
                                    source_score is not None,
                                    repaired_score,
                                    output_score,
                                    transfer,
                                ),
                            )
                        else:
                            artifact_directory = run_directory / "rejected" / source_path.stem
                            diagnostics_payload = {
                                "source_sha256": record.sha256,
                                "source_strict_error": strict_error,
                                "source_extended_error": extended_error,
                                "conversion_error": conversion_error,
                                "events_before": before_payload,
                                "events_repaired": repaired_payload,
                                "candidate_event_state": after_payload,
                                "validation": validation,
                                "last_trusted_state": after_payload,
                            }
                            _failure_artifacts(
                                artifact_directory,
                                source_bytes,
                                diagnostics_payload,
                                candidate_log,
                                result.abc,
                                record.error,
                            )
                            record.artifact_directory = artifact_directory.relative_to(
                                run_directory
                            ).as_posix()
                        record.details = {
                            "baseline_converted": baseline.get(source_path.name),
                            "internal_status": internal_status,
                            "upstream_source_status": upstream_source.get("status"),
                            "upstream_compare_status": upstream_output.get("ins_compare_status"),
                            "vocal_duration_extensions": vocal_extensions,
                            "added_output_measures": record.added_output_measures,
                            "notes_in_source_silence": silent_note_hits,
                            "exact_transfer_timeline": record.exact_transfer_timeline,
                        }
                        return record

        record.status = "rejected_internal"
        record.category = "FAILED"
        record.upstream_status = "not_checked" if result is None else "not_accepted"
        record.error = conversion_error or strict_error or "No verified converted output was produced."
        diagnostics = {
            "file": source_path.name,
            "source_sha256": record.sha256,
            "source_strict_error": strict_error,
            "source_extended_error": extended_error,
            "conversion_error": conversion_error,
            "source_upstream": upstream_source,
            "raw_partial_state": _event_payload(
                "raw_strict_state",
                source_score,
                strict_parser or ABCScoreParser("strict", DEFAULT_MAX_REPAIR_UNITS),
                record.sha256,
                source_score is not None,
                strict_error,
            ),
            "last_trusted_state": _event_payload(
                "extended_candidate_state",
                extended_score,
                extended_parser or ABCScoreParser("extended", DEFAULT_MAX_REPAIR_UNITS),
                record.sha256,
                extended_score is not None,
                extended_error,
            ),
            "parser_diagnostics": [
                item.to_dict()
                for parser in (strict_parser, extended_parser)
                if parser is not None
                for item in parser.partial_state.diagnostics
            ],
            "conversion_diagnostics": [item.to_dict() for item in result.diagnostics]
            if result
            else [],
            "traceback": traceback.format_exc() if conversion_error and result is None else "",
            "yue2_runtime_tested": False,
        }
        candidate_log = result.candidate_log if result else (
            extended_parser.candidate_log if extended_parser else []
        )
        artifact_directory = run_directory / "rejected" / source_path.stem
        _failure_artifacts(
            artifact_directory,
            source_bytes,
            diagnostics,
            candidate_log,
            result.abc if result else "",
            record.error,
        )
        record.artifact_directory = artifact_directory.relative_to(run_directory).as_posix()
        record.details = {
            "baseline_converted": baseline.get(source_path.name),
            "internal_status": internal_status,
            "upstream_source_status": upstream_source.get("status"),
        }
        return record
    except Exception as exc:
        LOGGER.error(f"Unexpected audit failure for file {source_path}: {exc}\n{traceback.format_exc()}")
        record.status = "audit_error"
        record.category = "FAILED"
        record.error = str(exc)
        failed_directory = run_directory / "rejected" / source_path.stem
        if source_bytes and not failed_directory.exists():
            _failure_artifacts(
                failed_directory,
                source_bytes,
                {"file": source_path.name, "exception": str(exc), "traceback": traceback.format_exc()},
                [],
                "",
                str(exc),
            )
            record.artifact_directory = failed_directory.relative_to(run_directory).as_posix()
        return record


def _load_baseline(path: Path) -> dict[str, bool]:
    """Read per-file converter statuses from the pre-change corpus Markdown report."""
    if not path.is_file():
        LOGGER.warning(f"Baseline report is unavailable at {path}.")
        return {}
    statuses: dict[str, bool] = {}
    try:
        in_table = False
        for line in path.read_text(encoding="utf-8").splitlines():
            if line == "## Per-file conversion status":
                in_table = True
                continue
            if in_table and line.startswith("## "):
                break
            if not in_table or not line.startswith("| YuE2_ABC_"):
                continue
            columns = [item.strip() for item in line.strip("|").split("|")]
            if len(columns) >= 4:
                statuses[columns[0]] = columns[2] == "ok"
    except Exception as exc:
        LOGGER.error(f"Unable to read baseline report {path}: {exc}\n{traceback.format_exc()}")
        raise
    return statuses


def _failure_group(record: AuditRecord) -> str:
    """Group one refusal by its dominant parser or musical-structure cause."""
    message = record.error.lower()
    if "chord" in message or "quote" in message:
        return "chord syntax or dialect"
    if "unsupported" in message or "notation" in message or "token" in message:
        return "unsupported ABC notation"
    if "overlap" in message or "monophonic" in message:
        return "event overlap"
    if "tie" in message or "duration" in message or "short by" in message:
        return "timing and ties"
    if "meter" in message or "key" in message or "metadata" in message:
        return "metadata or meter grid"
    if "voice" in message or "alignment" in message:
        return "voice alignment"
    return "other validation failure"


def _unique_run_directory(output_root: Path) -> Path:
    """Create a timestamped run directory and avoid collisions without overwrites."""
    output_root.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    for suffix in range(1000):
        name = f"run_{timestamp}" if suffix == 0 else f"run_{timestamp}_{suffix:03d}"
        path = output_root / name
        try:
            path.mkdir(exist_ok=False)
            return path
        except FileExistsError:
            continue
        except Exception as exc:
            LOGGER.error(f"Unable to create audit run directory {path}: {exc}\n{traceback.format_exc()}")
            raise
    raise FileExistsError(f"Unable to create a unique audit directory under {output_root}.")


def _display_path(path: Path) -> str:
    """Prefer a workspace-relative path in Markdown summaries."""
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return str(path.resolve())


def _write_summaries(
    run_directory: Path,
    records: list[AuditRecord],
    config: dict[str, Any],
) -> None:
    """Write CSV, JSON, and Markdown corpus summaries after every file has a status."""
    csv_path = run_directory / "summary.csv"
    try:
        with csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
            writer.writeheader()
            for record in records:
                writer.writerow({key: getattr(record, key) for key in SUMMARY_FIELDS})
    except Exception as exc:
        LOGGER.error(f"Unable to write file {csv_path}: {exc}\n{traceback.format_exc()}")
        raise

    counts = Counter(record.status for record in records)
    categories = Counter(record.category for record in records)
    failures = Counter(_failure_group(item) for item in records if item.status != "accepted")
    internal_successes = sum(
        item.details.get("internal_status") == "accepted" for item in records
    )
    upstream_successes = sum(item.upstream_status == "accepted" for item in records)
    partial_raw_count = sum(item.conversion_status == "PARTIAL_RAW" for item in records)
    smart_partial_count = sum(item.conversion_status == "SMART_PARTIAL" for item in records)
    smart_partial_fallbacks = [
        item for item in records if item.details.get("source_conversion_status") == "SMART_PARTIAL"
    ]
    verbatim_span_count = sum(item.verbatim_span_count for item in records)
    unconfirmed_vocal_count = sum(item.vocal_absence_confirmed == "no" for item in records)
    known_source_events_removed = sum(item.source_events_removed or 0 for item in records)
    known_source_events_changed = sum(item.source_events_changed or 0 for item in records)
    source_event_delta_unknown = sum(item.source_events_removed is None for item in records)
    vocal_events_transferred = sum(item.vocal_transferred for item in records)
    vocal_events_blocked = sum(item.vocal_blocked for item in records)
    original_ins_events_changed = sum(item.ins_changed for item in records)
    events_preserved = sum(item.events_preserved or 0 for item in records)
    events_modified = sum(item.events_modified or 0 for item in records)
    smart_recovered_records = [item for item in records if item.conversion_status == "SMART_RECOVERED"]
    duration_extension_cases = [item for item in records if item.vocal_duration_extensions]
    added_measure_cases = [item for item in records if item.added_output_measures]
    source_silence_cases = [item for item in records if item.notes_in_source_silence]
    transfer_mismatch_cases = [item for item in records if item.exact_transfer_timeline == "different"]
    transfer_checked_count = sum(item.exact_transfer_timeline in {"exact", "different"} for item in records)
    old_status = {
        item.file_name: item.details.get("baseline_converted")
        for item in records
    }
    regressions = [
        item.file_name
        for item in records
        if old_status[item.file_name] is True and item.details.get("internal_status") != "accepted"
    ]
    gains = [
        item.file_name
        for item in records
        if old_status[item.file_name] is False and item.details.get("internal_status") == "accepted"
    ]
    summary = {
        "config": config,
        "counts": {
            "files": len(records),
            "status": dict(counts),
            "category": dict(categories),
            "failure_category": dict(failures),
            "internal_conversion_successes": internal_successes,
            "independent_checker_successes": upstream_successes,
            "partial_raw_results": partial_raw_count,
            "smart_partial_results": smart_partial_count,
            "smart_partial_source_fallbacks": len(smart_partial_fallbacks),
            "fully_validated": internal_successes,
            "upstream_validated": upstream_successes,
            "raw_only_results": partial_raw_count,
            "verbatim_spans_preserved": verbatim_span_count,
            "events_preserved": events_preserved,
            "events_modified": events_modified,
            "source_events_removed_during_repair": known_source_events_removed,
            "source_events_changed_during_repair": known_source_events_changed,
            "files_with_unknown_source_event_delta": source_event_delta_unknown,
            "vocal_absence_not_confirmed": unconfirmed_vocal_count,
            "vocal_events_transferred": vocal_events_transferred,
            "vocal_events_not_transferred": vocal_events_blocked,
            "original_ins_events_changed_or_repaired": original_ins_events_changed,
            "baseline_regressions": len(regressions),
            "baseline_gains": len(gains),
            "conversion_rate_percent": 100 * internal_successes / len(records) if records else 0,
            "upstream_acceptance_percent_of_all_files": (
                100 * upstream_successes / len(records) if records else 0
            ),
            "exact_percent_of_all_files": 100 * categories["EXACT"] / len(records) if records else 0,
        },
        "smart_partial_source_coverage": {
            "converted_measures": sum(item.converted_measures for item in smart_partial_fallbacks),
            "raw_preserved_measures": sum(item.raw_preserved_measures for item in smart_partial_fallbacks),
            "resynchronized_segments": sum(item.resynchronized_segments for item in smart_partial_fallbacks),
            "files_output_with_full_source_music": sum(
                item.output_contains_full_source_music for item in smart_partial_fallbacks
            ),
            "files_with_known_duration": sum(item.duration_known for item in smart_partial_fallbacks),
            "minimum_source_music_coverage": min(
                (item.source_music_coverage for item in smart_partial_fallbacks),
                default=0.0,
            ),
        },
        "smart_musical_preservation_audit": {
            "smart_recovered_files": len(smart_recovered_records),
            "files_with_vocal_duration_extensions": [
                {
                    "file_name": item.file_name,
                    "changes": item.details.get("vocal_duration_extensions", []),
                }
                for item in duration_extension_cases
            ],
            "files_with_added_output_measures": [
                {"file_name": item.file_name, "count": item.added_output_measures}
                for item in added_measure_cases
            ],
            "files_with_notes_inside_source_full_measure_silence": [
                {
                    "file_name": item.file_name,
                    "notes": item.details.get("notes_in_source_silence", []),
                }
                for item in source_silence_cases
            ],
            "exact_transfer_timeline_checked": transfer_checked_count,
            "exact_transfer_timeline_mismatches": [item.file_name for item in transfer_mismatch_cases],
        },
        "baseline_regressions": regressions,
        "baseline_gains": gains,
        "files": [asdict(record) for record in records],
    }
    _write_json(run_directory / "summary.json", summary)
    category_rows = [f"| {key} | {value} |" for key, value in sorted(categories.items())]
    failure_rows = [f"| {key} | {value} |" for key, value in sorted(failures.items())]
    per_file_rows = [
        textwrap.fill(
            f"- `{item.file_name}`: status={item.status}; type={item.category}; "
            f"conversion={item.conversion_status}; spans={item.verbatim_span_count}; "
            f"converted_measures={item.converted_measures}; raw_measures={item.raw_preserved_measures}; "
            f"resync={item.resynchronized_segments}; coverage={item.source_music_coverage:.3f}; "
            f"full_source={item.output_contains_full_source_music}; duration_known={item.duration_known}; "
            f"repairs={item.repair_count}; upstream={item.upstream_status}; "
            f"Ins={item.ins_preservation}, changed={item.ins_changed}; "
            f"Vocal moved={item.vocal_transferred}, blocked={item.vocal_blocked}; "
            f"Vocal duration extensions={item.vocal_duration_extensions}; added bars={item.added_output_measures}; "
            f"notes in source silence={item.notes_in_source_silence}; transfer={item.exact_transfer_timeline}; "
            f"drift={item.duration_drift_seconds}s; uncertain={item.musical_uncertainty}; "
            f"artifacts=`{item.artifact_directory}`.",
            width=110,
            subsequent_indent="  ",
        )
        for item in records
    ]
    report = [
        "# YuE2 Auto Instrumental full corpus audit",
        "",
        f"- Corpus: `{config['corpus_display_path']}` ({len(records)} source files).",
        f"- Run: `{config['run_display_path']}`.",
        "- The independent checker is an offline snapshot; YuE2 runtime and audio were not tested.",
        "",
        "## Summary",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| Internal conversions | {internal_successes}/{len(records)} |",
        f"| Internal conversion rate | {100 * internal_successes / len(records):.1f}% |",
        f"| Accepted by independent checker | {upstream_successes}/{len(records)} |",
        f"| Independent checker rate | {100 * upstream_successes / len(records):.1f}% |",
        f"| SMART_PARTIAL results | {smart_partial_count} |",
        f"| Previous SMART_PARTIAL cases now using raw fallback | {len(smart_partial_fallbacks)} |",
        f"| Partial raw fallbacks | {partial_raw_count} |",
        f"| Verbatim spans preserved | {verbatim_span_count} |",
        f"| Known source events preserved | {events_preserved} |",
        f"| Known source events modified | {events_modified} |",
        f"| Source note events removed during repair | {known_source_events_removed} |",
        f"| Source note events changed during repair | {known_source_events_changed} |",
        f"| Files with unknown raw-to-repaired event delta | {source_event_delta_unknown} |",
        f"| Vocal events transferred or partially transferred | {vocal_events_transferred} |",
        f"| Vocal events not transferred | {vocal_events_blocked} |",
        f"| Original Ins events changed or repaired | {original_ins_events_changed} |",
        f"| Vocal absence not confirmed | {unconfirmed_vocal_count} |",
        f"| Failed internal validation or conversion | {counts['rejected_internal']} |",
        f"| Rejected by independent checker | {counts['rejected_upstream']} |",
        f"| Audit errors | {counts['audit_error']} |",
        f"| EXACT | {categories['EXACT']} |",
        f"| EXACT rate of full corpus | {100 * categories['EXACT'] / len(records):.1f}% |",
        f"| HEURISTIC | {categories['HEURISTIC']} |",
        f"| FAILED | {categories['FAILED']} |",
        f"| Baseline conversion regressions | {len(regressions)} |",
        f"| Baseline conversion gains | {len(gains)} |",
        f"| SMART_RECOVERED files checked for musical preservation | {len(smart_recovered_records)} |",
        f"| SMART files with Vocal duration extensions | {len(duration_extension_cases)} |",
        f"| Added output measures caused by conversion | {sum(item.added_output_measures for item in records)} |",
        f"| Notes in source full-measure silence | {sum(item.notes_in_source_silence for item in records)} |",
        f"| Exact transfer timelines checked | {transfer_checked_count} |",
        f"| Exact transfer timeline mismatches | {len(transfer_mismatch_cases)} |",
        "",
        "## SMART musical preservation audit",
        "",
        "The per-file CSV/JSON records count selected Smart duration extensions, converted measures added beyond the",
        "repaired source, note events overlapping source bars that are full rests in both voices, and exact",
        "onset/duration/pitch agreement for the Vocal-to-Ins transfer. Raw fallbacks have no converted timeline to",
        "compare and are marked `unknown`.",
        "",
        f"- Smart recovered: {len(smart_recovered_records)} files.",
        f"- Vocal duration extension cases: {len(duration_extension_cases)}.",
        f"- Added output measures: {sum(item.added_output_measures for item in records)}.",
        f"- Notes inside source full-measure silence: {sum(item.notes_in_source_silence for item in records)}.",
        f"- Exact transfer mismatches: {len(transfer_mismatch_cases)} of {transfer_checked_count} checked files.",
        "",
        "### Files with duration extensions",
        "",
        *(
            [
                f"- `{item.file_name}`: "
                + "; ".join(
                    f"{change.get('original_duration')} → {change.get('repaired_duration')} "
                    f"at {change.get('source_span', {}).get('line')}:{change.get('source_span', {}).get('text')}"
                    for change in item.details.get("vocal_duration_extensions", [])
                )
                for item in duration_extension_cases
            ]
            or ["- None."]
        ),
        "",
        "### Files with added measures or notes in source silence",
        "",
        *(
            [
                f"- `{item.file_name}`: added measures={item.added_output_measures}; "
                f"notes in silence={item.notes_in_source_silence}."
                for item in records
                if item.added_output_measures or item.notes_in_source_silence
            ]
            or ["- None."]
        ),
        "",
        "## SMART_PARTIAL source coverage",
        "",
        "| Metric | Result |",
        "|---|---:|",
        (
            f"| Converted measures, including diagnostics | "
            f"{sum(item.converted_measures for item in smart_partial_fallbacks)} |"
        ),
        (
            f"| Raw-preserved source measures | "
            f"{sum(item.raw_preserved_measures for item in smart_partial_fallbacks)} |"
        ),
        (
            f"| Strictly converted resynchronized segments | "
            f"{sum(item.resynchronized_segments for item in smart_partial_fallbacks)} |"
        ),
        (
            f"| Files whose main output contains the full source music | "
            f"{sum(item.output_contains_full_source_music for item in smart_partial_fallbacks)} / "
            f"{len(smart_partial_fallbacks)} |"
        ),
        (
            f"| Files with known full-score duration | "
            f"{sum(item.duration_known for item in smart_partial_fallbacks)} / "
            f"{len(smart_partial_fallbacks)} |"
        ),
        (
            f"| Minimum source music coverage | "
            f"{min((item.source_music_coverage for item in smart_partial_fallbacks), default=0.0):.3f} |"
        ),
        "",
        "## Categories",
        "",
        "| Category | Files |",
        "|---|---:|",
        *category_rows,
        "",
        "## Remaining refusal classes",
        "",
        "| Cause | Files |",
        "|---|---:|",
        *failure_rows,
        "",
        "## Per-file results",
        "",
        *per_file_rows,
        "",
        "## Baseline changes",
        "",
        textwrap.fill(
            f"Regressions: {', '.join(regressions) if regressions else 'none'}.",
            width=110,
        ),
        textwrap.fill(
            f"Gains: {', '.join(gains) if gains else 'none'}.",
            width=110,
        ),
        "",
    ]
    _write_text(run_directory / "summary.md", "\n".join(report))


def _parse_args() -> argparse.Namespace:
    """Read corpus, checker, output, and comparison paths from the command line."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--checker", type=Path, default=DEFAULT_CHECKER)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--on-unrecoverable",
        choices=("error", "smart", "continue"),
        default=DEFAULT_ON_UNRECOVERABLE,
    )
    return parser.parse_args()


def main() -> None:
    """Audit all current raw ABC files and persist every success or refusal."""
    arguments = _parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    corpus_directory = arguments.corpus.resolve()
    checker_path = arguments.checker.resolve()
    if not corpus_directory.is_dir():
        raise FileNotFoundError(f"ABC corpus directory does not exist: {corpus_directory}")
    if not checker_path.is_file():
        raise FileNotFoundError(f"Official YuE checker snapshot does not exist: {checker_path}")
    source_files = sorted(path for path in corpus_directory.glob("YuE2_ABC_*.txt") if path.is_file())
    if not source_files:
        raise FileNotFoundError(f"No YuE2 ABC files found in {corpus_directory}.")
    run_directory = _unique_run_directory(arguments.output_root.resolve())
    checker = _load_upstream_checker(checker_path)
    baseline = _load_baseline(arguments.baseline.resolve())
    config = {
        "run_directory": str(run_directory),
        "corpus_directory": str(corpus_directory),
        "run_display_path": _display_path(run_directory),
        "corpus_display_path": _display_path(corpus_directory),
        "corpus_file_count": len(source_files),
        "checker_path": str(checker_path),
        "baseline_report": str(arguments.baseline.resolve()),
        "repair_mode": DEFAULT_REPAIR_MODE,
        "on_unrecoverable": arguments.on_unrecoverable,
        "melody_policy": DEFAULT_MELODY_POLICY,
        "lyrics_mode": DEFAULT_LYRICS_MODE,
        "padding_seconds": DEFAULT_PADDING_SECONDS,
        "max_repair_units": DEFAULT_MAX_REPAIR_UNITS,
        "node_save_debug_artifacts_default": False,
        "python_version": sys.version,
        "yue2_runtime_tested": False,
    }
    _write_json(run_directory / "config.json", config)
    records: list[AuditRecord] = []
    iterator: Iterable[Path] = tqdm(source_files, desc="YuE2 ABC audit", unit="file") if tqdm else source_files
    for index, source_path in enumerate(iterator, start=1):
        record = _audit_one_file(
            source_path,
            run_directory,
            checker,
            checker_path,
            baseline,
            arguments.on_unrecoverable,
        )
        records.append(record)
        if tqdm is None and (index == 1 or index % 10 == 0 or index == len(source_files)):
            LOGGER.info(f"Audited {index}/{len(source_files)} files.")
    scratch_directory = run_directory / "checker_candidates"
    if scratch_directory.is_dir():
        try:
            scratch_directory.rmdir()
        except OSError:
            LOGGER.warning(f"Temporary checker directory still contains files: {scratch_directory}")
    _write_summaries(run_directory, records, config)
    LOGGER.info(f"Audit complete: {run_directory}")


if __name__ == "__main__":
    main()
