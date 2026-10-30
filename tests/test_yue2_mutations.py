"""Ground-truth mutation checks on 25 immutable real scores."""
from __future__ import annotations

import copy
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from yue2_auto_instrumental import ABCScoreParser, ScoreError
from yue2_test_support import CORPUS, assert_ground_truth, fingerprint, verify_manifest

MUTATIONS = ("pitch", "accidental", "duration", "lost_note", "extra_note", "final_bar",
             "chord", "meter", "key", "tempo", "outro", "tie_duration")


def sample() -> list[Path]:
    """Choose a stable set covering rare meter/key/chord/section/tie features first."""
    candidates = []
    for path in sorted(CORPUS.glob("YuE2_ABC_*.txt")):
        try:
            score = ABCScoreParser("strict", 4).parse(path.read_bytes().decode("utf-8"))
        except ScoreError:
            continue
        features = {f"meter:{m.meter}" for v in score.voices.values() for m in v.measures}
        features |= {f"key:{m.key}" for v in score.voices.values() for m in v.measures}
        features |= {f"section:{s}" for s in score.sections}
        if any(e.tied_after for v in score.voices.values() for e in v.events):
            features.add("ties")
        if any(e.kind == "chord" for v in score.voices.values() for e in v.events):
            features.add("chords")
        candidates.append((path, features))
    chosen, covered = [], set()
    while candidates and len(chosen) < 25:
        selected = max(range(len(candidates)), key=lambda i: len(candidates[i][1] - covered))
        path, features = candidates.pop(selected)
        covered |= features
        chosen.append(path)
    return chosen


@pytest.mark.extended
@pytest.mark.parametrize("path", sample(), ids=lambda p: p.stem)
@pytest.mark.parametrize("mutation", MUTATIONS)
def test_corrupt_real_output_is_detected(path: Path, mutation: str) -> None:
    """Detect a spoiled audit result without asking the converter to infer ground truth."""
    verify_manifest()
    source = ABCScoreParser("strict", 4).parse(path.read_bytes().decode("utf-8"))
    changed = copy.deepcopy(source)
    events = [e for v in changed.voices.values() for e in v.events if e.kind == "note"]
    assert events, f"Mutation sample has no sounding notes: {path}"
    event = events[-1]
    if mutation in ("pitch", "accidental"):
        event.pitch += 1
    elif mutation in ("duration", "tie_duration"):
        event.duration += Fraction(1, 32)
    elif mutation == "lost_note":
        changed.voices[event.voice].events.remove(event)
    elif mutation == "extra_note":
        extra = copy.deepcopy(event)
        extra.onset += event.duration
        changed.voices[event.voice].events.append(extra)
    elif mutation == "final_bar":
        changed.voices["ins"].measures.pop()
    elif mutation == "chord":
        chord = next((e for v in changed.voices.values() for e in v.events if e.kind == "chord"), None)
        if chord is not None:
            chord.chord += "#"
        else:
            event.kind, event.chord = "chord", "C#"
    elif mutation == "meter":
        changed.voices["ins"].measures[-1].meter = (9, 8)
        if fingerprint(source) == fingerprint(changed):
            changed.voices["ins"].measures[-1].meter = (4, 4)
    elif mutation == "key":
        changed.voices["ins"].measures[-1].key += "#"
    elif mutation == "tempo":
        changed.tempos[0].bpm += 1
    elif mutation == "outro":
        changed.sections.append("CORRUPTED_OUTRO")
    with pytest.raises(AssertionError, match="ground-truth"):
        assert_ground_truth(source, changed)

TEXT_MUTATIONS = ("remove_barline", "remove_tie", "extra_tie", "pitch", "duration", "note_in_rest",
                  "final_bar", "chord", "meter", "key", "tempo", "outro", "accidental")


def mutate_text(source: str, kind: str) -> str | None:
    """Create one controlled raw-text mutation without touching any source file."""
    import re
    score = ABCScoreParser("strict", 4).parse(source)
    lines = source.splitlines(keepends=True)
    music = [record.line_index for record in score.line_records]
    if kind in ("meter", "key", "tempo", "outro"):
        if kind == "meter":
            return re.sub(r"(?m)^M:.*$", "M:7/8", source, count=1)
        if kind == "key":
            return re.sub(r"(?m)^K:.*$", "K:INVALID", source, count=1)
        if kind == "tempo":
            return re.sub(r"(?m)^Q:.*$", "Q:1/4=1", source, count=1)
        return source + "\n% outro\n% verse\n"
    if kind == "final_bar":
        for voice in ("vocal", "ins"):
            records = [r for r in score.line_records if r.voice == voice]
            record = records[-1]
            line = lines[record.line_index]
            end = line.rfind("|")
            start = line.rfind("|", 0, end) + 1
            lines[record.line_index] = line[:start] + line[end + 1:]
        return "".join(lines)
    token = re.compile(r'"[^"\n]*"|(?P<note>[\^_=]*[A-Ga-gz][,\x27]*)(?P<duration>\d*)(?P<tie>-?)')
    for index in music:
        line = lines[index]
        if kind == "remove_barline" and line.count("|") > 1:
            lines[index] = line.replace("|", "", 1)
            return "".join(lines)
        for match in token.finditer(line):
            if match.group("note") is None:
                if kind == "chord":
                    replacement = '"C#MUTATED"'
                else:
                    continue
            else:
                note, duration, tie = match.group("note", "duration", "tie")
                if kind == "remove_tie" and tie:
                    replacement = note + duration
                elif kind == "extra_tie" and note != "z" and not tie:
                    replacement = match.group() + "-"
                elif kind == "pitch" and note != "z":
                    replacement = ("D" if "C" in note else "C") + duration + tie
                elif kind == "accidental" and note != "z":
                    replacement = ("^^" if note.startswith("_") else "__") + note.lstrip("^_=") + duration + tie
                elif kind == "duration" and note != "z":
                    replacement = note + str(int(duration or "1") + 1) + tie
                elif kind == "note_in_rest" and note == "z":
                    replacement = "C" + duration
                else:
                    continue
            lines[index] = line[:match.start()] + replacement + line[match.end():]
            return "".join(lines)
    return None


@pytest.mark.extended
@pytest.mark.parametrize("path", sample(), ids=lambda p: p.stem)
@pytest.mark.parametrize("mutation", TEXT_MUTATIONS)
def test_text_mutation_auditor(path: Path, mutation: str, record_property: Any) -> None:
    """Catch every applicable textual mutation with strict parsing or ground-truth comparison."""
    source = path.read_bytes().decode("utf-8")
    mutated = mutate_text(source, mutation)
    if mutated is None:
        record_property("applicable", "false")
        return
    record_property("applicable", "true")
    original = ABCScoreParser("strict", 4).parse(source)
    try:
        corrupted = ABCScoreParser("strict", 4).parse(mutated)
    except ScoreError:
        record_property("detected", "parser_rejection")
        return
    with pytest.raises(AssertionError, match="ground-truth"):
        assert_ground_truth(original, corrupted)
    record_property("detected", "semantic_mismatch")

@pytest.mark.extended
@pytest.mark.parametrize("path", sample(), ids=lambda p: p.stem)
def test_known_barline_recovery(path: Path, record_property: Any) -> None:
    """Demand exact musical recovery when only a within-line barline was removed."""
    from yue2_auto_instrumental import _score_end, convert_abc
    from yue2_test_support import expected_notes, notes
    source = path.read_bytes().decode("utf-8")
    damaged = mutate_text(source, "remove_barline")
    if damaged is None:
        record_property("applicable", "false")
        return
    original = ABCScoreParser("strict", 4).parse(source)
    result = convert_abc(damaged, on_unrecoverable="smart")
    assert result.conversion_status != "PARTIAL_RAW", "A deleted barline was not recovered"
    output = ABCScoreParser("strict", 4).parse(result.abc)
    assert notes(output) == expected_notes(original, "instrumental_priority")
    assert _score_end(output) == _score_end(original)

@pytest.mark.unit
def test_missing_barline_preserves_original_accidental_scope() -> None:
    """Expose accidental leakage across a recovered measure boundary without changing the converter."""
    from yue2_auto_instrumental import convert_abc
    from yue2_test_support import expected_notes, notes
    original = "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\nV: Vocal\n^C8|C8|\nV: Ins\nZ2|\n"
    score = ABCScoreParser("strict", 4).parse(original)
    damaged = original.replace("^C8|C8|", "^C8C8|")
    result = convert_abc(damaged, on_unrecoverable="smart")
    output = ABCScoreParser("strict", 4).parse(result.abc)
    assert notes(output) == expected_notes(score, "instrumental_priority")

@pytest.mark.unit
@pytest.mark.parametrize("kind", ("sound_in_silence", "lost_final_rest", "extra_tie"))
def test_auditor_detects_terminal_and_tie_corruption(kind: str) -> None:
    """Reject injected sound, missing silent tails and wrongly merged tied notes."""
    prefix = "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\nV: Vocal\nZ2|\nV: Ins\n"
    if kind == "sound_in_silence":
        before, after = prefix + "C8|Z|\n", prefix + "C8|D8|\n"
    elif kind == "extra_tie":
        before, after = prefix + "C8|C8|\n", prefix + "C8-|C8|\n"
    else:
        before = prefix + "C8|Z|\n"
        after = prefix.replace("Z2|", "Z|") + "C8|\n"
    left = ABCScoreParser("strict", 4).parse(before)
    right = ABCScoreParser("strict", 4).parse(after)
    with pytest.raises(AssertionError, match="ground-truth"):
        assert_ground_truth(left, right)

@pytest.mark.unit
@pytest.mark.parametrize("lyrics", ("sections_only", "empty", "instrumental_sections", "instrumental_only"))
@pytest.mark.parametrize("mode", ("smart", "continue"))
def test_raw_contract_for_every_lyrics_mode(lyrics: str, mode: str) -> None:
    """Preserve raw musical source and uncertainty across all lyrics choices."""
    from yue2_auto_instrumental import convert_abc
    import json
    source = (
        "X:1\r\nM:4/4\r\nL:1/8\r\nQ:1/4=120\r\nK:C\r\n% outro\r\n"
        "V: Vocal\r\nC8|\r\ne''0'|\r\nV: Ins\r\nZ2|\r\n"
    )
    result = convert_abc(source, lyrics_mode=lyrics, on_unrecoverable=mode)
    assert result.conversion_status == "PARTIAL_RAW"
    if lyrics == "instrumental_sections":
        before = [line for line in source.splitlines(keepends=True) if not line.lstrip().startswith("%")]
        after = [line for line in result.abc.splitlines(keepends=True) if not line.lstrip().startswith("%")]
        assert after == before
    else:
        assert result.abc == source
    conversion = json.loads(result.warnings)["conversion"]
    assert not conversion["vocal_absence_confirmed"]
    assert not conversion["duration_known"]
    assert result.max_duration == 300.0

@pytest.mark.unit
def test_numeric_duration_uses_serialized_grid_end() -> None:
    """Expose a one-second numeric underestimate after the Ins grid is extended."""
    from yue2_auto_instrumental import _score_seconds, convert_abc
    source = (
        "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\nV: Vocal\nC8|D8|\n"
        "V: Ins\nM:5/8\nZ3|\n"
    )
    result = convert_abc(source, on_unrecoverable="smart")
    output = ABCScoreParser("strict", 4).parse(result.abc)
    assert result.score_seconds == pytest.approx(_score_seconds(output), abs=1e-8)
