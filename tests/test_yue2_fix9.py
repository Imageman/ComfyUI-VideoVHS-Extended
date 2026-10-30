"""Manifest-controlled corpus, policy and auditor mutation tests for YuE2."""
from __future__ import annotations

import itertools
import json
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterator

import pytest

from yue2_auto_instrumental import ABCScoreParser, ScoreError, _score_end, _score_seconds, convert_abc
from yue2_test_support import (
    BASELINE, CORPUS, assert_ground_truth, dependency, expected_notes, load_checker, notes, verify_manifest,
)

POLICIES = ("instrumental_priority", "vocal_priority", "mute_vocal_only")
LYRICS = ("sections_only", "empty", "instrumental_sections", "instrumental_only")
RECOVERY = ("error", "smart", "continue")
FILES = sorted(CORPUS.glob("YuE2_ABC_*.txt"))


@pytest.fixture(scope="session", autouse=True)
def immutable_corpus() -> Iterator[None]:
    """Check source bytes before and after every selected suite."""
    verify_manifest()
    dependency("YuE2Instrumental.py")
    dependency("upstream_abc_tools.py")
    yield
    verify_manifest()


@pytest.fixture(scope="session")
def checker() -> Any:
    """Require the independent checker rather than skipping its validations."""
    return load_checker()


@pytest.fixture(scope="session")
def audit_root() -> Path:
    """Retain a unique artifact namespace so repeated test runs cannot collide."""
    import tempfile
    from yue2_test_support import ROOT
    directory = ROOT / "tmp"
    directory.mkdir(exist_ok=True)
    return Path(tempfile.mkdtemp(prefix="fix9-corpus-", dir=directory))


def parse(text: str) -> Any:
    """Strictly parse a score using the converter parser."""
    return ABCScoreParser("strict", 4).parse(text)


def validate(source: str, result: Any, policy: str) -> None:
    """Check serialized timelines independently against trustworthy source events."""
    if result.conversion_status == "PARTIAL_RAW":
        assert result.abc == source
        warning = json.loads(result.warnings)
        assert not warning["conversion"]["vocal_absence_confirmed"]
        return
    output = parse(result.abc)
    assert not notes(output, "vocal")
    ins = notes(output)
    assert all(a + b <= c for (a, b, _), (c, _, _) in zip(ins, ins[1:]))
    grids = [tuple((m.onset, m.duration, m.meter, m.key) for m in output.voices[v].measures)
             for v in ("vocal", "ins")]
    assert grids[0] == grids[1]
    assert result.score_seconds == pytest.approx(_score_seconds(output), abs=1e-8)
    try:
        original = parse(source)
    except ScoreError:
        return
    assert ins == expected_notes(original, policy)
    assert _score_end(output) == _score_end(original)


@pytest.mark.corpus
@pytest.mark.upstream
@pytest.mark.parametrize("mode", ("error", "smart"))
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_full_corpus(path: Path, mode: str, checker: Any, audit_root: Path) -> None:
    """Compare every real source to an explicitly captured per-file audit baseline."""
    from yue2_auto_instrumental_audit import _audit_one_file
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    artifact = audit_root / mode
    artifact.mkdir(parents=True, exist_ok=True)
    record = _audit_one_file(path, artifact, checker, dependency("upstream_abc_tools.py"), {}, mode)
    expected = baseline[mode][path.name]
    gates = ("sha256", "conversion_status", "upstream_status", "exact_transfer_timeline",
             "ins_preservation", "vocal_duration_extensions", "added_output_measures",
             "notes_in_source_silence")
    assert record.status != "audit_error", record.error
    assert record.notes_in_source_silence == 0
    assert record.added_output_measures == 0
    assert {key: getattr(record, key) for key in gates} == {key: expected[key] for key in gates}
    if record.conversion_status in ("OK", "SMART_RECOVERED"):
        assert record.details["reported_score_seconds"] == pytest.approx(
            record.details["serialized_score_seconds"], abs=1e-8
        )


@pytest.mark.extended
@pytest.mark.upstream
@pytest.mark.parametrize("policy,lyrics,mode", itertools.product(POLICIES, LYRICS, RECOVERY))
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_full_mode_matrix(path: Path, policy: str, lyrics: str, mode: str, checker: Any) -> None:
    """Validate every applicable recovery, lyrics and overlap policy combination."""
    source = path.read_bytes().decode("utf-8")
    try:
        result = convert_abc(source, melody_policy=policy, lyrics_mode=lyrics, on_unrecoverable=mode)
    except ScoreError:
        assert mode == "error"
        return
    if result.conversion_status == "PARTIAL_RAW" and lyrics == "instrumental_sections":
        original_music = [line for line in source.splitlines(keepends=True)
                          if not line.lstrip().startswith("%")]
        output_music = [line for line in result.abc.splitlines(keepends=True)
                        if not line.lstrip().startswith("%")]
        assert output_music == original_music
        assert not json.loads(result.warnings)["conversion"]["vocal_absence_confirmed"]
        return
    validate(source, result, policy)
    if result.conversion_status != "PARTIAL_RAW":
        checker.parse(result.abc)


@pytest.mark.unit
@pytest.mark.parametrize("policy", POLICIES)
def test_overlap_oracle(policy: str) -> None:
    """Compare overlap policies to a separate fractional interval implementation."""
    source = "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\nV: Vocal\nC8|\nV: Ins\nz2E2z4|\n"
    validate(source, convert_abc(source, melody_policy=policy), policy)


@pytest.mark.unit
@pytest.mark.parametrize("body", ("D8|", "C4z4|", "C8|C8|", "z8|"))
def test_auditor_rejects_synthetic_mutations(body: str) -> None:
    """Detect pitch, duration, added-tail and lost-note corruption independently."""
    prefix = "X:1\nM:4/4\nL:1/8\nQ:1/4=120\nK:C\nV: Vocal\n"
    before = parse(prefix + "C8|\nV: Ins\nZ|\n")
    after = parse(prefix + body + "\nV: Ins\n" + ("Z2|" if body.count("|") == 2 else "Z|") + "\n")
    with pytest.raises(AssertionError, match="ground-truth"):
        assert_ground_truth(before, after)


@pytest.mark.extended
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_real_ground_truth_mutation(path: Path) -> None:
    """Make sure changing a parsed real pitch is rejected even when syntax stays valid."""
    import copy
    source = path.read_bytes().decode("utf-8")
    try:
        score = parse(source)
    except ScoreError:
        return
    for voice in score.voices.values():
        for index, event in enumerate(voice.events):
            if event.kind == "note":
                changed = copy.deepcopy(score)
                target = next(v for k, v in changed.voices.items() if v.name == voice.name)
                target.events[index].pitch += 1
                with pytest.raises(AssertionError):
                    assert_ground_truth(score, changed)
                return
from test_yue2_auto_instrumental import YuE2AutoInstrumentalTests

@pytest.mark.unit
def test_final_note_and_trailing_rest() -> None:
    """Preserve five terminal bars while removing invalid cross-pitch ties."""
    source = (
        "X:1\nM:4/4\nL:1/32\nQ:1/4=120\nK:C\n% outro\nV: Vocal\n"
        "D,32-|A,,32-|=B,,32-|=B,,32-|\nV: Ins\nZ4|\n"
        "V: Vocal\n=B,,8z24|\nV: Ins\nZ|\n"
    )
    result = convert_abc(source)
    output = parse(result.abc)
    assert result.bars == 5
    assert _score_end(output) == 5
    assert notes(output)[-1] == (Fraction(2), Fraction(9, 4), 47)
    assert not any(a + b > Fraction(17, 4) for a, b, _ in notes(output))


@pytest.mark.extended
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_smart_determinism(path: Path) -> None:
    """Repeat Smart decisions and compare output plus selected musical changes."""
    source = path.read_bytes().decode("utf-8")
    first = convert_abc(source, on_unrecoverable="smart")
    second = convert_abc(source, on_unrecoverable="smart")
    assert (first.abc, first.conversion_status) == (second.abc, second.conversion_status)


@pytest.mark.corpus
@pytest.mark.parametrize("path", FILES, ids=lambda p: p.stem)
def test_confirmed_silence(path: Path) -> None:
    """Forbid new sound anywhere both strictly parsed source voices are silent."""
    from yue2_test_support import silence_metrics
    source = path.read_bytes().decode("utf-8")
    try:
        original = parse(source)
    except ScoreError:
        return
    result = convert_abc(source)
    output = parse(result.abc)
    metrics = silence_metrics(original)
    for left, right in metrics["intervals"]:
        assert not any(a < right and left < a + b for a, b, _ in notes(output))

@pytest.mark.corpus
@pytest.mark.parametrize("file_id", (3, 18, 28, 71, 187, 207, 229, 259, 276, 299, 312, 353))
def test_fix8_duration_spans(file_id: int) -> None:
    """Compare each risky duration hypothesis and exact source span to the frozen audit."""
    name = f"YuE2_ABC_{file_id:05d}.txt"
    source = (CORPUS / name).read_bytes().decode("utf-8")
    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["smart"][name]
    result = convert_abc(source, on_unrecoverable="smart")
    changes = json.loads(result.warnings)["conversion"]["source_musical_event_changes"]["duration_changes"]
    expected = baseline["musical_preservation"]["vocal_duration_extensions"]
    vocal = [change for change in changes if change["voice"] == "vocal"]
    assert vocal == expected
    for change in vocal:
        span = change["source_span"]
        assert source[span["start_offset"]:span["end_offset"]] == span["text"]
        assert Fraction(change["repaired_duration"]) > Fraction(change["original_duration"])
        assert change["reason"]

@pytest.mark.corpus
@pytest.mark.parametrize("file_id", (3, 18, 28, 71, 187, 207, 229, 259, 276, 299, 312, 353))
def test_smart_duration_hypothesis_determinism(file_id: int) -> None:
    """Repeat the risky Smart decisions and compare selected source event changes."""
    source = (CORPUS / f"YuE2_ABC_{file_id:05d}.txt").read_bytes().decode("utf-8")
    first = convert_abc(source, on_unrecoverable="smart")
    second = convert_abc(source, on_unrecoverable="smart")
    assert first.abc == second.abc
    left = json.loads(first.warnings)["conversion"]["source_musical_event_changes"]
    right = json.loads(second.warnings)["conversion"]["source_musical_event_changes"]
    assert left == right

@pytest.mark.unit
@pytest.mark.parametrize("mutation", ("bytes", "file_set"))
def test_manifest_rejects_fixture_drift(tmp_path: Path, monkeypatch: Any, mutation: str) -> None:
    """Prove that tests refuse changed fixture contents and additional corpus files."""
    import yue2_test_support as support
    corpus = tmp_path / "raw_abc"
    corpus.mkdir()
    source = corpus / "YuE2_ABC_00001.txt"
    source.write_bytes(b"original")
    manifest = tmp_path / "manifest.json"
    monkeypatch.setattr(support, "CORPUS", corpus)
    monkeypatch.setattr(support, "MANIFEST", manifest)
    manifest.write_text(json.dumps(support.current_manifest()), encoding="utf-8")
    if mutation == "bytes":
        source.write_bytes(b"changed")
    else:
        (corpus / "YuE2_ABC_00002.txt").write_bytes(b"extra")
    with pytest.raises(AssertionError, match="manifest mismatch"):
        support.verify_manifest()


@pytest.mark.unit
def test_missing_checker_is_configuration_error(tmp_path: Path, monkeypatch: Any) -> None:
    """Refuse to silently skip independent validation when its fixture is missing."""
    import yue2_test_support as support
    (tmp_path / "tests").mkdir()
    monkeypatch.setattr(support, "ROOT", tmp_path)
    with pytest.raises(RuntimeError, match="Expected one tests"):
        support.load_checker()

@pytest.mark.corpus
@pytest.mark.parametrize("file_id", (28, 45, 129, 188, 200, 229, 235, 265, 283, 287, 319, 405))
def test_reported_duration_matches_rendered_score(file_id: int) -> None:
    """Expose numeric duration calculated before the repaired grid is serialized."""
    source = (CORPUS / f"YuE2_ABC_{file_id:05d}.txt").read_bytes().decode("utf-8")
    result = convert_abc(source, on_unrecoverable="smart")
    assert result.conversion_status != "PARTIAL_RAW"
    output = parse(result.abc)
    assert result.score_seconds == pytest.approx(_score_seconds(output), abs=1e-8)

@pytest.mark.unit
def test_audit_refuses_noncanonical_corpus(monkeypatch: Any) -> None:
    """Prevent an old tmp corpus from silently replacing the manifest-controlled source."""
    from argparse import Namespace
    import yue2_auto_instrumental_audit as audit
    monkeypatch.setattr(audit, "_parse_args", lambda: Namespace(
        corpus=audit.ROOT / "tmp" / "raw_abc", checker=dependency("upstream_abc_tools.py")
    ))
    with pytest.raises(ValueError, match="canonical ABC corpus"):
        audit.main()
