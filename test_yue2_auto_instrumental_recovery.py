"""Evaluate YuE2 recovery on synthetic damage and a deterministic corpus split."""

from __future__ import annotations

import argparse
import logging
import textwrap
import traceback
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from yue2_test_support import dependency, verify_manifest

from yue2_auto_instrumental import ABCScoreParser, ScoreError, convert_abc


LOGGER = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parent
CORPUS_DIR = ROOT / "tests" / "raw_abc"
REPORT_PATH = ROOT / "doc" / "yue2_auto_instrumental_recovery_report.md"
DEVELOPMENT_FILE_ID_MAX = 96
CorpusSplit = Literal["development", "holdout", "all"]


def _wrap_report_text(text: str) -> str:
    """Wrap report prose to fit the repository's Markdown line-width limit."""
    return textwrap.fill(text, width=110)


@dataclass(frozen=True)
class SyntheticCase:
    """Describe a known-source damage case for exact recovery checks."""

    name: str
    split: Literal["development", "holdout"]
    clean: str | None
    damaged: str
    expect_rejection: bool = False


def _make_score(vocal: str, ins: str, ins_voice: str = "Ins") -> str:
    """Build a compact two-voice ABC score for a recovery experiment."""
    return (
        "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\n"
        f"V: Vocal\n{vocal}\nV: {ins_voice}\n{ins}\n"
    )


def _synthetic_cases() -> list[SyntheticCase]:
    """Return event-equivalent synthetic damages and one expected safe rejection."""
    return [
        SyntheticCase(
            "deleted_barline_with_tied_note",
            "development",
            _make_score("C8-|C2D6|", "G8|A8|"),
            _make_score("C8-C2|D6|", "G8|A8|"),
        ),
        SyntheticCase(
            "repeat_barline_variant",
            "development",
            _make_score("C8|D8|", "G8|A8|"),
            _make_score("C8::D8||", "G8|A8|"),
        ),
        SyntheticCase(
            "equivalent_dotted_duration",
            "development",
            _make_score("C3/2z13/2|", "G8|"),
            _make_score("C1.z13/2|", "G8|"),
        ),
        SyntheticCase(
            "chord_on_full_measure_rest",
            "development",
            _make_score('"G"z8|', "Z|"),
            _make_score('"G"Z|', "Z|"),
        ),
        SyntheticCase(
            "missing_boundary_before_full_bar_rest",
            "development",
            _make_score('"F#"z8|"G#"Z|', "Z2|"),
            _make_score('"F#"z8"G#"Z|', "Z2|"),
        ),
        SyntheticCase(
            "empty_chord_token",
            "development",
            _make_score("C8|", "Z|"),
            _make_score('""C8|', "Z|"),
        ),
        SyntheticCase(
            "voice_identifier_alias",
            "holdout",
            _make_score("Z|", "G8|A8|"),
            _make_score("Z|", "G8|A8|", "instrumental"),
        ),
        SyntheticCase(
            "orphan_quote_before_note",
            "holdout",
            _make_score("e'8|", "Z|"),
            _make_score('"e\'8|', "Z|"),
        ),
        SyntheticCase(
            "invalid_tie_marker",
            "holdout",
            _make_score("C4D4|", "Z|"),
            _make_score("C4-D4|", "Z|"),
        ),
        SyntheticCase(
            "compressed_measure_rest",
            "holdout",
            _make_score("Z|Z|", "G8|A8|"),
            _make_score("Z2|", "G8|A8|"),
        ),
        SyntheticCase(
            "overlapping_ins_notes_must_reject",
            "holdout",
            None,
            _make_score("Z|Z|", "G10|A6|"),
            expect_rejection=True,
        ),
    ]


def _music_signature(
    abc: str,
    voice: str,
) -> tuple[list[tuple[object, object, int]], list[tuple[object, str]]]:
    """Return exact note timelines and chord positions for one parsed voice."""
    score = ABCScoreParser("extended", 4).parse(abc)
    notes = [
        (event.onset, event.duration, event.pitch or 0)
        for event in score.voices[voice].events
        if event.kind == "note"
    ]
    chords = [
        (event.onset, event.chord)
        for event in score.voices[voice].events
        if event.kind == "chord"
    ]
    return notes, chords


def _synthetic_report(split: CorpusSplit) -> tuple[list[str], dict[str, int]]:
    """Evaluate event accuracy and safe rejection on selected synthetic cases."""
    counts: Counter[str] = Counter()
    rows: list[str] = []
    cases = [case for case in _synthetic_cases() if split == "all" or case.split == split]
    for case in cases:
        counts[f"{case.split}_cases"] += 1
        if case.expect_rejection:
            try:
                convert_abc(case.damaged)
            except ScoreError:
                counts["expected_rejections"] += 1
                counts[f"{case.split}_expected_rejections"] += 1
                rows.append(f"| {case.split} | {case.name} | safe rejection | n/a |")
            except Exception as exc:
                counts["unexpected_failures"] += 1
                rows.append(f"| {case.split} | {case.name} | unexpected error: {exc} | n/a |")
            else:
                counts["false_successes"] += 1
                counts[f"{case.split}_false_successes"] += 1
                rows.append(f"| {case.split} | {case.name} | false success | n/a |")
            continue

        try:
            clean_signatures = {
                voice: _music_signature(case.clean or "", voice)
                for voice in ("vocal", "ins")
            }
            recovered_signatures = {
                voice: _music_signature(case.damaged, voice)
                for voice in ("vocal", "ins")
            }
            exact = recovered_signatures == clean_signatures
            result = convert_abc(case.damaged)
            output = ABCScoreParser("strict", 4).parse(result.abc)
            vocal_empty = not any(
                event.kind == "note" for event in output.voices["vocal"].events
            )
            counts["exact_cases"] += int(exact)
            counts["recovery_cases"] += 1
            counts[f"{case.split}_recovery_cases"] += 1
            counts[f"{case.split}_exact_cases"] += int(exact)
            counts["structurally_valid"] += 1
            counts["vocal_removed"] += int(vocal_empty)
            status = "exact" if exact else "event mismatch"
            rows.append(
                f"| {case.split} | {case.name} | {status} | "
                f"{'valid, Vocal silent' if vocal_empty else 'output invariant failed'} |"
            )
        except ScoreError as exc:
            counts["recovery_failures"] += 1
            rows.append(f"| {case.split} | {case.name} | recovery rejected | {exc} |")
        except Exception as exc:
            counts["unexpected_failures"] += 1
            LOGGER.error(
                f"Unexpected synthetic case failure for {case.name}: {exc}\n{traceback.format_exc()}"
            )
            rows.append(f"| {case.split} | {case.name} | unexpected error | {exc} |")
    return rows, dict(counts)


def _corpus_split(path: Path) -> Literal["development", "holdout"]:
    """Use the original 96 files for development and newly added IDs as holdout."""
    try:
        file_id = int(path.stem.rsplit("_", 1)[1])
    except (IndexError, ValueError) as exc:
        raise ValueError(f"Unexpected corpus filename {path.name!r}.") from exc
    return "development" if file_id <= DEVELOPMENT_FILE_ID_MAX else "holdout"


def _failure_category(message: str) -> str:
    """Group a diagnostic failure by the recovery stage that rejected it."""
    normalized = message.lower()
    if "chord" in normalized:
        return "chord syntax"
    if "timeline" in normalized or "grid" in normalized or "voice" in normalized:
        return "voice alignment"
    if "overlap" in normalized or "overrun" in normalized:
        return "event overlap"
    if "meter" in normalized or "measure" in normalized or "duration" in normalized:
        return "timing structure"
    if "key" in normalized or "tempo" in normalized:
        return "metadata"
    if "notation" in normalized or "token" in normalized:
        return "unsupported token"
    return "other validation"


def _corpus_report(
    split: CorpusSplit,
) -> tuple[list[str], dict[str, Counter[str]], dict[str, list[str]]]:
    """Measure parser recovery, conversion invariants, and Ins preservation by split."""
    verify_manifest()
    if not CORPUS_DIR.is_dir():
        raise FileNotFoundError(f"ABC corpus directory does not exist: {CORPUS_DIR}")
    files = sorted(path for path in CORPUS_DIR.glob("YuE2_ABC_*.txt") if path.is_file())
    if not files:
        raise FileNotFoundError(f"No ABC source files were found in {CORPUS_DIR}.")
    counts = {name: Counter() for name in ("development", "holdout")}
    failure_examples: dict[str, list[str]] = {name: [] for name in counts}
    selected = [path for path in files if split == "all" or _corpus_split(path) == split]
    for path in selected:
        group = _corpus_split(path)
        current = counts[group]
        current["files"] += 1
        try:
            abc = path.read_text(encoding="utf-8-sig")
        except Exception as exc:
            LOGGER.error(f"Unable to read corpus file {path}: {exc}\n{traceback.format_exc()}")
            raise
        original_score = None
        try:
            original_score = ABCScoreParser("strict", 4).parse(abc)
            current["strict_valid"] += 1
        except ScoreError:
            current["strict_invalid"] += 1
        try:
            ABCScoreParser("extended", 4).parse(abc)
            current["syntax_recovered"] += 1
        except ScoreError:
            pass
        try:
            result = convert_abc(abc)
            converted = ABCScoreParser("strict", 4).parse(result.abc)
            current["conversion_success"] += 1
            current["output_structurally_valid"] += 1
            if not any(event.kind == "note" for event in converted.voices["vocal"].events):
                current["vocal_removed"] += 1
            transfer_repairs = [item for item in result.diagnostics if item.severity == "warning"]
            current["repaired"] += int(bool(transfer_repairs))
            current["heuristic_repairs"] += sum(
                "heuristically" in item.repair.lower() for item in transfer_repairs
            )
            if original_score is not None:
                original_ins = Counter(
                    (event.onset, event.duration, event.pitch or 0)
                    for event in original_score.voices["ins"].events
                    if event.kind == "note"
                )
                converted_ins = Counter(
                    (event.onset, event.duration, event.pitch or 0)
                    for event in converted.voices["ins"].events
                    if event.kind == "note"
                )
                current["ins_checked"] += 1
                current["ins_preserved"] += int(
                    all(converted_ins[event] >= amount for event, amount in original_ins.items())
                )
        except ScoreError as exc:
            current["conversion_rejected"] += 1
            category = _failure_category(str(exc))
            current[f"failure:{category}"] += 1
            if len(failure_examples[group]) < 12:
                failure_examples[group].append(f"- `{path.name}` ({category}): {exc}")
        except Exception as exc:
            current["unexpected_failures"] += 1
            LOGGER.error(
                f"Unexpected recovery evaluation failure for {path}: {exc}\n{traceback.format_exc()}"
            )
    rows = []
    for group in ("development", "holdout"):
        if split != "all" and group != split:
            continue
        metric = counts[group]
        if not metric["files"]:
            continue
        rows.append(
            f"| {group} | {metric['files']} | {metric['syntax_recovered']} | "
            f"{metric['conversion_success']} | {metric['repaired']} | {metric['heuristic_repairs']} | "
            f"{metric['ins_preserved']}/{metric['ins_checked']} | {metric['vocal_removed']} | "
            f"{metric['conversion_rejected']} | {metric['unexpected_failures']} |"
        )
    for group in ("development", "holdout"):
        if split != "all" and group != split:
            continue
    return rows, counts, failure_examples


def _write_report(
    split: CorpusSplit,
    corpus_rows: list[str],
    corpus_counts: dict[str, Counter[str]],
    failure_examples: dict[str, list[str]],
    synthetic_rows: list[str],
    synthetic_counts: dict[str, int],
) -> None:
    """Write aggregate development/holdout results with explicit split limits."""
    selected_groups = ("development", "holdout") if split == "all" else (split,)
    summary_rows = []
    failure_rows: list[str] = []
    example_rows: list[str] = []
    for group in selected_groups:
        metric = corpus_counts[group]
        if not metric["files"]:
            continue
        total = metric["files"]
        summary_rows.append(
            f"| {group} | {total} | {metric['strict_valid']} | {metric['syntax_recovered']} "
            f"({100 * metric['syntax_recovered'] / total:.1f}%) | "
            f"{metric['conversion_success']} ({100 * metric['conversion_success'] / total:.1f}%) | "
            f"{metric['output_structurally_valid']}/{metric['conversion_success']} | "
            f"{metric['ins_preserved']}/{metric['ins_checked']} | "
            f"{metric['vocal_removed']}/{metric['conversion_success']} | "
            f"{metric['conversion_rejected']} | {metric['unexpected_failures']} |"
        )
        for key, value in sorted(metric.items()):
            if key.startswith("failure:"):
                failure_rows.append(f"| {group} | {key.removeprefix('failure:')} | {value} |")
        example_rows.extend(
            textwrap.fill(item, width=110, subsequent_indent="  ")
            for item in failure_examples[group]
        )
    synthetic_total = synthetic_counts.get("recovery_cases", 0)
    synthetic_exact = synthetic_counts.get("exact_cases", 0)
    development_total = synthetic_counts.get("development_recovery_cases", 0)
    development_exact = synthetic_counts.get("development_exact_cases", 0)
    holdout_total = synthetic_counts.get("holdout_recovery_cases", 0)
    holdout_exact = synthetic_counts.get("holdout_exact_cases", 0)
    report = [
        "# YuE2 Auto Instrumental recovery evaluation",
        "",
        _wrap_report_text(
            f"Corpus: all {len(list(CORPUS_DIR.glob('YuE2_ABC_*.txt')))} current files in `tests/raw_abc`. "
            "File IDs 00001–00096 are development; IDs above 00096 are holdout. "
            "Split assignment uses filenames only."
        ),
        "",
        _wrap_report_text(
            "The holdout is retrospective, not blind: one aggregate development run included newly added "
            "files before the corpus update was recognized. Per-file holdout diagnostics were not used to "
            "tune recovery."
        ),
        "",
        "| Split | N | Strict | Syntax ok | Converted | Output ok | Ins kept | Vocal gone | Rejected | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *summary_rows,
        "",
        _wrap_report_text(
            f"Synthetic exact event recovery: {synthetic_exact}/{synthetic_total} known-source cases. "
            f"Development: {development_exact}/{development_total}; "
            f"synthetic holdout: {holdout_exact}/{holdout_total}. "
            f"Expected safe rejections: {synthetic_counts.get('expected_rejections', 0)}; "
            f"false successes: {synthetic_counts.get('false_successes', 0)}."
        ),
        "",
        _wrap_report_text(
            "The previous implementation accepted 56/96 files (58.3%). This pass is compared below; "
            "acceptance alone is not treated as recovery accuracy."
        ),
        "",
        "## Corpus counts",
        "",
        "| Split | N | Syntax ok | Converted | Repaired | Heuristic | Ins kept | Vocal gone | Rejected | Errors |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        *corpus_rows,
        "",
        "## Rejection categories",
        "",
        "| Split | Category | Count |",
        "|---|---|---:|",
        *failure_rows,
        "",
        "## First rejected inputs per split",
        "",
        "Diagnostic examples are shown as bullets so long parser messages remain readable.",
        *example_rows,
        "",
        "## Synthetic cases",
        "",
        "| Split | Case | Event result | Output validation |",
        "|---|---|---|---|",
        *synthetic_rows,
        "",
        "## Limits",
        "",
        _wrap_report_text(
            "Synthetic coverage measures exact event recovery for the listed transformations. Real damaged "
            "corpus files have no known correct score, so their exact reconstruction accuracy is unknown. "
            "Unmatched chord text, conflicting Ins overlaps, unsupported pitch syntax, and ambiguous metadata "
            "remain diagnostic failures rather than guessed notes."
        ),
        "",
    ]
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    except Exception as exc:
        LOGGER.error(f"Unable to write recovery report {REPORT_PATH}: {exc}\n{traceback.format_exc()}")
        raise


def run_evaluation(split: CorpusSplit) -> dict[str, int]:
    """Run the selected corpus and synthetic evaluations and write a Markdown report."""
    corpus_rows, corpus_counts, failure_examples = _corpus_report(split)
    synthetic_rows, synthetic_counts = _synthetic_report(split)
    _write_report(
        split,
        corpus_rows,
        corpus_counts,
        failure_examples,
        synthetic_rows,
        synthetic_counts,
    )
    for group in ("development", "holdout"):
        if split != "all" and group != split:
            continue
        metric = corpus_counts[group]
        LOGGER.info(
            f"{group}: {metric['conversion_success']}/{metric['files']} converted, "
            f"{metric['ins_preserved']}/{metric['ins_checked']} Ins checks passed."
        )
    LOGGER.info(
        f"Synthetic exact recovery: {synthetic_counts.get('exact_cases', 0)}/"
        f"{synthetic_counts.get('recovery_cases', 0)}; "
        f"safe rejections: {synthetic_counts.get('expected_rejections', 0)}; "
        f"false successes: {synthetic_counts.get('false_successes', 0)}."
    )
    return synthetic_counts


def _parse_args() -> argparse.Namespace:
    """Read the requested development/holdout evaluation split."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", choices=("development", "holdout", "all"), default="development")
    return parser.parse_args()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    run_evaluation(_parse_args().split)
