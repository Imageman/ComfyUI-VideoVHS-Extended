"""Standalone unit and real-corpus regression checks for the YuE2 node."""

from __future__ import annotations

import importlib.util
import json
import logging
import random
import sys
import textwrap
import tempfile
import traceback
import unittest
from collections import Counter
from fractions import Fraction
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from yue2_test_support import dependency, verify_manifest

from yue2_auto_instrumental import ABCScoreParser, ScoreError, convert_abc


LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
CORPUS_DIR = ROOT / "tests" / "raw_abc"
SBL_PATH = dependency("YuE2Instrumental.py")
REPORT_PATH = ROOT / "doc" / "yue2_auto_instrumental_corpus_report.md"


def _percentage(part: int, total: int) -> str:
    """Format a bounded percentage without dividing by zero."""
    return f"{100.0 * part / total:.1f}%" if total else "n/a"


def _classify_error(message: str) -> str:
    """Group a converter failure into a compact musical diagnostic category."""
    normalized = message.lower()
    if "tie" in normalized:
        return "ties"
    if "measure" in normalized or "duration" in normalized:
        return "measure duration or alignment"
    if "meter" in normalized or "key" in normalized:
        return "meter or key metadata"
    if "chord" in normalized:
        return "chord symbols"
    if "voice" in normalized:
        return "voice structure"
    if "notation" in normalized or "token" in normalized:
        return "unsupported ABC token"
    return "other validation error"


def _make_score(
    vocal: str,
    ins: str,
    meter: str = "4/4",
    note_length: str = "1/8",
    key: str = "C",
    tempo: str = "1/4=120",
) -> str:
    """Build a compact two-voice ABC fixture for focused tests."""
    return (
        f"X:1\nM:{meter}\nL:{note_length}\nQ:{tempo}\nK:{key}\n"
        f"V: Vocal\n{vocal}\nV: Ins\n{ins}\n"
    )


def _load_sbl_reference() -> type[Any]:
    """Load the baseline converter directly from the preserved reference file."""
    if not SBL_PATH.is_file():
        raise FileNotFoundError(f"SBL reference file does not exist: {SBL_PATH}")
    try:
        spec = importlib.util.spec_from_file_location("sbl_yue2_reference", SBL_PATH)
        if spec is None or spec.loader is None:
            raise ImportError(f"Unable to create an import loader for {SBL_PATH}")
        module: ModuleType = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.YuE2Instrumental
    except Exception as exc:
        LOGGER.error(f"Unable to load SBL reference at {SBL_PATH}: {exc}\n{traceback.format_exc()}")
        raise


def _run_corpus_report() -> dict[str, int]:
    """Compare the new converter with SBL on every source file and write a Markdown report."""
    verify_manifest()
    if not CORPUS_DIR.is_dir():
        raise FileNotFoundError(f"ABC corpus directory does not exist: {CORPUS_DIR}")
    files = sorted(path for path in CORPUS_DIR.rglob("*.txt") if path.is_file())
    if not files:
        raise FileNotFoundError(f"No source ABC files were found in {CORPUS_DIR}.")
    baseline = _load_sbl_reference()
    counts: Counter[str] = Counter()
    sbl_failures: Counter[str] = Counter()
    new_failures: Counter[str] = Counter()
    status_rows: list[str] = []
    detail_rows: list[str] = []
    rejected_details: list[str] = []
    for path in files:
        try:
            abc = path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            LOGGER.error(f"Unable to read ABC file {path}: {exc}\n{traceback.format_exc()}")
            raise
        try:
            ABCScoreParser("strict", 4).parse(abc)
            counts["strict_valid"] += 1
        except ScoreError:
            counts["strict_invalid"] += 1
        sbl_status = "rejected"
        sbl_seconds = ""
        sbl_error = ""
        try:
            sbl_result = baseline.process(abc, 0.0)
            sbl_status = "ok"
            sbl_seconds = f"{sbl_result[3]:.3f}"
            counts["sbl_success"] += 1
        except Exception as exc:
            sbl_error = str(exc)
            sbl_failures[_classify_error(sbl_error)] += 1
            counts["sbl_rejected"] += 1

        new_status = "rejected"
        new_error = ""
        seconds = ""
        repairs = 0
        transferred = 0
        dropped = 0
        preserved = "n/a"
        ins_notes = 0
        ins_changed = "0"
        ins_change_count = 0
        ins_change_unknown = False
        try:
            result = convert_abc(abc)
            details = json.loads(result.warnings) if result.warnings else {}
            transfer = details.get("transfer", {})
            new_status = "ok"
            seconds = f"{result.score_seconds:.3f}"
            repairs = result.repair_count
            transferred = transfer.get("fully_transferred", 0) + transfer.get("partially_transferred", 0)
            dropped = transfer.get("not_transferred", 0)
            preserved = str(transfer.get("ins_preservation_verified", 0) == 1).lower()
            ins_notes = transfer.get("ins_notes_preserved", 0)
            ins_change_count = transfer.get("ins_notes_changed", 0) + transfer.get("ins_notes_repaired", 0)
            ins_change_unknown = transfer.get("ins_changes_unknown", 0) > 0
            ins_changed = "unknown" if ins_change_unknown else str(ins_change_count)
            counts["new_success"] += 1
            counts["new_repaired"] += int(repairs > 0)
            counts["vocal_transferred"] += transferred
            counts["vocal_dropped"] += dropped
            counts["ins_preserved"] += int(preserved == "true")
            counts["ins_notes_total"] += ins_notes
            counts["ins_notes_changed"] += ins_change_count
            counts["ins_changes_unknown"] += int(ins_change_unknown)
        except ScoreError as exc:
            new_error = str(exc)
            new_failures[_classify_error(new_error)] += 1
            counts["new_rejected"] += 1
        except Exception as exc:
            new_status = "unexpected"
            new_error = str(exc)
            counts["new_unexpected"] += 1
            LOGGER.error(f"Unexpected converter error for {path}: {exc}\n{traceback.format_exc()}")

        counts["files"] += 1
        delta = ""
        if sbl_seconds and seconds:
            delta = f"{float(seconds) - float(sbl_seconds):+.3f}"
        relative_path = path.relative_to(CORPUS_DIR).as_posix()
        status_rows.append(
            f"| {relative_path} | {sbl_status} | {new_status} | {repairs} | "
            f"{sbl_seconds} | {seconds} | {delta} |"
        )
        detail_rows.append(
            f"| {relative_path} | {ins_notes} | {ins_changed} | {transferred} | {dropped} | {preserved} |"
        )
        if new_error:
            rejected_details.append(
                textwrap.fill(
                    f"- `{relative_path}`: {new_error}",
                    width=110,
                    subsequent_indent="  ",
                )
            )

    counts["new_without_repairs"] = counts["new_success"] - counts["new_repaired"]
    summary = [
        "# YuE2 Auto Instrumental corpus report",
        "",
        f"Corpus: `tests/raw_abc` ({counts['files']} UTF-8 files).",
        "",
        "| Metric | Result |",
        "|---|---:|",
        f"| SBL accepted | {counts['sbl_success']} |",
        f"| New converter accepted | {counts['new_success']} |",
        f"| Strict-valid inputs | {counts['strict_valid']} |",
        f"| Inputs requiring repair | {counts['strict_invalid']} |",
        f"| Accepted without repairs | {counts['new_without_repairs']} |",
        f"| Repaired by safe/extended defaults | {counts['new_repaired']} |",
        f"| Rejected for explicit/manual repair | {counts['new_rejected']} |",
        f"| Unexpected converter failures | {counts['new_unexpected']} |",
        f"| Ins notes parsed from successful inputs | {counts['ins_notes_total']} |",
        f"| Ins notes changed by repair or policy (known count) | {counts['ins_notes_changed']} |",
        f"| Inputs where invalid metadata makes Ins changes unknown | {counts['ins_changes_unknown']} |",
        f"| Internal Ins invariant checks passed | {counts['ins_preserved']} |",
        f"| Invariant violations among returned results | {counts['invariant_violations']} |",
        f"| Vocal notes transferred | {counts['vocal_transferred']} |",
        f"| Vocal notes blocked by Ins | {counts['vocal_dropped']} |",
        "",
        *textwrap.wrap(
            f"Structural success rate: {_percentage(counts['new_success'], counts['files'])}. "
            f"Repair success rate: {_percentage(counts['new_repaired'], counts['strict_invalid'])} "
            "(successful repaired files / strict-invalid inputs). "
            f"Internal Ins invariant pass rate: {_percentage(counts['ins_preserved'], counts['new_success'])}. "
            "The warning-based repair count includes source-spelling serialization diagnostics; added accidentals "
            "preserve exact pitch and do not indicate changed musical events. This checks converter invariants, "
            "not reconstruction accuracy for damaged source ABC. "
            "See the quality report for per-file exact event comparisons and uncertainty.",
            width=110,
        ),
        "",
        "YuE2 generation and live ComfyUI node loading were not tested in this environment.",
        "",
        "## Per-file conversion status",
        "",
        "| File | SBL | New | Repairs | SBL s | New s | Delta s |",
        "|---|---|---|---:|---:|---:|---:|",
        *status_rows,
        "",
        "## Per-file music statistics",
        "",
        "| File | Ins notes | Ins changed | Vocal moved | Vocal not moved | Ins checked |",
        "|---|---:|---:|---:|---:|---|",
        *detail_rows,
        "",
        "## Unrecoverable ABC inputs",
        "",
        *(rejected_details or ["None."]),
        "",
        "## Rejection categories",
        "",
        "| Category | SBL | New converter |",
        "|---|---:|---:|",
        *[
            f"| {category} | {sbl_failures[category]} | {new_failures[category]} |"
            for category in sorted(set(sbl_failures) | set(new_failures))
        ],
        "",
    ]
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text("\n".join(summary), encoding="utf-8")
    except Exception as exc:
        LOGGER.error(f"Unable to write corpus report {REPORT_PATH}: {exc}\n{traceback.format_exc()}")
        raise
    return dict(counts)


class YuE2AutoInstrumentalTests(unittest.TestCase):
    """Verify the library, node contract, and the full local corpus."""

    def test_empty_score_returns_typed_empty_outputs(self) -> None:
        """Return empty strings and zero numeric values for empty input."""
        result = convert_abc(" \n\t")
        self.assertEqual(result.abc, "")
        self.assertEqual(result.lyrics, "")
        self.assertEqual(result.max_duration, 0.0)
        self.assertEqual(result.score_seconds, 0.0)
        self.assertEqual(result.bars, 0)
        self.assertEqual(result.bpm, 0.0)
        self.assertEqual(result.info, "")
        self.assertEqual(result.warnings, "")
        self.assertEqual(result.repair_count, 0)

    def test_merged_metadata_prefix_preserves_following_music(self) -> None:
        """Split a valid meter prefix from music appended to its metadata line."""
        score = _make_score("", "Z|").replace("V: Vocal\n\n", "V: Vocal\nM:4/4C8|\n")
        parsed = ABCScoreParser("extended", 4).parse(score)
        notes = [event for event in parsed.voices["vocal"].events if event.kind == "note"]
        self.assertEqual([(event.onset, event.duration, event.pitch) for event in notes], [(0, 1, 60)])
        self.assertIn("merged_metadata_music", {item.code for item in parsed.diagnostics})

    def test_unsupported_chord_quality_is_preserved_without_substitution(self) -> None:
        """Keep a lexical chord label opaque and report its unsupported quality."""
        result = convert_abc(_make_score('"G/Dsus2"z8|', "Z|"))
        self.assertIn('"G/Dsus2"', result.abc)
        self.assertIn("unsupported_chord_quality", {item.code for item in result.diagnostics})

    def test_node_exposes_unique_mapping_and_ten_outputs(self) -> None:
        """Keep the ComfyUI registration name and ten output types stable."""
        from yue2_auto_instrumental import VHSXYuE2AutoInstrumental

        self.assertEqual(VHSXYuE2AutoInstrumental.RETURN_NAMES[0], "abc")
        self.assertEqual(len(VHSXYuE2AutoInstrumental.RETURN_TYPES), 10)
        self.assertEqual(len(VHSXYuE2AutoInstrumental.RETURN_NAMES), 10)
        self.assertEqual(VHSXYuE2AutoInstrumental.RETURN_NAMES[-1], "conversion_status")
        schema = VHSXYuE2AutoInstrumental.INPUT_TYPES()
        self.assertEqual(schema["required"]["on_unrecoverable"][0], ["error", "smart", "continue"])
        repair_tooltip = schema["required"]["repair_mode"][1]["tooltip"]
        self.assertIn("strict rejects", repair_tooltip)
        self.assertIn("safe applies deterministic repairs", repair_tooltip)
        self.assertIn("extended also tests bounded repairs", repair_tooltip)
        self.assertIn("smart forces extended; continue forces safe", repair_tooltip)
        fallback_tooltip = schema["required"]["on_unrecoverable"][1]["tooltip"]
        self.assertIn("PARTIAL_RAW keeps the ABC", fallback_tooltip)
        self.assertIn("max_duration defaults to 300 seconds", fallback_tooltip)
        self.assertIn("zero score_seconds, bars, or bpm values mean unknown", fallback_tooltip)
        self.assertIn("durations are best-effort and unvalidated", fallback_tooltip)
        self.assertIn("Vocal absence is unconfirmed", fallback_tooltip)
        self.assertEqual(
            schema["required"]["lyrics_mode"][0],
            ["sections_only", "empty", "instrumental_sections", "instrumental_only"],
        )
        self.assertTrue(schema["required"]["lyrics_mode"][1]["tooltip"])
        self.assertEqual(len(VHSXYuE2AutoInstrumental().convert("")), 10)
        self.assertEqual(VHSXYuE2AutoInstrumental().convert("")[-1], "OK")

        package_name = "vhse_yue2_registration_test"
        spec = importlib.util.spec_from_file_location(
            package_name,
            ROOT / "__init__.py",
            submodule_search_locations=[str(ROOT)],
        )
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        try:
            spec.loader.exec_module(package)
            self.assertEqual(
                package.NODE_CLASS_MAPPINGS["VHSX_YuE2AutoInstrumental"].__name__,
                VHSXYuE2AutoInstrumental.__name__,
            )
            self.assertEqual(
                package.NODE_DISPLAY_NAME_MAPPINGS["VHSX_YuE2AutoInstrumental"],
                "YuE2 Auto Instrumental (VHSX)",
            )
        finally:
            for module_name in tuple(sys.modules):
                if module_name == package_name or module_name.startswith(f"{package_name}."):
                    sys.modules.pop(module_name, None)

    def test_empty_instrumental_voice_receives_vocal_notes(self) -> None:
        """Transfer Vocal notes into an otherwise silent Ins voice."""
        result = convert_abc(_make_score("C4D4|", "Z|"))
        self.assertIn("transferred: 2", result.info)
        self.assertEqual(result.bars, 1)
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        self.assertEqual(
            [event.pitch for event in parsed.voices["ins"].events if event.kind == "note"],
            [60, 62],
        )

    def test_instrumental_voice_wins_full_overlap(self) -> None:
        """Preserve Ins note pitch and time when it overlaps Vocal completely."""
        result = convert_abc(_make_score("C8|", "G8|"))
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        notes = [event for event in parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual([(event.onset, event.duration, event.pitch) for event in notes], [(0, 1, 67)])
        self.assertIn("not transferred: 1", result.info)

    def test_partial_overlap_splits_vocal_note_around_ins(self) -> None:
        """Move only the free half of a Vocal note and keep Ins unchanged."""
        result = convert_abc(_make_score("C8|", "z4G4|"))
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        notes = [event for event in parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual(
            [(event.onset, event.duration, event.pitch) for event in notes],
            [(0, Fraction(1, 2), 60), (Fraction(1, 2), Fraction(1, 2), 67)],
        )

    def test_long_vocal_note_is_split_around_multiple_ins_notes(self) -> None:
        """Move every free fragment of a long Vocal note around several Ins notes."""
        result = convert_abc(_make_score("C8|", "G2z2A2z2|"))
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        notes = [event for event in parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual(
            [(event.onset, event.duration, event.pitch) for event in notes],
            [
                (0, Fraction(1, 4), 67),
                (Fraction(1, 4), Fraction(1, 4), 60),
                (Fraction(1, 2), Fraction(1, 4), 69),
                (Fraction(3, 4), Fraction(1, 4), 60),
            ],
        )

    def test_alternative_melody_policies_are_explicit(self) -> None:
        """Apply vocal priority and mute-only modes without leaving Vocal notes."""
        score = _make_score("C8|", "G8|")
        vocal_result = convert_abc(score, melody_policy="vocal_priority")
        vocal_parsed = ABCScoreParser("strict", 4).parse(vocal_result.abc)
        vocal_ins = [event for event in vocal_parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual([event.pitch for event in vocal_ins], [60])
        vocal_stats = json.loads(vocal_result.warnings)["transfer"]
        self.assertEqual(vocal_stats["ins_notes_changed"], 1)
        self.assertEqual(vocal_stats["ins_notes_preserved"], 0)

        mute_result = convert_abc(score, melody_policy="mute_vocal_only")
        mute_parsed = ABCScoreParser("strict", 4).parse(mute_result.abc)
        mute_ins = [event for event in mute_parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual([event.pitch for event in mute_ins], [67])
        self.assertEqual(len([event for event in mute_parsed.voices["vocal"].events if event.kind == "note"]), 0)

    def test_short_measure_is_padded_with_a_rest(self) -> None:
        """Append a diagnostic rest to a short measure in safe mode."""
        result = convert_abc(_make_score("C4|", "Z|"), repair_mode="safe")
        self.assertGreater(result.repair_count, 0)
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        self.assertEqual(parsed.voices["vocal"].measures[0].duration, 1)

    def test_overflow_preserves_note_duration_across_the_barline(self) -> None:
        """Keep a pitched event intact and serialize its overhang across barlines."""
        score = _make_score("C10|", "Z|")
        result = convert_abc(score, repair_mode="extended", max_repair_units=4)
        self.assertEqual(result.repair_count, 1)
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        ins_notes = [event for event in parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual([(event.onset, event.duration, event.pitch) for event in ins_notes], [(0, Fraction(5, 4), 60)])
        with self.assertRaises(ScoreError):
            convert_abc(score, repair_mode="strict")

    def test_missing_barline_recovery_matches_known_source_events(self) -> None:
        """Recover a deleted boundary by shifting the Vocal suffix without changing notes."""
        clean = _make_score("C8-|C2D6|", "G8|A8|")
        damaged = _make_score("C8-C2|D6|", "G8|A8|")
        expected = ABCScoreParser("strict", 4).parse(clean)
        recovered = ABCScoreParser("extended", 4).parse(damaged)
        expected_notes = [
            (event.onset, event.duration, event.pitch)
            for event in expected.voices["vocal"].events
            if event.kind == "note"
        ]
        recovered_notes = [
            (event.onset, event.duration, event.pitch)
            for event in recovered.voices["vocal"].events
            if event.kind == "note"
        ]
        self.assertEqual(recovered_notes, expected_notes)
        self.assertTrue(any("shift the Vocal event suffix" in item.repair for item in recovered.diagnostics))

    def test_overlapping_ins_notes_fail_without_modifying_ins(self) -> None:
        """Reject irreconcilable Ins overlaps instead of changing instrumental notes."""
        score = _make_score("Z|Z|", "G10|A6|")
        with self.assertRaisesRegex(ScoreError, "preserves Ins"):
            convert_abc(score)

    def test_chord_with_uppercase_full_bar_rest_is_preserved(self) -> None:
        """Keep a chord label attached to an uppercase full-measure rest."""
        result = convert_abc(_make_score('"G"Z|', "Z|"))
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        chords = [event.chord for event in parsed.voices["vocal"].events if event.kind == "chord"]
        self.assertEqual(chords, ['"G"'])

    def test_boundary_before_uppercase_full_bar_rest_is_recovered(self) -> None:
        """Insert a structural boundary before a full-bar rest without moving chords."""
        result = convert_abc(_make_score('"F#"z8"G#"Z|', "Z2|"))
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        chords = [
            (event.onset, event.chord)
            for event in parsed.voices["vocal"].events
            if event.kind == "chord"
        ]
        self.assertEqual(chords, [(Fraction(0), '"F#"'), (Fraction(1), '"G#"')])

    def test_repeat_barlines_are_normalized_as_measure_boundaries(self) -> None:
        """Treat repeat barline tokens as boundaries and report the semantic loss."""
        result = convert_abc(_make_score("C8::D8||", "G8|A8|"))
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        self.assertEqual(len(parsed.voices["ins"].measures), 2)
        details = json.loads(result.warnings)
        self.assertTrue(any("repeat playback is not modeled" in item["repair"] for item in details["diagnostics"]))

    def test_dotted_note_round_trip_preserves_exact_duration(self) -> None:
        """Serialize a dotted note as an exact rational duration."""
        result = convert_abc(_make_score("C1.|", "Z|"))
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        notes = [event for event in parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual([(event.duration, event.pitch) for event in notes], [(Fraction(3, 16), 60)])

    def test_long_integer_durations_use_supported_tied_units(self) -> None:
        """Decompose an unsupported integer multiplier into exact supported YuE lengths."""
        source = _make_score("C28z4|", "Z|").replace("L:1/8", "L:1/32")
        result = convert_abc(source)
        self.assertIn("C24-C4z4|", result.abc)
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        notes = [event for event in parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual([(event.onset, event.duration, event.pitch) for event in notes], [(0, Fraction(7, 8), 60)])

    def test_invalid_tie_is_removed_without_dropping_notes(self) -> None:
        """Remove a mismatched tie marker while preserving both pitched events."""
        result = convert_abc(_make_score("C4-D4|", "Z|"))
        details = json.loads(result.warnings)
        self.assertTrue(any("Tie does not continue" in item["problem"] for item in details["diagnostics"]))
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        self.assertEqual(len([event for event in parsed.voices["ins"].events if event.kind == "note"]), 2)

    def test_chord_symbols_from_both_voices_are_preserved(self) -> None:
        """Keep chord labels from either source voice in the final Vocal track."""
        score = _make_score('"C"z8|', '"G"G8|')
        result = convert_abc(score)
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        chords = [
            event.chord
            for event in parsed.voices["vocal"].events
            if event.kind == "chord"
        ]
        self.assertEqual(chords, ['"C"', '"G"'])

    def test_valid_ins_grid_anchors_a_shorter_vocal_voice(self) -> None:
        """Pad Vocal on the valid Ins grid without changing instrumental events."""
        score = _make_score("C8|", "G8|A8|")
        original = ABCScoreParser("extended", 4).parse(score)
        result = convert_abc(score)
        converted = ABCScoreParser("strict", 4).parse(result.abc)
        original_ins = [
            (event.onset, event.duration, event.pitch)
            for event in original.voices["ins"].events
            if event.kind == "note"
        ]
        converted_ins = [
            (event.onset, event.duration, event.pitch)
            for event in converted.voices["ins"].events
            if event.kind == "note"
        ]
        self.assertEqual(converted_ins, original_ins)
        self.assertEqual(
            len(converted.voices["vocal"].measures),
            len(converted.voices["ins"].measures),
        )

    def test_ins_meter_timeline_synchronizes_vocal_metadata(self) -> None:
        """Normalize Vocal metadata to the Ins grid while preserving Ins notes."""
        score = (
            "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\n"
            "V: Vocal\nC8|\nM:3/4\nD6|\n"
            "V: Ins\nG8|A8|\n"
        )
        result = convert_abc(score)
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        self.assertEqual(
            [measure.meter for measure in parsed.voices["vocal"].measures],
            [measure.meter for measure in parsed.voices["ins"].measures],
        )
        self.assertTrue(any("Use Ins as the output grid" in item.repair for item in result.diagnostics))

    def test_tied_note_keeps_pitch_across_key_change(self) -> None:
        """Carry the tied pitch through a synchronized key-signature change."""
        score = (
            "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:Bb\n"
            "V: Vocal\nB8-|\nK:C\nB8|\n"
            "V: Ins\nZ|\nK:C\nZ|\n"
        )
        result = convert_abc(score)
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        notes = [event for event in parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual([(event.onset, event.duration, event.pitch) for event in notes], [(0, 2, 70)])
        self.assertIn("_B8|", result.abc)

    def test_lyrics_modes_preserve_or_map_tags_and_keep_abc_contracts(self) -> None:
        """Keep each Lyrics policy distinct while changing comments only in instrumental_sections."""
        score = (
            "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\n"
            "% Verse\nV: Vocal\nC8|\n"
            "% Pre-chorus\nD8|\n"
            "% Chorus\nE8|\n"
            "% Mood\nF8|\n"
            "V: Ins\nZ|Z|Z|Z|\n"
        )
        sections_only = convert_abc(score, lyrics_mode="sections_only")
        empty = convert_abc(score, lyrics_mode="empty")
        instrumental_sections = convert_abc(score, lyrics_mode="instrumental_sections")
        instrumental_only = convert_abc(score, lyrics_mode="instrumental_only")

        self.assertEqual(
            sections_only.lyrics,
            "[Verse]\n[Pre-chorus]\n[Chorus]\n[Mood]",
        )
        self.assertEqual(empty.lyrics, "")
        self.assertEqual(
            instrumental_sections.lyrics,
            "[Instrumental]\n[Interlude]\n[Instrumental]",
        )
        self.assertEqual(instrumental_only.lyrics, "[Intro]\n[Instrumental]\n[Outro]")
        self.assertEqual(sections_only.abc, empty.abc)
        self.assertEqual(sections_only.abc, instrumental_only.abc)
        self.assertIn("% Instrumental", instrumental_sections.abc)
        self.assertIn("% Interlude", instrumental_sections.abc)
        self.assertNotIn("% Verse", instrumental_sections.abc)
        self.assertNotIn("% Pre-chorus", instrumental_sections.abc)
        diagnostics = {item.code for item in instrumental_sections.diagnostics}
        self.assertIn("unknown_instrumental_section", diagnostics)
        self.assertIn("duplicate_instrumental_section", diagnostics)
        self.assertIn(
            "instrumental_lyrics_may_not_match_abc",
            {item.code for item in instrumental_only.diagnostics},
        )
        continue_instrumental_only = convert_abc(
            score,
            lyrics_mode="instrumental_only",
            on_unrecoverable="continue",
        )
        self.assertEqual(continue_instrumental_only.conversion_status, "OK")

    def test_lyrics_fallback_and_duration_based_instrumental_count(self) -> None:
        """Emit Intro/Outro without source tags and use ceiling duration buckets."""
        no_sections = convert_abc(_make_score("C8|", "Z|"), lyrics_mode="sections_only")
        self.assertEqual(no_sections.lyrics, "[Intro]\n[Outro]")

        long_score = _make_score("Z21|", "Z21|")
        instrumental = convert_abc(long_score, lyrics_mode="instrumental_only")
        self.assertEqual(instrumental.score_seconds, 42.0)
        self.assertEqual(
            instrumental.lyrics,
            "[Intro]\n[Instrumental]\n[Instrumental]\n[Outro]",
        )
        self.assertEqual(instrumental.abc, convert_abc(long_score).abc)

    def test_unknown_duration_in_smart_partial_and_partial_raw_lyrics(self) -> None:
        """Use three Instrumental sections and warnings when a partial score has unknown duration."""
        partial = (
            "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\n"
            "V: Vocal\nC8|\nD4H4|\nV: Ins\nG8|A8|\n"
        )
        smart = convert_abc(partial, on_unrecoverable="smart", lyrics_mode="instrumental_only")
        self.assertEqual(smart.conversion_status, "PARTIAL_RAW")
        self.assertEqual(smart.abc, partial)
        self.assertEqual(smart.max_duration, 300.0)
        self.assertEqual(smart.lyrics.count("[Instrumental]"), 3)
        smart_diagnostics = {item["code"] for item in json.loads(smart.warnings)["diagnostics"]}
        self.assertIn("unknown_instrumental_duration", smart_diagnostics)
        self.assertIn("instrumental_lyrics_may_not_match_abc", smart_diagnostics)
        self.assertEqual(smart.abc, convert_abc(partial, on_unrecoverable="smart").abc)

        raw_source = _make_score("C4H4|", "Z|")
        raw = convert_abc(
            raw_source,
            on_unrecoverable="continue",
            lyrics_mode="instrumental_only",
        )
        self.assertEqual(raw.conversion_status, "PARTIAL_RAW")
        self.assertEqual(raw.abc, raw_source)
        self.assertEqual(raw.lyrics.count("[Instrumental]"), 3)
        raw_diagnostics = {item["code"] for item in json.loads(raw.warnings)["diagnostics"]}
        self.assertIn("unknown_instrumental_duration", raw_diagnostics)
        self.assertIn("instrumental_lyrics_may_not_match_abc", raw_diagnostics)

    def test_source_note_spelling_respects_keys_and_measure_accidentals(self) -> None:
        """Preserve natural, flat, and sharp spelling while resetting accidentals at barlines."""
        key_score = (
            "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:Bb\n"
            "V: Vocal\nZ|Z|\nV: Ins\nB8|=B8|\n"
        )
        key_result = convert_abc(key_score)
        self.assertIn("B8|=B8|", key_result.abc)

        accidental_score = _make_score("Z|Z|", "^F4F4|F8|")
        accidental_result = convert_abc(accidental_score)
        self.assertIn("^F4F4|F8|", accidental_result.abc)
        parsed = ABCScoreParser("strict", 4).parse(accidental_result.abc)
        notes = [event for event in parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual(
            [(event.onset, event.duration, event.pitch) for event in notes],
            [
                (Fraction(0), Fraction(1, 2), 66),
                (Fraction(1, 2), Fraction(1, 2), 66),
                (Fraction(1), Fraction(1), 65),
            ],
        )

        transferred = convert_abc(_make_score("_B8|", "Z|"))
        self.assertIn("_B8|", transferred.abc)

    def test_tied_source_note_keeps_accidental_across_barline(self) -> None:
        """Repeat a sharp only as needed when a tied source note crosses a barline."""
        result = convert_abc(_make_score("Z|Z|", "^F8-|^F8|"))
        self.assertIn("^F8-|^F8|", result.abc)
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        notes = [event for event in parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual([(event.onset, event.duration, event.pitch) for event in notes], [(0, 2, 66)])

    def test_enharmonic_ins_spellings_survive_with_exact_pitch(self) -> None:
        """Retain enharmonic source spellings even when they share a MIDI pitch."""
        score = _make_score("Z|Z|", "^C8|_D8|")
        result = convert_abc(score)
        self.assertIn("^C8|_D8|", result.abc)
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        notes = [event for event in parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual([event.pitch for event in notes], [61, 61])
        self.assertEqual([event.spelling for event in notes], ["C", "D"])
        self.assertEqual([event.accidental for event in notes], ["^", "_"])

    def test_tempo_changes_are_integrated_into_score_duration(self) -> None:
        """Calculate score time using each active tempo segment."""
        score = (
            "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\n"
            "V: Vocal\nC8|\nQ:1/4=60\nD8|\n"
            "V: Ins\nZ|\nQ:1/4=60\nZ|\n"
        )
        result = convert_abc(score)
        self.assertEqual(result.score_seconds, 6.0)

    def test_fractional_lengths_and_sections_are_preserved(self) -> None:
        """Keep fractional note lengths exact and produce Lyrics from section tags only."""
        score = "X:1\nM:2/4\nL:1/8\nQ:1/4=100\nK:C\n% verse\nV: Vocal\nC3/2D5/2|\nV: Ins\nZ|\n"
        result = convert_abc(score)
        self.assertEqual(result.lyrics, "[verse]")
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        notes = [event for event in parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual([event.duration for event in notes], [Fraction(3, 16), Fraction(5, 16)])

    def test_multi_measure_rests_keep_their_bar_count(self) -> None:
        """Preserve compressed multi-measure rests for both output voices."""
        result = convert_abc(_make_score("Z2|", "Z2|"))
        self.assertEqual(result.bars, 2)
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        self.assertEqual(len(parsed.voices["vocal"].measures), 2)
        self.assertEqual(len(parsed.voices["ins"].measures), 2)

    def test_meter_changes_are_kept_on_the_shared_grid(self) -> None:
        """Serialize synchronized measure-size changes and calculate duration."""
        score = (
            "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\n"
            "V: Vocal\nC8|\nM:3/4\nD6|\n"
            "V: Ins\nZ|\nM:3/4\nZ|\n"
        )
        result = convert_abc(score, padding_seconds=0.5)
        self.assertEqual(result.bars, 2)
        self.assertEqual(result.score_seconds, 3.5)
        self.assertEqual(result.max_duration, 4.0)

    def test_conversion_is_idempotent_on_its_output(self) -> None:
        """Avoid changing the musical serialization on a second conversion."""
        first = convert_abc(_make_score("C4D4|", "Z|"))
        second = convert_abc(first.abc)
        self.assertEqual(first.abc, second.abc)

    def test_generated_round_trips_preserve_instrumental_event_timelines(self) -> None:
        """Check parser-serializer-parser invariants on deterministic generated scores."""
        generator = random.Random(20261030)
        pitches = "CDEFGAB"
        for _ in range(40):
            bars: list[str] = []
            for _ in range(3):
                remaining = 8
                tokens: list[str] = []
                while remaining:
                    duration = generator.choice(
                        [value for value in (1, 2, 3, 4) if value <= remaining]
                    )
                    pitch = generator.choice(pitches)
                    tokens.append(pitch if duration == 1 else f"{pitch}{duration}")
                    remaining -= duration
                bars.append("".join(tokens))
            score = _make_score("Z3|", "|".join(bars) + "|")
            original = ABCScoreParser("strict", 4).parse(score)
            converted = convert_abc(score)
            reparsed = ABCScoreParser("strict", 4).parse(converted.abc)
            original_notes = Counter(
                (event.onset, event.duration, event.pitch)
                for event in original.voices["ins"].events
                if event.kind == "note"
            )
            converted_notes = Counter(
                (event.onset, event.duration, event.pitch)
                for event in reparsed.voices["ins"].events
                if event.kind == "note"
            )
            self.assertEqual(converted_notes, original_notes)

    def test_unsupported_note_token_is_an_explicit_error(self) -> None:
        """Reject unknown music tokens rather than silently deleting them."""
        with self.assertRaises(ScoreError):
            convert_abc(_make_score("H8|", "Z|"))

    def test_smart_returns_validated_recovery_status(self) -> None:
        """Mark a repaired, strictly validated score as SMART_RECOVERED."""
        result = convert_abc(_make_score("C4-D4|", "Z|"), on_unrecoverable="smart")
        self.assertEqual(result.conversion_status, "SMART_RECOVERED")
        conversion = json.loads(result.warnings)["conversion"]
        self.assertEqual(conversion["internal_validator"], "passed")
        self.assertTrue(conversion["vocal_absence_confirmed"])

    def test_smart_preserves_whole_raw_score_when_recovery_fails(self) -> None:
        """Keep unknown ABC tokens verbatim and avoid claiming Vocal was removed."""
        source = _make_score("C4H4|", "Z|")
        result = convert_abc(source, on_unrecoverable="smart")
        conversion = json.loads(result.warnings)["conversion"]
        self.assertEqual(result.conversion_status, "PARTIAL_RAW")
        self.assertEqual(result.abc, source)
        self.assertEqual(result.max_duration, 300.0)
        self.assertEqual(result.bars, 0)
        self.assertFalse(conversion["vocal_absence_confirmed"])
        self.assertEqual(conversion["internal_validator"], "not_passed")
        self.assertEqual(result.verbatim_spans[0]["text"], source)
        self.assertEqual(result.verbatim_spans[0]["start_offset"], 0)
        self.assertEqual(result.verbatim_spans[0]["end_offset"], len(source))

    def test_smart_recovers_a_missing_meter_from_exact_cross_voice_grid(self) -> None:
        """Select the meter hypothesis that avoids synthetic padding in both voices."""
        clean = _make_score("C4|D4|", "G4|A4|").replace("M:4/4\n", "M:2/4\n", 1)
        damaged = clean.replace("M:2/4\n", "", 1)
        result = convert_abc(damaged, on_unrecoverable="smart")
        self.assertEqual(result.conversion_status, "SMART_RECOVERED")
        repaired = ABCScoreParser("strict", 4).parse(result.repaired_abc)
        expected = ABCScoreParser("strict", 4).parse(clean)
        for voice_name in ("vocal", "ins"):
            actual_notes = [
                (event.onset, event.duration, event.pitch)
                for event in repaired.voices[voice_name].events
                if event.kind == "note"
            ]
            expected_notes = [
                (event.onset, event.duration, event.pitch)
                for event in expected.voices[voice_name].events
                if event.kind == "note"
            ]
            self.assertEqual(actual_notes, expected_notes)
        conversion = json.loads(result.warnings)["conversion"]
        self.assertIn("Insert M:2/4", conversion["selected_hypotheses"][0])

    def test_smart_repairs_a_missing_barline_without_changing_events(self) -> None:
        """Use a complete-note sequence to recover deleted measure boundaries."""
        clean = _make_score("C8|D8|", "G8|A8|")
        damaged = _make_score("C8D8", "G8|A8|")
        result = convert_abc(damaged, on_unrecoverable="smart")
        self.assertEqual(result.conversion_status, "SMART_RECOVERED")
        expected = ABCScoreParser("strict", 4).parse(clean)
        repaired = ABCScoreParser("strict", 4).parse(result.repaired_abc)
        for voice_name in ("vocal", "ins"):
            actual = [
                (event.onset, event.duration, event.pitch)
                for event in repaired.voices[voice_name].events
                if event.kind == "note"
            ]
            wanted = [
                (event.onset, event.duration, event.pitch)
                for event in expected.voices[voice_name].events
                if event.kind == "note"
            ]
            self.assertEqual(actual, wanted)

    def test_smart_recovers_a_short_tied_note_duration(self) -> None:
        """Increase only a tied note by the exact measure deficit and preserve the timeline."""
        header = "X:1\nM:7/8\nL:1/32\nQ:1/4=120\nK:C\n"
        clean = header + "V: Vocal\nC8D8E8F4-|F1z27|\nV: Ins\nZ|Z|\n"
        damaged = header + "V: Vocal\nC8D8E8F3-|F1z27|\nV: Ins\nZ|Z|\n"
        result = convert_abc(damaged, on_unrecoverable="smart")
        self.assertEqual(result.conversion_status, "SMART_RECOVERED")
        expected = ABCScoreParser("strict", 4).parse(clean)
        repaired = ABCScoreParser("strict", 4).parse(result.repaired_abc)
        actual_notes = [
            (event.onset, event.duration, event.pitch)
            for event in repaired.voices["vocal"].events
            if event.kind == "note"
        ]
        expected_notes = [
            (event.onset, event.duration, event.pitch)
            for event in expected.voices["vocal"].events
            if event.kind == "note"
        ]
        self.assertEqual(actual_notes, expected_notes)

    def test_six_bar_tied_outro_is_transferred_without_sounding_in_silence(self) -> None:
        """Keep the exact tied Vocal interval and both full silent measures."""
        source = (
            "X:1\nM:2/4\nL:1/32\nQ:1/4=120\nK:C\n"
            "% outro\nV: Vocal\na16-|a16-|a16-|a16|\n"
            "V: Ins\nZ4|\n"
            "% silence\nV: Vocal\nZ2|\nV: Ins\nZ2|\n"
        )
        parsed_source = ABCScoreParser("strict", 4).parse(source)
        result = convert_abc(source, on_unrecoverable="smart")
        converted = ABCScoreParser("strict", 4).parse(result.abc)

        source_vocal = [
            (event.onset, event.duration, event.pitch)
            for event in parsed_source.voices["vocal"].events
            if event.kind == "note"
        ]
        converted_ins = [
            (event.onset, event.duration, event.pitch)
            for event in converted.voices["ins"].events
            if event.kind == "note"
        ]
        self.assertEqual(source_vocal, [(Fraction(0), Fraction(2), 81)])
        self.assertEqual(converted_ins, source_vocal)
        self.assertEqual(len(converted.voices["ins"].measures), 6)
        self.assertEqual(result.bars, 6)
        self.assertEqual(result.score_seconds, 6.0)
        self.assertIn("% outro", result.abc)
        self.assertIn("% silence", result.abc)
        self.assertFalse(
            any(
                event.onset < 3 and event.onset + event.duration > 2
                for event in converted.voices["ins"].events
                if event.kind == "note"
            )
        )

    def test_smart_late_repair_preserves_six_bar_prefix_and_records_duration_change(self) -> None:
        """Keep a trusted six-bar prefix unchanged while documenting a later duration repair."""
        prefix = (
            "X:1\nM:2/4\nL:1/32\nQ:1/4=120\nK:C\n"
            "% outro\nV: Vocal\na16-|a16-|a16-|a16|\n"
            "V: Ins\nZ4|\n"
            "% silence\nV: Vocal\nZ2|\nV: Ins\nZ2|\n"
        )
        damaged = prefix + "V: Vocal\nd12-|d12|\nV: Ins\nZ2|\n"
        parsed_prefix = ABCScoreParser("strict", 4).parse(prefix)
        result = convert_abc(damaged, on_unrecoverable="smart")
        converted = ABCScoreParser("strict", 4).parse(result.abc)

        source_events = [
            (event.onset, event.duration, event.pitch)
            for event in parsed_prefix.voices["vocal"].events
            if event.kind == "note"
        ]
        prefix_output_events = [
            (event.onset, event.duration, event.pitch)
            for event in converted.voices["ins"].events
            if event.kind == "note" and event.onset < 3
        ]
        self.assertEqual(result.conversion_status, "SMART_RECOVERED")
        self.assertEqual(prefix_output_events, source_events)
        self.assertFalse(
            any(
                event.onset < 3 and event.onset + event.duration > 2
                for event in converted.voices["ins"].events
                if event.kind == "note"
            )
        )
        conversion = json.loads(result.warnings)["conversion"]
        changes = conversion["source_musical_event_changes"]["duration_changes"]
        self.assertEqual(len(changes), 1)
        self.assertEqual(changes[0]["original_duration"], "3/8")
        self.assertEqual(changes[0]["repaired_duration"], "1/2")
        self.assertTrue(changes[0]["reason"])
        self.assertIn("d12", changes[0]["source_span"]["text"])
        source_span = changes[0]["source_span"]
        self.assertEqual(
            damaged[source_span["start_offset"]:source_span["end_offset"]],
            source_span["text"],
        )

    def test_smart_trims_overlapping_instrumental_note_to_barline(self) -> None:
        """Repair an overhanging Ins duration while keeping both pitches and onsets."""
        source = _make_score("Z|Z|", "G10|A6|")
        result = convert_abc(source, on_unrecoverable="smart")
        self.assertEqual(result.conversion_status, "SMART_RECOVERED")
        repaired = ABCScoreParser("strict", 4).parse(result.repaired_abc)
        notes = [
            (event.onset, event.duration, event.pitch)
            for event in repaired.voices["ins"].events
            if event.kind == "note"
        ]
        self.assertEqual(notes, [(Fraction(0), Fraction(1), 67), (Fraction(1), Fraction(3, 4), 69)])

    def test_smart_closes_broken_chord_and_keeps_its_source_label(self) -> None:
        """Close an unterminated chord at the only boundary with a valid following note."""
        source = _make_score('"D#/A##z8|', "Z|")
        result = convert_abc(source, on_unrecoverable="smart")
        self.assertEqual(result.conversion_status, "SMART_RECOVERED")
        repaired = ABCScoreParser("strict", 4).parse(result.repaired_abc)
        chords = [
            event.chord
            for event in repaired.voices["vocal"].events
            if event.kind == "chord"
        ]
        self.assertEqual(chords, ['"D#/A##"'])

    def test_smart_combines_chord_and_adjacent_tied_measure_repairs(self) -> None:
        """Repair a broken chord and two adjacent tied deficits without losing notes."""
        header = "X:1\nM:7/8\nL:1/32\nQ:1/4=120\nK:C\n"
        clean = (
            header
            + 'V: Vocal\n"G"C8D8E8F4-|F1C8D8E8G3-|G1z27|\n'
            + "V: Ins\nZ|Z|Z|\n"
        )
        damaged = (
            header
            + 'V: Vocal\n"GC8D8E8F3-|F1C8D8E8G2-|G1z27|\n'
            + "V: Ins\nZ|Z|Z|\n"
        )
        result = convert_abc(damaged, on_unrecoverable="smart")
        self.assertEqual(result.conversion_status, "SMART_RECOVERED")
        expected = ABCScoreParser("strict", 4).parse(clean)
        repaired = ABCScoreParser("strict", 4).parse(result.repaired_abc)
        actual_notes = [
            (event.onset, event.duration, event.pitch)
            for event in repaired.voices["vocal"].events
            if event.kind == "note"
        ]
        expected_notes = [
            (event.onset, event.duration, event.pitch)
            for event in expected.voices["vocal"].events
            if event.kind == "note"
        ]
        self.assertEqual(actual_notes, expected_notes)
        hypotheses = json.loads(result.warnings)["conversion"]["selected_hypotheses"]
        self.assertEqual(len(hypotheses), 3)

    def test_smart_partial_converts_aligned_prefix_and_preserves_remaining_source(self) -> None:
        """Preserve the full ABC and keep a validated aligned prefix as a diagnostic artifact."""
        source = (
            "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\n"
            "V: Vocal\nC8|\nD4H4|\n"
            "V: Ins\nG8|A8|\n"
        )
        result = convert_abc(source, on_unrecoverable="smart")
        self.assertEqual(result.conversion_status, "PARTIAL_RAW")
        self.assertEqual(result.abc, source)
        self.assertEqual(result.bars, 0)
        self.assertTrue(result.repaired_abc)
        prefix = ABCScoreParser("strict", 4).parse(result.repaired_abc)
        self.assertEqual(len(prefix.voices["ins"].measures), 1)
        self.assertTrue(result.verbatim_spans)
        self.assertEqual(result.verbatim_spans[0]["text"], source)
        self.assertEqual(result.verbatim_spans[0]["start_offset"], 0)
        self.assertEqual(result.verbatim_spans[0]["end_offset"], len(source))
        conversion = json.loads(result.warnings)["conversion"]
        self.assertFalse(conversion["vocal_absence_confirmed"])
        self.assertEqual(conversion["source_conversion_status"], "SMART_PARTIAL")
        self.assertTrue(conversion["output_contains_full_source_music"])
        self.assertEqual(conversion["converted_measures"], 1)

    def test_smart_resynchronizes_after_damage_without_dropping_source_music(self) -> None:
        """Keep all source text and strictly convert later meter-synchronized segments."""
        vocal_prefix = "\n".join("C8|" for _ in range(16))
        ins_prefix = "\n".join("G8|" for _ in range(16))
        source = (
            "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\n"
            "% intro\nV: Vocal\n"
            f"{vocal_prefix}\n"
            "% interlude\ne''0'|\nM:9/8\nDEFGABcde|\n"
            "M:4/4\nFGABcdef|\n% outro\n"
            "V: Ins\n"
            f"{ins_prefix}\n"
            "M:9/8\nz9|\n"
            "M:4/4\nz8|\n% silence\n"
        )
        result = convert_abc(source, on_unrecoverable="smart")

        self.assertEqual(result.conversion_status, "PARTIAL_RAW")
        self.assertEqual(result.abc, source)
        self.assertIn("e''0'", result.abc)
        for section in ("% intro", "% interlude", "% outro", "% silence"):
            self.assertIn(section, result.abc)
        prefix = ABCScoreParser("strict", 4).parse(result.repaired_abc)
        self.assertEqual(len(prefix.voices["ins"].measures), 16)
        self.assertEqual(len(result.diagnostic_segments), 2, repr(result.candidate_log))
        self.assertEqual(
            {str(segment["meter"]) for segment in result.diagnostic_segments},
            {"9/8", "4/4"},
        )
        for segment in result.diagnostic_segments:
            converted = ABCScoreParser("strict", 4).parse(str(segment["abc"]))
            self.assertFalse(
                any(event.kind == "note" for event in converted.voices["vocal"].events)
            )
            self.assertTrue(
                any(event.kind == "note" for event in converted.voices["ins"].events)
            )

        conversion = json.loads(result.warnings)["conversion"]
        self.assertEqual(conversion["converted_measures"], 16 + sum(
            int(segment["converted_measures"]) for segment in result.diagnostic_segments
        ))
        self.assertEqual(conversion["resynchronized_segments"], len(result.diagnostic_segments))
        self.assertGreater(conversion["raw_preserved_measures"], 16)
        self.assertEqual(conversion["source_music_coverage"], 1.0)
        self.assertTrue(conversion["output_contains_full_source_music"])
        self.assertFalse(conversion["duration_known"])
        self.assertEqual(result.max_duration, 300.0)

    def test_continue_accepts_clean_score_and_preserves_ambiguous_score(self) -> None:
        """Convert a clean score but keep the full raw input after an unknown token."""
        clean = convert_abc(_make_score("C8|", "Z|"), on_unrecoverable="continue")
        self.assertEqual(clean.conversion_status, "OK")
        spelling_adjustment = convert_abc(
            _make_score("F4F4|", "^F4z4|"),
            on_unrecoverable="continue",
        )
        self.assertEqual(spelling_adjustment.conversion_status, "OK")
        self.assertIn(
            "source_spelling_accidental_adjusted",
            {item.code for item in spelling_adjustment.diagnostics},
        )
        uncertain_source = _make_score("C4-D4|", "Z|")
        uncertain = convert_abc(uncertain_source, on_unrecoverable="continue")
        self.assertEqual(uncertain.conversion_status, "PARTIAL_RAW")
        self.assertEqual(uncertain.abc, uncertain_source)
        self.assertEqual(uncertain.score_seconds, 2.0)
        self.assertEqual(uncertain.max_duration, 2.0)
        uncertain_conversion = json.loads(uncertain.warnings)["conversion"]
        self.assertEqual(uncertain_conversion["numeric_output_source"], "safe_parser_estimate")
        self.assertFalse(uncertain_conversion["unknown_numeric_outputs_use_zero_sentinel"])
        source = _make_score("C4H4|", "Z|")
        partial = convert_abc(source, on_unrecoverable="continue")
        self.assertEqual(partial.conversion_status, "PARTIAL_RAW")
        self.assertEqual(partial.abc, source)
        self.assertEqual(partial.max_duration, 300.0)
        self.assertEqual(partial.score_seconds, 0.0)
        partial_conversion = json.loads(partial.warnings)["conversion"]
        self.assertEqual(partial_conversion["numeric_output_source"], "default_300_seconds")
        self.assertTrue(partial_conversion["unknown_numeric_outputs_use_zero_sentinel"])
        self.assertFalse(partial_conversion["vocal_absence_confirmed"])

    def test_invalid_unrecoverable_policy_still_raises(self) -> None:
        """Reject invalid settings rather than returning a raw fallback for configuration errors."""
        with self.assertRaises(ScoreError):
            convert_abc(_make_score("C8|", "Z|"), on_unrecoverable="guess")

    def test_partial_parse_keeps_valid_events_before_unknown_token(self) -> None:
        """Expose lexed events and completed voices when a later token is unsupported."""
        parser = ABCScoreParser("extended", 4)
        with self.assertRaises(ScoreError):
            parser.parse(_make_score("C4H4|", "Z|"))
        partial_notes = [
            event.pitch
            for event in parser.partial_state.measure_events
            if event.kind == "note"
        ]
        self.assertEqual(partial_notes, [60])
        self.assertEqual(parser.partial_state.active_voice, "vocal")
        self.assertEqual(parser.partial_state.line_number, 7)

    def test_unmatched_quote_candidate_preserves_following_note_tokens(self) -> None:
        """Drop one orphan quote only when the following token is a clear lowercase note."""
        result = convert_abc(_make_score('"e\'8|', "Z|"))
        details = json.loads(result.warnings)
        self.assertTrue(
            any("orphan quote" in item["repair"].lower() for item in details["diagnostics"])
        )
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        ins_notes = [event for event in parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual([(event.pitch, event.duration) for event in ins_notes], [(88, Fraction(1, 1))])

    def test_missing_chord_opening_quote_is_repaired_without_event_changes(self) -> None:
        """Restore one unambiguous missing chord quote after a quoted note token."""
        score = _make_score('"A#""c4-"C#maj7"c4|', "Z|")
        result = convert_abc(score)
        self.assertIn('"C#maj7"', result.repaired_abc)
        self.assertIn("missing_chord_opening_quote", {item.code for item in result.diagnostics})
        repaired = ABCScoreParser("strict", 4).parse(result.repaired_abc)
        vocal_notes = [event for event in repaired.voices["vocal"].events if event.kind == "note"]
        chords = [event for event in repaired.voices["vocal"].events if event.kind == "chord"]
        self.assertEqual(
            [(event.onset, event.duration, event.pitch) for event in vocal_notes],
            [(Fraction(0), Fraction(1), 72)],
        )
        self.assertEqual([(event.onset, event.chord) for event in chords], [
            (Fraction(0), '"A#"'),
            (Fraction(1, 2), '"C#maj7"'),
        ])
        self.assertTrue(result.candidate_log)

    def test_node_debug_artifacts_are_saved_only_when_enabled(self) -> None:
        """Save unique source, repaired, converted, and diagnostic artifacts on request."""
        from yue2_auto_instrumental import VHSXYuE2AutoInstrumental

        with tempfile.TemporaryDirectory(prefix="yue2-node-debug-", dir=ROOT / "tmp") as temp_name:
            folder_paths = SimpleNamespace(get_output_directory=lambda: temp_name)
            with patch.dict(sys.modules, {"folder_paths": folder_paths}):
                outputs = VHSXYuE2AutoInstrumental().convert(
                    _make_score("C8|", "Z|"),
                    save_debug_artifacts=True,
                )
            self.assertIn("debug artifacts:", outputs[6])
            artifact_directories = list(Path(temp_name).iterdir())
            self.assertEqual(len(artifact_directories), 1)
            self.assertEqual(
                {path.name for path in artifact_directories[0].iterdir()},
                {
                    "source.abc",
                    "repaired.abc",
                    "converted.abc",
                    "repairs.json",
                    "validation.json",
                },
            )

    def test_partial_raw_debug_artifacts_are_not_labeled_as_converted(self) -> None:
        """Save raw fallback and spans without creating repaired or converted files."""
        from yue2_auto_instrumental import VHSXYuE2AutoInstrumental

        source = _make_score("C4H4|", "Z|")
        with tempfile.TemporaryDirectory(prefix="yue2-node-partial-", dir=ROOT / "tmp") as temp_name:
            folder_paths = SimpleNamespace(get_output_directory=lambda: temp_name)
            with patch.dict(sys.modules, {"folder_paths": folder_paths}):
                outputs = VHSXYuE2AutoInstrumental().convert(
                    source,
                    on_unrecoverable="smart",
                    save_debug_artifacts=True,
                )
            self.assertEqual(outputs[-1], "PARTIAL_RAW")
            artifact_directory = next(Path(temp_name).iterdir())
            self.assertEqual(
                {path.name for path in artifact_directory.iterdir()},
                {"source.abc", "partial_raw.abc", "repairs.json", "validation.json"},
            )
            self.assertEqual((artifact_directory / "partial_raw.abc").read_text(encoding="utf-8"), source)
            validation = json.loads((artifact_directory / "validation.json").read_text(encoding="utf-8"))
            repairs = json.loads((artifact_directory / "repairs.json").read_text(encoding="utf-8"))
            self.assertEqual(validation["status"], "PARTIAL_RAW")
            self.assertFalse(validation["vocal_absence_confirmed"])
            self.assertEqual(repairs["verbatim_spans"][0]["text"], source)

    def test_empty_chord_token_does_not_change_note_timeline(self) -> None:
        """Drop an empty chord annotation while preserving the following note event."""
        result = convert_abc(_make_score('""C8|', "Z|"))
        details = json.loads(result.warnings)
        self.assertTrue(
            any("empty chord label" in item["repair"].lower() for item in details["diagnostics"])
        )
        parsed = ABCScoreParser("strict", 4).parse(result.abc)
        ins_notes = [event for event in parsed.voices["ins"].events if event.kind == "note"]
        self.assertEqual([(event.pitch, event.duration) for event in ins_notes], [(60, Fraction(1, 1))])

    def test_real_corpus_regression_and_report(self) -> None:
        """Run SBL comparison and node invariants for every local ABC source."""
        counts = _run_corpus_report()
        self.assertGreater(counts.get("files", 0), 0)
        self.assertEqual(
            counts.get("new_success", 0) + counts.get("new_rejected", 0),
            counts.get("files", 0),
        )
        self.assertEqual(counts.get("new_unexpected", 0), 0)
        self.assertTrue(REPORT_PATH.is_file())


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    unittest.main(verbosity=2)
