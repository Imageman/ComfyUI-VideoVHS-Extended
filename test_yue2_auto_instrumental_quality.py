"""Audit musical preservation, upstream YuE syntax, and node contracts per ABC file."""

from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import logging
import math
import subprocess
import sys
import tempfile
import traceback
import unittest
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from yue2_auto_instrumental import (
    ABCScoreParser,
    ParsedScore,
    ScoreError,
    _score_seconds,
    convert_abc,
)


ROOT = Path(__file__).resolve().parent
CORPUS_DIR = ROOT / "tmp" / "raw_abc"
UPSTREAM_CHECKER = ROOT / "tmp" / "upstream_abc_tools.py"
DEFAULT_BASELINE_REPORT = ROOT / "tmp" / "yue2_auto_instrumental_corpus_report_baseline_158.md"
DEFAULT_REPORT_PATH = ROOT / "doc" / "yue2_auto_instrumental_quality_report.md"
BASELINE_REPORT = DEFAULT_BASELINE_REPORT
REPORT_PATH = DEFAULT_REPORT_PATH
LOGGER = logging.getLogger(__name__)
EXACT_REPAIR_PREFIXES = (
    "Use V:",
    "Drop the empty chord label",
    "Drop the orphan quote as a token-level repair",
    "Remove the quote delimiters at token level",
    "Trim only trailing rest duration",
    "Clip excess trailing rest duration",
    "Treat the remaining content as a complete measure",
    "Insert a missing final bar marker",
    "Insert a missing opening quote around the chord symbol",
    "Keep the valid metadata prefix and preserve the trailing text as a comment",
    "Split the valid metadata prefix and parse the remaining music tokens",
)
HEURISTIC_REPAIR_WORDS = (
    "heuristically",
    "append a rest",
    "keep the previous",
    "use ins as the output grid",
    "repeat barline",
    "tie does not continue",
    "malformed",
    "insert a barline",
)


@dataclass
class FileQuality:
    """Hold independent parsing, conversion, and preservation results for one ABC file."""

    file_name: str
    strict_input: bool = False
    parsed: bool = False
    repaired_exact: bool = False
    repaired_heuristic: bool = False
    converted: bool = False
    validated_internal: bool = False
    validated_upstream: bool = False
    source_validated_upstream: bool = False
    ins_preserved_exactly: str = "unknown"
    ins_spelling_preserved_exactly: str = "unknown"
    ins_vs_recovered_parse: str = "unknown"
    chord_positions_exactly: str = "unknown"
    duration_drift_seconds: str = "unknown"
    musical_semantics_uncertain: bool = True
    unsupported_chord_quality: bool = False
    upstream_compare_exact: str = "unknown"
    baseline_converted: str = "unknown"
    diagnostic: str = ""


def _load_upstream_checker() -> ModuleType:
    """Load the official YuE checker snapshot without importing optional packages."""
    spec = importlib.util.spec_from_file_location("upstream_abc_tools", UPSTREAM_CHECKER)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load YuE ABC checker at {UPSTREAM_CHECKER}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _note_signature(score: ParsedScore, voice_name: str) -> tuple[tuple[object, ...], ...]:
    """Return sounding-note onset, duration, pitch, and tied continuation data."""
    events = [
        event
        for event in score.voices[voice_name].events
        if event.kind == "note"
    ]
    events.sort(key=lambda event: (event.onset, event.source_order))
    return tuple(
        (event.onset, event.duration, event.pitch, event.tied_after)
        for event in events
    )


def _note_spelling_signature(score: ParsedScore, voice_name: str) -> tuple[tuple[object, ...], ...]:
    """Return sounding notes with their spelling and explicit accidental token."""
    events = [
        event
        for event in score.voices[voice_name].events
        if event.kind == "note"
    ]
    events.sort(key=lambda event: (event.onset, event.source_order))
    return tuple(
        (
            event.onset,
            event.duration,
            event.pitch,
            event.tied_after,
            event.accidental or "",
            event.spelling,
        )
        for event in events
    )


def _grid_signature(score: ParsedScore, voice_name: str) -> tuple[tuple[object, ...], ...]:
    """Return absolute measure boundaries and active metadata for one voice."""
    return tuple(
        (measure.onset, measure.duration, measure.meter, measure.key)
        for measure in score.voices[voice_name].measures
    )


def _chord_signature(score: ParsedScore) -> tuple[tuple[object, str], ...]:
    """Return all chord labels with their absolute source positions."""
    chords = [
        (event.onset, event.chord)
        for voice in score.voices.values()
        for event in voice.events
        if event.kind == "chord"
    ]
    return tuple(sorted(chords, key=lambda item: (item[0], item[1])))


def _has_exact_repairs(score: ParsedScore) -> bool:
    """Classify known event-preserving parser repairs conservatively."""
    return any(
        diagnostic.repair.startswith(EXACT_REPAIR_PREFIXES)
        for diagnostic in score.diagnostics
    )


def _has_heuristic_repairs(score: ParsedScore) -> bool:
    """Detect repairs that may infer or alter musical timing or articulation."""
    return any(
        diagnostic.code != "unsupported_chord_quality"
        and not diagnostic.repair.startswith("Preserve the opaque chord label unchanged")
        and (
            any(
                word in diagnostic.problem.lower() or word in diagnostic.repair.lower()
                for word in HEURISTIC_REPAIR_WORDS
            )
            or not diagnostic.repair.startswith(EXACT_REPAIR_PREFIXES)
        )
        for diagnostic in score.diagnostics
    )


def _load_baseline_status(path: Path) -> dict[str, bool]:
    """Read prior per-file conversion results from the preserved corpus report."""
    if not path.is_file():
        LOGGER.warning(f"Baseline report is unavailable at {path}.")
        return {}
    in_status_table = False
    statuses: dict[str, bool] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if line == "## Per-file conversion status":
                in_status_table = True
                continue
            if in_status_table and line.startswith("## "):
                break
            if not in_status_table or not line.startswith("| YuE2_ABC_"):
                continue
            columns = [item.strip() for item in line.strip("|").split("|")]
            if len(columns) >= 3:
                statuses[Path(columns[0]).name] = columns[2] == "ok"
    except Exception as exc:
        LOGGER.error(f"Unable to read baseline report {path}: {exc}\n{traceback.format_exc()}")
        raise
    return statuses


def _run_upstream_cli(arguments: list[str]) -> tuple[int, dict[str, object], str]:
    """Run one official checker command and decode its JSON result."""
    command = [sys.executable, str(UPSTREAM_CHECKER), *arguments]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            encoding="utf-8",
            text=True,
        )
    except Exception as exc:
        LOGGER.error(f"Unable to run upstream checker {command}: {exc}\n{traceback.format_exc()}")
        raise
    try:
        payload = json.loads(result.stdout) if result.stdout.strip().startswith("{") else {}
    except json.JSONDecodeError:
        payload = {}
    return result.returncode, payload, result.stderr.strip()


def _classify_file(
    path: Path,
    temp_dir: Path,
    upstream: ModuleType,
    baseline_status: dict[str, bool],
) -> FileQuality:
    """Evaluate one source and its converted output with internal and upstream parsers."""
    record = FileQuality(path.name)
    record.baseline_converted = (
        "yes" if baseline_status.get(path.name) is True
        else "no" if baseline_status.get(path.name) is False
        else "unknown"
    )
    try:
        abc = path.read_text(encoding="utf-8-sig")
    except Exception as exc:
        LOGGER.error(f"Unable to read source file {path}: {exc}\n{traceback.format_exc()}")
        raise

    source_score: ParsedScore | None = None
    strict_parser = ABCScoreParser("strict", 4)
    try:
        source_score = strict_parser.parse(abc)
        record.strict_input = True
        record.parsed = True
    except ScoreError as exc:
        record.diagnostic = str(exc)

    extended_parser = ABCScoreParser("extended", 4)
    try:
        extended_score = extended_parser.parse(abc)
        record.parsed = True
        if source_score is None:
            source_score = extended_score
        record.repaired_exact = _has_exact_repairs(extended_score)
        record.repaired_heuristic = _has_heuristic_repairs(extended_score)
        record.unsupported_chord_quality = any(
            item.code == "unsupported_chord_quality" for item in extended_score.diagnostics
        )
    except ScoreError as exc:
        extended_score = None
        record.unsupported_chord_quality = any(
            item.code == "unsupported_chord_quality" for item in extended_parser.diagnostics
        )
        if not record.diagnostic:
            record.diagnostic = str(exc)

    try:
        upstream_source = upstream.parse_abc(abc)
        record.source_validated_upstream = True
    except upstream.AbcError:
        upstream_source = None

    try:
        converted = convert_abc(abc)
        record.converted = True
    except ScoreError as exc:
        record.diagnostic = str(exc)
        return record
    except Exception as exc:
        record.diagnostic = f"unexpected converter error: {exc}"
        LOGGER.error(f"Unexpected converter failure for {path}: {exc}\n{traceback.format_exc()}")
        return record

    output_path = temp_dir / path.name
    try:
        output_path.write_text(converted.abc, encoding="utf-8", newline="")
    except Exception as exc:
        LOGGER.error(f"Unable to write temporary converted file {output_path}: {exc}\n{traceback.format_exc()}")
        raise

    output_score: ParsedScore | None = None
    try:
        output_score = ABCScoreParser("strict", 4).parse(converted.abc)
        record.validated_internal = True
    except ScoreError as exc:
        record.diagnostic = f"internal output validation failed: {exc}"

    upstream_output = None
    try:
        upstream_output = upstream.parse_abc(converted.abc)
        inspect_code, _, inspect_error = _run_upstream_cli(["inspect", str(output_path)])
        record.validated_upstream = inspect_code == 0
        if inspect_code != 0:
            record.diagnostic = f"upstream inspect failed: {inspect_error or inspect_code}"
    except upstream.AbcError as exc:
        record.diagnostic = f"upstream output validation failed: {exc}"

    if source_score is not None and output_score is not None:
        source_ins = Counter(_note_signature(source_score, "ins"))
        output_ins = Counter(_note_signature(output_score, "ins"))
        source_spelling = Counter(_note_spelling_signature(source_score, "ins"))
        output_spelling = Counter(_note_spelling_signature(output_score, "ins"))
        recovered_ins_preserved = not source_ins - output_ins
        record.ins_vs_recovered_parse = "yes" if recovered_ins_preserved else "no"
        source_unambiguous = record.strict_input or not record.repaired_heuristic
        if source_unambiguous:
            record.ins_spelling_preserved_exactly = (
                "yes" if not source_spelling - output_spelling else "no"
            )
            ins_equal = (
                recovered_ins_preserved
                and _grid_signature(source_score, "ins") == _grid_signature(output_score, "ins")
                and _score_seconds(source_score) == _score_seconds(output_score)
            )
            record.ins_preserved_exactly = "yes" if ins_equal else "no"
            drift = _score_seconds(output_score) - _score_seconds(source_score)
            record.duration_drift_seconds = f"{drift:.9f}"
        if record.strict_input:
            chord_equal = _chord_signature(source_score) == _chord_signature(output_score)
            record.chord_positions_exactly = "yes" if chord_equal else "no"
        if record.repaired_heuristic and not record.strict_input:
            record.musical_semantics_uncertain = True
        elif record.ins_preserved_exactly == "no" or record.chord_positions_exactly == "no":
            record.musical_semantics_uncertain = True
        elif record.unsupported_chord_quality:
            record.musical_semantics_uncertain = True
        else:
            record.musical_semantics_uncertain = False

    if upstream_source is not None and upstream_output is not None:
        compare_code, compare_payload, compare_error = _run_upstream_cli(
            ["compare", str(path), str(output_path), "--voices", "Ins"]
        )
        compare_match = compare_payload.get("match") is True
        record.upstream_compare_exact = "yes" if compare_match else "no"
        if compare_code not in (0, 1):
            record.diagnostic = f"upstream compare failed: {compare_error or compare_code}"
        if compare_match is True and record.ins_preserved_exactly == "unknown":
            record.ins_preserved_exactly = "yes"
            record.musical_semantics_uncertain = record.unsupported_chord_quality
    if record.baseline_converted == "yes" and not record.converted:
        record.diagnostic = f"Previously accepted, now rejected: {record.diagnostic}"
    return record


def _format_bool(value: bool) -> str:
    """Render a status flag as a stable Markdown token."""
    return "yes" if value else "no"


def _write_quality_report(records: list[FileQuality], report_path: Path) -> None:
    """Write per-file validation statuses, exact comparisons, and regressions."""
    counts: dict[str, int] = {
        "files": len(records),
        "strict_input": sum(record.strict_input for record in records),
        "parsed": sum(record.parsed for record in records),
        "repaired_exact": sum(record.repaired_exact for record in records),
        "repaired_heuristic": sum(record.repaired_heuristic for record in records),
        "converted": sum(record.converted for record in records),
        "validated_internal": sum(record.validated_internal for record in records),
        "validated_upstream": sum(record.validated_upstream for record in records),
        "source_validated_upstream": sum(record.source_validated_upstream for record in records),
        "unsupported_chord_quality": sum(record.unsupported_chord_quality for record in records),
        "ins_preserved_exactly": sum(record.ins_preserved_exactly == "yes" for record in records),
        "ins_spelling_preserved_exactly": sum(
            record.ins_spelling_preserved_exactly == "yes" for record in records
        ),
        "ins_spelling_unknown": sum(record.ins_spelling_preserved_exactly == "unknown" for record in records),
        "ins_preservation_unknown": sum(record.ins_preserved_exactly == "unknown" for record in records),
        "chord_positions_exactly": sum(record.chord_positions_exactly == "yes" for record in records),
        "chord_positions_unknown": sum(record.chord_positions_exactly == "unknown" for record in records),
        "musical_semantics_uncertain": sum(record.musical_semantics_uncertain for record in records),
        "previously_accepted_now_rejected": sum(
            record.baseline_converted == "yes" and not record.converted for record in records
        ),
        "previously_rejected_now_accepted": sum(
            record.baseline_converted == "no" and record.converted for record in records
        ),
    }
    summary_rows = [f"| {name} | {value} |" for name, value in counts.items()]
    regression_rows = [
        _wrap_bullet(f"- `{record.file_name}`: {record.diagnostic}")
        for record in records
        if record.baseline_converted == "yes" and not record.converted
    ]
    uncertain_rows = [
        _wrap_bullet(
            f"- `{record.file_name}`: "
            f"{record.diagnostic or 'recovery semantics are not independently established.'}"
        )
        for record in records
        if record.musical_semantics_uncertain and record.converted
    ]
    status_rows = [
        _wrap_bullet(
            f"- `{record.file_name}`: parsed={_format_bool(record.parsed)}; "
            f"repaired_exact={_format_bool(record.repaired_exact)}; "
            f"repaired_heuristic={_format_bool(record.repaired_heuristic)}; "
            f"converted={_format_bool(record.converted)}; "
            f"validated_internal={_format_bool(record.validated_internal)}; "
            f"validated_upstream={_format_bool(record.validated_upstream)}; "
            f"ins_vs_recovered_parse={record.ins_vs_recovered_parse}; "
            f"ins_preserved_exactly={record.ins_preserved_exactly}; "
            f"ins_spelling_preserved_exactly={record.ins_spelling_preserved_exactly}; "
            f"chord_positions_exactly={record.chord_positions_exactly}; "
            f"duration_drift_seconds={record.duration_drift_seconds}; "
            f"musical_semantics_uncertain={_format_bool(record.musical_semantics_uncertain)}; "
            f"baseline={record.baseline_converted}; "
            f"upstream_compare_exact={record.upstream_compare_exact}; "
            f"unsupported_chord_quality={_format_bool(record.unsupported_chord_quality)}"
        )
        for record in records
    ]
    report = [
        "# YuE2 Auto Instrumental music quality audit",
        "",
        _wrap(
            f"Corpus: {counts['files']} current files in `tmp/raw_abc`. Baseline results were captured "
            "before algorithm changes. Model execution was not available and was not tested."
        ),
        "",
        "## Summary",
        "",
        "| Status | Count |",
        "|---|---:|",
        *summary_rows,
        "",
        _wrap(
            "Independent source checks count only inputs accepted by the YuE checker. Upstream output validation "
            "means its Python parser and `inspect` command both accepted the converted ABC."
        ),
        "",
        _wrap(
            "`Ins preserved exactly` checks that each recovered source note, including ties, remains in output. "
            "Vocal notes transferred into uncovered Ins time are allowed. `unknown` means damaged input did not "
            "establish a unique source timeline."
        ),
        "",
        _wrap(
            "`chord_positions_exactly` compares chord labels and onset times for strict-valid source scores."
        ),
        "",
        _wrap(
            "`Ins spelling preserved exactly` additionally compares each source note's letter case, octave marks, "
            "and explicit accidental against its reparsed output event. It is unknown when source recovery itself "
            "is heuristic."
        ),
        "",
        _wrap(
            "The official checker verifies its limited ABC dialect and symbolic note/meter events. It does not "
            "verify generated audio or prove that the YuE2 model accepts a score."
        ),
        "",
        "## Previously accepted, now rejected",
        "",
        *(regression_rows or ["None." ]),
        "",
        "## Accepted files with uncertain musical semantics",
        "",
        *(uncertain_rows or ["None." ]),
        "",
        "## Per-file status",
        "",
        "Every requested per-file status and comparison is listed below.",
        *status_rows,
        "",
        "## Comparison details",
        "",
        _wrap(
            "Per-file note onset, duration, pitch, and tie-resolved articulation are compared from exact "
            "rational timelines. Chord positions and score-duration drift are included when source parsing is exact. "
            "The upstream compare status reports full-track equality, which can differ after Vocal transfer. "
            "An `unknown` field means the source did not establish an exact comparison baseline."
        ),
        "",
    ]
    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text("\n".join(report), encoding="utf-8")
    except Exception as exc:
        LOGGER.error(f"Unable to write quality report {report_path}: {exc}\n{traceback.format_exc()}")
        raise


def _wrap(text: str) -> str:
    """Wrap one Markdown paragraph within the repository's line-width limit."""
    import textwrap

    return textwrap.fill(text, width=110)


def _wrap_bullet(text: str) -> str:
    """Wrap one detailed Markdown list item within the repository line-width limit."""
    import textwrap

    return textwrap.fill(text, width=110, subsequent_indent="  ")


class YuE2QualityTests(unittest.TestCase):
    """Check static node contracts and per-file musical validation statuses."""

    def test_core_converter_has_only_standard_library_imports(self) -> None:
        """Ensure importing the conversion core does not require ComfyUI or model packages."""
        source_path = ROOT / "yue2_auto_instrumental.py"
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertTrue(imported.issubset(sys.stdlib_module_names), sorted(imported))

    def test_node_registration_and_output_contract(self) -> None:
        """Verify unique registration, schema names, and values returned by ordinary Python calls."""
        init_path = ROOT / "__init__.py"
        tree = ast.parse(init_path.read_text(encoding="utf-8"), filename=str(init_path))
        mappings: dict[str, ast.Dict] = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in {
                        "NODE_CLASS_MAPPINGS",
                        "NODE_DISPLAY_NAME_MAPPINGS",
                    } and isinstance(node.value, ast.Dict):
                        mappings[target.id] = node.value
        self.assertEqual(set(mappings), {"NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"})
        class_keys = [ast.literal_eval(key) for key in mappings["NODE_CLASS_MAPPINGS"].keys]
        display_keys = [ast.literal_eval(key) for key in mappings["NODE_DISPLAY_NAME_MAPPINGS"].keys]
        self.assertEqual(len(class_keys), len(set(class_keys)))
        self.assertEqual(len(display_keys), len(set(display_keys)))
        self.assertIn("VHSX_YuE2AutoInstrumental", class_keys)

        from yue2_auto_instrumental import VHSXYuE2AutoInstrumental

        schema = VHSXYuE2AutoInstrumental.INPUT_TYPES()
        self.assertIn("abc", schema["required"])
        self.assertEqual(schema["required"]["on_unrecoverable"][0], ["error", "smart", "continue"])
        self.assertFalse(schema["optional"]["save_debug_artifacts"][1]["default"])
        self.assertEqual(len(VHSXYuE2AutoInstrumental.RETURN_TYPES), 10)
        self.assertEqual(len(VHSXYuE2AutoInstrumental.RETURN_TYPES), len(VHSXYuE2AutoInstrumental.RETURN_NAMES))
        self.assertEqual(len(set(VHSXYuE2AutoInstrumental.RETURN_NAMES)), 10)
        self.assertEqual(VHSXYuE2AutoInstrumental.RETURN_NAMES[-1], "conversion_status")
        values = VHSXYuE2AutoInstrumental().convert("")
        expected_types = (str, str, float, float, int, float, str, str, int, str)
        self.assertEqual(tuple(type(value) for value in values), expected_types)

    def test_all_corpus_files_and_write_report(self) -> None:
        """Audit source/output events with both parsers and record each required status."""
        self.assertTrue(UPSTREAM_CHECKER.is_file(), f"Official checker is missing: {UPSTREAM_CHECKER}")
        self.assertTrue(CORPUS_DIR.is_dir(), f"ABC corpus directory is missing: {CORPUS_DIR}")
        files = sorted(CORPUS_DIR.glob("YuE2_ABC_*.txt"))
        self.assertGreater(len(files), 0, "The current raw ABC corpus must not be empty.")
        upstream = _load_upstream_checker()
        baseline_status = _load_baseline_status(BASELINE_REPORT)
        with tempfile.TemporaryDirectory(prefix="yue2-quality-", dir=ROOT / "tmp") as temp_name:
            records = [
                _classify_file(path, Path(temp_name), upstream, baseline_status)
                for path in files
            ]
        self.assertEqual(len({record.file_name for record in records}), len(files))
        _write_quality_report(records, REPORT_PATH)
        LOGGER.info(
            f"Quality audit: converted={sum(record.converted for record in records)}/{len(records)}, "
            f"upstream={sum(record.validated_upstream for record in records)}, "
            f"Ins exact={sum(record.ins_preserved_exactly == 'yes' for record in records)}, "
            f"Ins spelling exact={sum(record.ins_spelling_preserved_exactly == 'yes' for record in records)}, "
            f"uncertain={sum(record.musical_semantics_uncertain for record in records)}."
        )


def main() -> None:
    """Run static and corpus-quality checks with optional baseline/report paths."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_REPORT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    arguments = parser.parse_args()
    global BASELINE_REPORT, REPORT_PATH
    BASELINE_REPORT = arguments.baseline
    REPORT_PATH = arguments.report
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(YuE2QualityTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)


if __name__ == "__main__":
    main()
