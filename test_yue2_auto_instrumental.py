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

from yue2_auto_instrumental import ABCScoreParser, ScoreError, convert_abc


LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
CORPUS_DIR = ROOT / "tmp" / "raw_abc"
SBL_PATH = ROOT / "reference" / "SBL" / "YuE2Instrumental.py"
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
        f"Corpus: `tmp/raw_abc` ({counts['files']} UTF-8 files).",
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
            "This checks converter invariants, not reconstruction accuracy for damaged source ABC. "
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
        self.assertIn("=C24-=C4z4|", result.abc)
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
        self.assertEqual(result.bars, 0)
        self.assertFalse(conversion["vocal_absence_confirmed"])
        self.assertEqual(conversion["internal_validator"], "not_passed")
        self.assertEqual(result.verbatim_spans[0]["text"], source)
        self.assertEqual(result.verbatim_spans[0]["start_offset"], 0)
        self.assertEqual(result.verbatim_spans[0]["end_offset"], len(source))

    def test_continue_accepts_clean_score_and_preserves_ambiguous_score(self) -> None:
        """Convert a clean score but keep the full raw input after an unknown token."""
        clean = convert_abc(_make_score("C8|", "Z|"), on_unrecoverable="continue")
        self.assertEqual(clean.conversion_status, "OK")
        source = _make_score("C4H4|", "Z|")
        partial = convert_abc(source, on_unrecoverable="continue")
        self.assertEqual(partial.conversion_status, "PARTIAL_RAW")
        self.assertEqual(partial.abc, source)
        self.assertFalse(json.loads(partial.warnings)["conversion"]["vocal_absence_confirmed"])

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
