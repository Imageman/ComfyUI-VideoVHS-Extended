"""Convert YuE2 ABC scores to instrumental scores without requiring ComfyUI."""

from __future__ import annotations

import copy
import difflib
import hashlib
import importlib
import json
import logging
import math
import re
import tempfile
import time
import traceback
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Literal


LOGGER = logging.getLogger(__name__)
MAX_SCORE_CHARS = 2_000_000
MAX_SCORE_LINES = 100_000
MAX_MEASURES = 2_000
MAX_EVENTS = 200_000
MAX_NUMERIC_TOKEN = 999_999
MAX_DURATION_SECONDS = 900.0
DEFAULT_PARTIAL_RAW_MAX_DURATION_SECONDS = 300.0
MAX_PADDING_SECONDS = 30.0
INSTRUMENTAL_LYRIC_SECONDS = 40.0
MIN_INSTRUMENTAL_LYRIC_SECTIONS = 1
MAX_INSTRUMENTAL_LYRIC_SECTIONS = 12
DEFAULT_INSTRUMENTAL_LYRIC_SECTIONS = 3
MIN_MAX_DURATION_SECONDS = 0.04
MAX_REPAIR_UNIT_LIMIT = 32
MAX_REPAIR_CANDIDATES = 32
MAX_REPAIR_LOOKAHEAD_MEASURES = 4
MAX_REPAIR_SUFFIX_EVENTS = 20_000
MAX_SMART_CANDIDATES = 32
MAX_SMART_DEPTH = 3
MAX_SMART_BEAM_WIDTH = 6
MAX_SMART_PROPOSALS_PER_CATEGORY = 8
MAX_SMART_CHORD_SCAN_CHARS = 4096
MAX_SMART_SECONDS = 2.0
MAX_UPSTREAM_NOTE_LENGTH_DENOMINATOR = 1024
YU2_SUPPORTED_DURATION_UNITS = (48, 32, 24, 16, 12, 8, 6, 4, 3, 2, 1)
MIDI_C4 = 60
SEMITONES_PER_OCTAVE = 12
MIN_MIDI_PITCH = 0
MAX_MIDI_PITCH = 127
DEFAULT_PADDING_SECONDS = 0.0
DEFAULT_MAX_REPAIR_UNITS = 4

RepairMode = Literal["strict", "safe", "extended"]
MelodyPolicy = Literal["instrumental_priority", "vocal_priority", "mute_vocal_only"]
LyricsMode = Literal["sections_only", "empty", "instrumental_sections", "instrumental_only"]


class ScoreError(ValueError):
    """Represent an ABC score that cannot be converted without guessing."""


@dataclass
class Diagnostic:
    """Describe a validation issue and any deterministic repair applied."""

    severity: str
    voice: str
    measure: int | None
    location: str
    problem: str
    repair: str = ""
    code: str = ""

    def to_dict(self) -> dict[str, str | int | None]:
        """Return the diagnostic as a JSON-compatible mapping."""
        return {
            "severity": self.severity,
            "voice": self.voice,
            "measure": self.measure,
            "location": self.location,
            "problem": self.problem,
            "repair": self.repair,
            "code": self.code,
        }


@dataclass
class ScoreEvent:
    """Represent one pitched note, rest, or chord label on the score timeline."""

    kind: Literal["note", "rest", "chord"]
    onset: Fraction
    duration: Fraction
    voice: str
    measure_index: int
    line_number: int
    pitch: int | None = None
    spelling: str = ""
    accidental: str | None = None
    chord: str = ""
    tied_after: bool = False
    source_order: int = 0


@dataclass
class Measure:
    """Store a voice measure and its time signature at the time it was parsed."""

    onset: Fraction
    duration: Fraction
    meter: tuple[int, int]
    note_length: Fraction
    key: str
    line_number: int
    events: list[ScoreEvent] = field(default_factory=list)


@dataclass(frozen=True)
class ShortMeasureObservation:
    """Capture unpadded content length for a later cross-voice meter check."""

    voice: str
    measure_index: int
    onset: Fraction
    actual_duration: Fraction
    expected_duration: Fraction
    ends_with_tie: bool


@dataclass
class LineRecord:
    """Connect a source music line with the measures parsed from that line."""

    line_index: int
    voice: str
    measure_indices: list[int]
    comment: str | None


@dataclass
class VoiceScore:
    """Hold parsed measures and events for a named ABC voice."""

    name: str
    meter: tuple[int, int]
    note_length: Fraction
    key: str
    cursor: Fraction = Fraction(0)
    measures: list[Measure] = field(default_factory=list)
    events: list[ScoreEvent] = field(default_factory=list)


@dataclass(frozen=True)
class ABCToken:
    """Hold one lexed musical token and its source span."""

    kind: Literal["note", "chord", "unknown"]
    raw: str
    start: int
    end: int
    repair: str = ""


@dataclass
class PartialParseState:
    """Retain completed voices and the active measure when token parsing fails."""

    voices: dict[str, VoiceScore] = field(default_factory=dict)
    line_records: list[LineRecord] = field(default_factory=list)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    active_voice: str | None = None
    line_number: int | None = None
    measure_events: list[ScoreEvent] = field(default_factory=list)


@dataclass
class TempoChange:
    """Store a tempo change at an absolute whole-note position."""

    onset: Fraction
    beat_unit: Fraction
    bpm: float
    voice: str | None = None


@dataclass(frozen=True)
class RepairCandidate:
    """Represent an event-level proposal with cost order: Ins, time, events, chords, transfer, complexity."""

    action: str
    onset_overrides: tuple[tuple[int, Fraction], ...]
    duration_overrides: tuple[tuple[int, Fraction], ...]
    cost: tuple[int, int, int, int, int, int]
    diagnostics: tuple[Diagnostic, ...]
    ignored_tokens: tuple[int, ...] = ()
    note_token_overrides: tuple[tuple[int, str], ...] = ()


@dataclass
class ParsedScore:
    """Contain the parsed score, preserved source lines, and diagnostics."""

    source_lines: list[str]
    line_records: list[LineRecord]
    voices: dict[str, VoiceScore]
    diagnostics: list[Diagnostic]
    tempos: list[TempoChange]
    sections: list[str]
    initial_meter: tuple[int, int]
    initial_note_length: Fraction
    initial_key: str
    metadata_trailing_comments: dict[int, str]
    newline: str


@dataclass
class ConversionResult:
    """Return the converted ABC, validation status, and structured statistics."""

    abc: str
    lyrics: str
    max_duration: float
    score_seconds: float
    bars: int
    bpm: float
    info: str
    warnings: str
    repair_count: int
    diagnostics: list[Diagnostic]
    repaired_abc: str = ""
    candidate_log: list[dict[str, object]] = field(default_factory=list)
    conversion_status: str = "OK"
    verbatim_spans: list[dict[str, object]] = field(default_factory=list)
    diagnostic_segments: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class _SmartTextCandidate:
    """Describe one bounded text repair proposal and its heuristic cost."""

    text: str
    category: str
    reason: str
    cost: tuple[int, int, int]
    details: dict[str, object] = field(default_factory=dict)


NOTE_PATTERN = re.compile(
    r"(?P<acc>\^\^|__|\^|_|=)?(?P<note>[A-Ga-gzZ])"
    r"(?P<oct>[' ,]*)(?P<length>\d*(?:/\d*)?)(?P<dots>\.*)(?P<tie>-?)"
)
CHORD_PATTERN = re.compile(r'"(?:\\.|[^"\\])*"')
CHORD_ROOT_PATTERN = r"[A-G](?:#{1,2}|b{1,2})?"
CHORD_COMPONENT_PATTERN = CHORD_ROOT_PATTERN + r"[A-Za-z0-9+#b°ø()\-]*"
CHORD_SYMBOL_PATTERN = re.compile(
    CHORD_COMPONENT_PATTERN + r"(?:/" + CHORD_COMPONENT_PATTERN + r"){0,3}\Z"
)
YU2_SUPPORTED_CHORD_QUALITIES = frozenset(
    ("", "m", "dim", "aug", "7", "maj7", "m7", "dim7", "m7b5", "sus4", "sus2", "6", "m6", "7sus4", "m(maj7)")
)
CHORD_PREFIX_REST_PATTERN = re.compile(r'(?P<chords>(?:"(?:\\.|[^"\\])*"\s*)+)Z\Z')
METER_PATTERN = re.compile(r"(?P<top>\d+)\s*/\s*(?P<bottom>\d+)")
NOTE_LENGTH_PATTERN = re.compile(r"1\s*/\s*(\d+)")
TEMPO_PATTERN = re.compile(r"(?:(\d+)\s*/\s*(\d+)\s*=\s*)?(\d+(?:\.\d+)?)")
FIELD_PATTERN = re.compile(r"^\s*([A-Za-z]):\s*(.*?)\s*$")
BODY_FIELD_PREFIX_PATTERN = re.compile(r"^\s*([A-Za-z]):\s*(.*)$")
VOICE_PATTERN = re.compile(r"^\s*V:\s*([^\s]+)(?:\s+.*)?$", re.IGNORECASE)
BAR_REST_PATTERN = re.compile(r"Z(\d*)\Z")
NATURAL_PITCHES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
KEY_SIGNATURES = {
    "C": 0,
    "G": 1,
    "D": 2,
    "A": 3,
    "E": 4,
    "B": 5,
    "F#": 6,
    "C#": 7,
    "F": -1,
    "Bb": -2,
    "Eb": -3,
    "Ab": -4,
    "Db": -5,
    "Gb": -6,
    "Cb": -7,
    "Am": 0,
    "Em": 1,
    "Bm": 2,
    "F#m": 3,
    "C#m": 4,
    "G#m": 5,
    "D#m": 6,
    "A#m": 7,
    "Dm": -1,
    "Gm": -2,
    "Cm": -3,
    "Fm": -4,
    "Bbm": -5,
    "Ebm": -6,
    "Abm": -7,
}
SECTION_ALIASES = {
    "prechorus": "pre-chorus",
    "pre chorus": "pre-chorus",
    "pre_chorus": "pre-chorus",
}
VOICE_ALIASES = {
    "v": "vocal",
    "vox": "vocal",
    "vocalmelody": "vocal",
    "vocal-melody": "vocal",
    "inschorlude": "ins",
    "inst": "ins",
    "inst.": "ins",
    "instrumental": "ins",
    "instrumental-melody": "ins",
    "accompaniment": "ins",
}
KNOWN_SECTIONS = {
    "intro",
    "verse",
    "pre-chorus",
    "chorus",
    "bridge",
    "interlude",
    "instrumental",
    "breakdown",
    "solo",
    "hook",
    "refrain",
    "outro",
}
SECTION_TAG_PATTERN = re.compile(r"\s*%\s*([A-Za-z][A-Za-z _-]*)\s*")
INSTRUMENTAL_SECTION_MAP = {
    "intro": "Intro",
    "verse": "Instrumental",
    "pre-chorus": "Instrumental",
    "chorus": "Interlude",
    "bridge": "Bridge",
    "interlude": "Interlude",
    "instrumental": "Instrumental",
    "outro": "Outro",
}
NON_MUSICAL_WARNING_CODES = frozenset(
    {
        "unsupported_chord_quality",
        "source_spelling_accidental_adjusted",
        "source_spelling_fallback",
        "unknown_instrumental_section",
        "duplicate_instrumental_section",
        "unknown_instrumental_duration",
        "instrumental_lyrics_may_not_match_abc",
    }
)


def _parse_bounded_int(value: str, label: str, allow_zero: bool = False) -> int:
    """Parse a bounded integer used by ABC timing fields."""
    if not value or len(value) > 6 or not value.isdigit():
        raise ScoreError(f"Invalid or oversized {label} {value!r}.")
    result = int(value)
    minimum = 0 if allow_zero else 1
    if result < minimum or result > MAX_NUMERIC_TOKEN:
        raise ScoreError(f"{label} must be between {minimum} and {MAX_NUMERIC_TOKEN}.")
    return result


def _parse_fraction(value: str, default: Fraction = Fraction(1)) -> Fraction:
    """Parse an ABC duration multiplier into an exact fraction."""
    if not value:
        return default
    if "/" not in value:
        return Fraction(_parse_bounded_int(value, "note duration", allow_zero=True), 1)
    numerator_text, denominator_text = value.split("/", 1)
    numerator = _parse_bounded_int(numerator_text, "note duration", allow_zero=True) if numerator_text else 1
    denominator = _parse_bounded_int(denominator_text, "note duration denominator") if denominator_text else 2
    if denominator <= 0:
        raise ScoreError("ABC note duration has a zero or negative denominator.")
    return Fraction(numerator, denominator)


def _parse_meter(value: str) -> tuple[int, int]:
    """Parse a positive ABC meter such as 4/4 or 9/8."""
    match = METER_PATTERN.fullmatch(value.strip())
    if not match:
        raise ScoreError(f"Unsupported ABC meter {value!r}.")
    meter = (
        _parse_bounded_int(match.group("top"), "meter numerator"),
        _parse_bounded_int(match.group("bottom"), "meter denominator"),
    )
    if min(meter) <= 0:
        raise ScoreError(f"ABC meter must be positive, received {value!r}.")
    return meter


def _measure_duration(meter: tuple[int, int]) -> Fraction:
    """Return the measure length in whole-note units."""
    return Fraction(meter[0], meter[1])


def _parse_key_signature(key: str) -> dict[str, int]:
    """Return the ABC key-signature alteration for each note letter."""
    normalized = key.strip().split()[0] if key.strip() else ""
    if normalized not in KEY_SIGNATURES:
        raise ScoreError(f"Unsupported ABC key {key!r}.")
    count = KEY_SIGNATURES[normalized]
    order = "FCGDAEB" if count > 0 else "BEADGCF"
    accidentals = {letter: 0 for letter in NATURAL_PITCHES}
    for letter in order[: abs(count)]:
        accidentals[letter] = 1 if count > 0 else -1
    return accidentals


def _parse_tempo(value: str) -> TempoChange:
    """Parse an ABC Q: field into a beat unit and beats per minute."""
    match = TEMPO_PATTERN.fullmatch(value.strip())
    if not match:
        raise ScoreError(f"Unsupported ABC tempo {value!r}.")
    numerator = _parse_bounded_int(match.group(1), "tempo beat numerator") if match.group(1) else 1
    denominator = _parse_bounded_int(match.group(2), "tempo beat denominator") if match.group(2) else 4
    if len(match.group(3)) > 12:
        raise ScoreError(f"Tempo value is oversized in {value!r}.")
    bpm = float(match.group(3))
    if numerator <= 0 or denominator <= 0 or not math.isfinite(bpm) or bpm <= 0:
        raise ScoreError(f"ABC tempo must be positive, received {value!r}.")
    return TempoChange(Fraction(0), Fraction(numerator, denominator), bpm)


def _match_chord_symbol(text: str, position: int = 0) -> re.Match[str] | None:
    """Match one quoted chord only when its root and optional bass note are valid."""
    match = CHORD_PATTERN.match(text, position)
    if match is None:
        return None
    if CHORD_SYMBOL_PATTERN.fullmatch(match.group()[1:-1]) is None:
        return None
    return match


def _is_note_sequence(text: str) -> bool:
    """Return whether a string can be consumed entirely as adjacent ABC note tokens."""
    cursor = 0
    while cursor < len(text):
        note = NOTE_PATTERN.match(text, cursor)
        if note is None or note.end() == cursor:
            return False
        cursor = note.end()
    return bool(text)


def _chord_quality_supported(chord: str) -> bool:
    """Check a lexical chord label against YuE's supported quality vocabulary."""
    parts = chord[1:-1].split("/")
    if len(parts) > 2:
        return False
    root_match = re.fullmatch(r"([A-G](?:#{1,2}|b{1,2})?)(.*)", parts[0])
    if root_match is None or root_match.group(2) not in YU2_SUPPORTED_CHORD_QUALITIES:
        return False
    return all(re.fullmatch(CHORD_ROOT_PATTERN, bass) is not None for bass in parts[1:])


def _pitch_from_token(
    note: str,
    octave_marks: str,
    accidental: str | None,
    key_signature: dict[str, int],
    measure_accidentals: dict[str, int],
) -> tuple[int, int]:
    """Resolve an ABC note token to MIDI pitch and accidental state."""
    letter = note.upper()
    natural = MIDI_C4 + NATURAL_PITCHES[letter]
    if note.islower():
        natural += SEMITONES_PER_OCTAVE
    octave_offset = octave_marks.count("'") - octave_marks.count(",")
    natural += SEMITONES_PER_OCTAVE * octave_offset
    if accidental is not None:
        alteration = {"=": 0, "^": 1, "^^": 2, "_": -1, "__": -2}[accidental]
        measure_accidentals[letter] = alteration
    else:
        alteration = measure_accidentals.get(letter, key_signature[letter])
    pitch = natural + alteration
    if not MIN_MIDI_PITCH <= pitch <= MAX_MIDI_PITCH:
        raise ScoreError(f"ABC note {note}{octave_marks} is outside MIDI pitch range.")
    return pitch, alteration


def _pitch_to_token(pitch: int) -> str:
    """Spell a MIDI pitch as a stable ABC note token with an explicit accidental."""
    pitch_class = pitch % SEMITONES_PER_OCTAVE
    names = {
        0: ("C", 0, "="),
        1: ("C", 1, "^"),
        2: ("D", 0, "="),
        3: ("D", 1, "^"),
        4: ("E", 0, "="),
        5: ("F", 0, "="),
        6: ("F", 1, "^"),
        7: ("G", 0, "="),
        8: ("G", 1, "^"),
        9: ("A", 0, "="),
        10: ("A", 1, "^"),
        11: ("B", 0, "="),
    }
    letter, alteration, accidental = names[pitch_class]
    base_pitch = MIDI_C4 + NATURAL_PITCHES[letter] + alteration
    octave_delta = (pitch - base_pitch) // SEMITONES_PER_OCTAVE
    if octave_delta < 0:
        note = letter + "," * abs(octave_delta)
    elif octave_delta == 0:
        note = letter
    else:
        note = letter.lower() + "'" * (octave_delta - 1)
    return accidental + note


def _extract_sections(lines: list[str]) -> list[str]:
    """Extract standalone section tags, preserving their source names and order."""
    sections = [
        re.sub(r"\s+", " ", match.group(1).strip())
        for line in lines
        if (match := SECTION_TAG_PATTERN.fullmatch(line)) is not None
    ]
    return sections or ["Intro", "Outro"]


def _instrumental_section_name(source_name: str) -> tuple[str, bool]:
    """Map a section tag to a supported YuE instrumental label."""
    normalized = re.sub(r"[\s_]+", " ", source_name.strip().lower())
    normalized = SECTION_ALIASES.get(normalized, normalized)
    if normalized in INSTRUMENTAL_SECTION_MAP:
        return INSTRUMENTAL_SECTION_MAP[normalized], True
    return "Instrumental", False


def _build_lyrics(
    sections: list[str],
    source_lines: list[str],
    lyrics_mode: LyricsMode,
    score_seconds: float | None,
    duration_known: bool,
) -> tuple[str, dict[int, str | None], list[Diagnostic], bool]:
    """Build Lyrics and optional section-comment rewrites for the requested mode."""
    section_comment_overrides: dict[int, str | None] = {}
    diagnostics: list[Diagnostic] = []
    add_fallback_comments = False
    if lyrics_mode == "sections_only":
        lyrics_sections = sections
    elif lyrics_mode == "empty":
        return "", section_comment_overrides, diagnostics, add_fallback_comments
    elif lyrics_mode == "instrumental_sections":
        source_tags = [
            (line_index, re.sub(r"\s+", " ", match.group(1).strip()))
            for line_index, line in enumerate(source_lines)
            if (match := SECTION_TAG_PATTERN.fullmatch(line)) is not None
        ]
        if not source_tags:
            lyrics_sections = ["Intro", "Outro"]
            add_fallback_comments = True
        else:
            lyrics_sections = []
            unknown_count = 0
            merged_count = 0
            for line_index, source_name in source_tags:
                section, recognized = _instrumental_section_name(source_name)
                unknown_count += int(not recognized)
                if lyrics_sections and lyrics_sections[-1] == section:
                    section_comment_overrides[line_index] = None
                    merged_count += 1
                    continue
                lyrics_sections.append(section)
                section_comment_overrides[line_index] = section
            if unknown_count:
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        "score",
                        None,
                        "Lyrics section mapping",
                        f"Mapped {unknown_count} unknown section tag(s) to Instrumental.",
                        "Preserve the section position using the supported Instrumental label.",
                        "unknown_instrumental_section",
                    )
                )
            if merged_count:
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        "score",
                        None,
                        "Lyrics section mapping",
                        f"Merged {merged_count} adjacent duplicate instrumental section label(s).",
                        "Remove repeated adjacent comments after section mapping.",
                        "duplicate_instrumental_section",
                    )
                )
    elif lyrics_mode == "instrumental_only":
        if duration_known and score_seconds is not None:
            count = math.ceil(score_seconds / INSTRUMENTAL_LYRIC_SECONDS)
            count = max(
                MIN_INSTRUMENTAL_LYRIC_SECTIONS,
                min(MAX_INSTRUMENTAL_LYRIC_SECTIONS, count),
            )
        else:
            count = DEFAULT_INSTRUMENTAL_LYRIC_SECTIONS
            diagnostics.append(
                Diagnostic(
                    "warning",
                    "score",
                    None,
                    "Lyrics duration",
                    "Total duration is unknown for a partial ABC result.",
                    f"Use {DEFAULT_INSTRUMENTAL_LYRIC_SECTIONS} Instrumental Lyrics sections.",
                    "unknown_instrumental_duration",
                )
            )
        lyrics_sections = ["Intro", *("Instrumental" for _ in range(count)), "Outro"]
        diagnostics.append(
            Diagnostic(
                "warning",
                "score",
                None,
                "Lyrics mode",
                "Generated instrumental Lyrics may not match ABC section comments or pass "
                "YuE2's strict instrumental checker.",
                "Review Lyrics against the ABC score before using the strict instrumental request checker.",
                "instrumental_lyrics_may_not_match_abc",
            )
        )
    else:
        raise ScoreError(f"Unsupported lyrics mode {lyrics_mode!r}.")

    lyrics = "\n".join(f"[{section}]" for section in lyrics_sections)
    return lyrics, section_comment_overrides, diagnostics, add_fallback_comments


def _rewrite_raw_section_comments(
    abc: str,
    section_comment_overrides: dict[int, str | None],
    add_fallback_section_comments: bool,
) -> str:
    """Change only standalone section comments in an otherwise raw fallback string."""
    lines = abc.splitlines(keepends=True)
    for line_index, section in section_comment_overrides.items():
        if not 0 <= line_index < len(lines):
            continue
        line = lines[line_index]
        ending = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        lines[line_index] = f"% {section}{ending}" if section is not None else ""
    if add_fallback_section_comments:
        newline = "\r\n" if "\r\n" in abc else "\n"
        first_voice = next(
            (index for index, line in enumerate(lines) if VOICE_PATTERN.fullmatch(line.strip())),
            len(lines),
        )
        lines.insert(first_voice, f"% Intro{newline}")
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        lines.append(f"% Outro{newline}")
    return "".join(lines)


class ABCScoreParser:
    """Parse and normalize the two-voice ABC subset used by YuE2."""

    def __init__(self, repair_mode: RepairMode, max_repair_units: int) -> None:
        """Initialize parsing options and diagnostic state."""
        if repair_mode not in ("strict", "safe", "extended"):
            raise ScoreError(f"Unknown repair mode {repair_mode!r}.")
        if max_repair_units < 0:
            raise ScoreError("max_repair_units cannot be negative.")
        self.repair_mode = repair_mode
        self.max_repair_units = max_repair_units
        self.diagnostics: list[Diagnostic] = []
        self.line_records: list[LineRecord] = []
        self.tempo_changes: list[TempoChange] = []
        self.metadata_trailing_comments: dict[int, str] = {}
        self.unsupported_chord_labels: set[str] = set()
        self.short_measures: list[ShortMeasureObservation] = []
        self.pending_overflows: list[tuple[str, int, Fraction]] = []
        self.partial_state = PartialParseState()
        self.candidate_log: list[dict[str, object]] = []
        self._event_order = 0

    def parse(self, abc: str) -> ParsedScore:
        """Parse ABC text and apply only the selected deterministic repairs."""
        if not abc or not abc.strip():
            raise ScoreError("ABC score is empty.")
        if len(abc) > MAX_SCORE_CHARS:
            raise ScoreError(f"ABC score exceeds the {MAX_SCORE_CHARS} character limit.")

        lines = abc.splitlines()
        if len(lines) > MAX_SCORE_LINES:
            raise ScoreError(f"ABC score exceeds the {MAX_SCORE_LINES} line limit.")
        headers, header_end = self._read_header(lines)
        initial_meter = _parse_meter(headers["M"])
        initial_note_length = self._parse_note_length(headers["L"])
        initial_key = headers["K"]
        _parse_key_signature(initial_key)
        initial_tempo = _parse_tempo(headers["Q"])
        self.tempo_changes = [initial_tempo]
        voices = {
            name: VoiceScore(name, initial_meter, initial_note_length, initial_key)
            for name in ("vocal", "ins")
        }
        self.partial_state = PartialParseState(voices=voices)

        active_voice: str | None = None
        for line_index in range(header_end + 1, len(lines)):
            line = lines[line_index]
            stripped = line.strip()
            if not stripped or stripped.startswith("%"):
                continue
            voice_match = VOICE_PATTERN.fullmatch(stripped)
            if voice_match:
                active_voice = self._resolve_voice(voice_match.group(1), line_index)
                self.partial_state.active_voice = active_voice
                continue
            field_match = BODY_FIELD_PREFIX_PATTERN.match(stripped)
            if field_match and field_match.group(1) in ("M", "L", "K", "Q"):
                if active_voice is None:
                    raise ScoreError(f"ABC field at line {line_index + 1} appears before a voice.")
                self._consume_body_fields(line_index, stripped, voices[active_voice])
                continue
            if active_voice is None:
                raise ScoreError(f"Expected V: Vocal or V: Ins before music at line {line_index + 1}.")
            self.partial_state.line_number = line_index + 1
            self._parse_music_line(line_index, line, voices[active_voice])

        if self.repair_mode == "extended":
            self._check_short_measure_hypotheses()

        for voice in voices.values():
            if not voice.measures:
                self._repair_or_raise(
                    voice.name,
                    None,
                    "score end",
                    f"Voice {voice.name} contains no measures.",
                    "No empty music measures were added.",
                )
            self._resolve_ties(voice)

        if self.repair_mode == "extended":
            self._resolve_overfull_measures(voices)
            self._record_ins_grid_alignment(voices)
        self.partial_state.line_records = self.line_records
        self.partial_state.diagnostics = self.diagnostics

        if not voices["ins"].measures and not voices["vocal"].measures:
            raise ScoreError("ABC score contains no measures.")
        if self.repair_mode == "strict":
            self._validate_voice_grids(voices)
        self._check_limits(voices)
        return ParsedScore(
            source_lines=lines,
            line_records=self.line_records,
            voices=voices,
            diagnostics=self.diagnostics,
            tempos=self._normalized_tempos(),
            sections=_extract_sections(lines),
            initial_meter=initial_meter,
            initial_note_length=initial_note_length,
            initial_key=initial_key,
            metadata_trailing_comments=self.metadata_trailing_comments,
            newline="\r\n" if "\r\n" in abc else "\n",
        )

    def _record_ins_grid_alignment(self, voices: dict[str, VoiceScore]) -> None:
        """Choose the Ins grid as canonical when the two parsed voice grids differ."""
        vocal_grid = [
            (measure.onset, measure.duration, measure.meter, measure.note_length, measure.key)
            for measure in voices["vocal"].measures
        ]
        ins_grid = [
            (measure.onset, measure.duration, measure.meter, measure.note_length, measure.key)
            for measure in voices["ins"].measures
        ]
        if vocal_grid == ins_grid or not ins_grid:
            return
        self._add_diagnostic(
            "warning",
            "vocal",
            None,
            "score timeline",
            "Vocal and Ins source grids differ in measure count or meter metadata.",
            "Use Ins as the output grid; retain Vocal event and chord absolute onsets and durations.",
        )

    def _read_header(self, lines: list[str]) -> tuple[dict[str, str], int]:
        """Read required global score fields through the initial K: field."""
        headers: dict[str, str] = {}
        header_end: int | None = None
        for index, line in enumerate(lines):
            match = FIELD_PATTERN.fullmatch(line)
            if not match:
                continue
            key, value = match.groups()
            if key in ("M", "L", "Q", "K"):
                headers[key] = value
                if key == "K":
                    header_end = index
                    break
        missing = [name for name in ("M", "L", "Q", "K") if name not in headers]
        if header_end is None or missing:
            raise ScoreError(f"ABC header is missing required fields: {', '.join(missing or ['K'])}.")
        return headers, header_end

    def _parse_note_length(self, value: str) -> Fraction:
        """Read the score's default note unit from an L: field."""
        match = NOTE_LENGTH_PATTERN.fullmatch(value.strip())
        if not match:
            raise ScoreError(f"Unsupported ABC note length {value!r}.")
        return Fraction(1, _parse_bounded_int(match.group(1), "note length denominator"))

    def _resolve_voice(self, token: str, line_index: int) -> str:
        """Map a source voice label to Vocal or Ins, recording safe aliases."""
        normalized = token.lower()
        if normalized == "vocal":
            voice = "vocal"
        elif normalized == "ins":
            voice = "ins"
        elif normalized in VOICE_ALIASES:
            voice = VOICE_ALIASES[normalized]
        else:
            raise ScoreError(f"Unsupported voice {token!r} at line {line_index + 1}.")
        if normalized != voice:
            self._add_diagnostic(
                "warning",
                voice,
                None,
                f"line {line_index + 1}",
                f"Voice label {token!r} is not canonical.",
                f"Use V: {voice.title()} when serializing the score.",
            )
        return voice

    def _apply_body_field(
        self,
        field_name: str,
        value: str,
        voice: VoiceScore,
        line_index: int,
    ) -> None:
        """Apply a voice-local meter, note length, key, or tempo change."""
        location = f"line {line_index + 1}"
        if field_name == "M":
            repaired_value = value.strip()
            match = re.match(r"\s*(\d+\s*/\s*\d+)", repaired_value)
            if match:
                repaired_value = match.group(1)
                if repaired_value != value.strip():
                    self._add_diagnostic(
                        "warning",
                        voice.name,
                        len(voice.measures) + 1,
                        location,
                        f"Malformed meter field {value!r} contains a valid meter prefix.",
                        f"Use M:{repaired_value}.",
                    )
                voice.meter = _parse_meter(repaired_value)
                return
            if self.repair_mode == "extended":
                if voice.name == "ins":
                    raise ScoreError(
                        f"Invalid Ins meter field {value!r} at {location}; the instrumental grid is ambiguous."
                    )
                self._add_diagnostic(
                    "warning",
                    voice.name,
                    len(voice.measures) + 1,
                    location,
                    f"Invalid meter field {value!r}.",
                    f"Keep the previous meter {voice.meter[0]}/{voice.meter[1]}.",
                )
                return
            raise ScoreError(f"Unsupported meter change {value!r} at {location}.")
        if field_name == "L":
            try:
                voice.note_length = self._parse_note_length(value)
            except ScoreError:
                if self.repair_mode != "extended":
                    raise
                if voice.name == "ins":
                    raise ScoreError(
                        f"Unsupported Ins note length change {value!r} at {location}; "
                        "instrumental note durations cannot be inferred safely."
                    )
                self._add_diagnostic(
                    "warning",
                    voice.name,
                    len(voice.measures) + 1,
                    location,
                    f"Unsupported note length change {value!r}.",
                    f"Keep the previous L: unit 1/{voice.note_length.denominator}.",
                )
            return
        if field_name == "K":
            try:
                _parse_key_signature(value)
                voice.key = value.strip()
            except ScoreError:
                if self.repair_mode != "extended":
                    raise
                if voice.name == "ins":
                    peer = self.partial_state.voices.get("vocal")
                    peer_key = peer.key if peer and peer.cursor == voice.cursor else None
                    if peer_key is None:
                        raise ScoreError(
                            f"Unsupported Ins key change {value!r} at {location}; "
                            "instrumental pitches cannot be inferred safely."
                        )
                    voice.key = peer_key
                    self._add_diagnostic(
                        "warning",
                        voice.name,
                        len(voice.measures) + 1,
                        location,
                        f"Damaged Ins key {value!r} at the same onset as Vocal key {peer_key!r}.",
                        f"Use the aligned Vocal key {peer_key!r}; pitches remain tied to the shared key timeline.",
                    )
                    return
                self._add_diagnostic(
                    "warning",
                    voice.name,
                    len(voice.measures) + 1,
                    location,
                    f"Unsupported key change {value!r}.",
                    f"Keep the previous key {voice.key}.",
                )
            return
        try:
            tempo = _parse_tempo(value)
        except ScoreError:
            if self.repair_mode != "extended":
                raise
            if voice.name == "ins":
                raise ScoreError(
                    f"Unsupported Ins tempo change {value!r} at {location}; "
                    "the score timeline cannot be calculated safely."
                )
            previous = max(
                (change for change in self.tempo_changes if change.onset <= voice.cursor),
                key=lambda change: change.onset,
            )
            previous_unit = (
                f"{previous.beat_unit.numerator}/{previous.beat_unit.denominator}"
            )
            self._add_diagnostic(
                "warning",
                voice.name,
                len(voice.measures) + 1,
                location,
                f"Unsupported tempo change {value!r}.",
                f"Keep the previous Q:{previous_unit}={previous.bpm:g}.",
            )
            return
        tempo.onset = voice.cursor
        tempo.voice = voice.name
        self.tempo_changes.append(tempo)

    def _consume_body_fields(self, line_index: int, line: str, voice: VoiceScore) -> None:
        """Parse a body metadata prefix and preserve any merged music or trailing text."""
        match = BODY_FIELD_PREFIX_PATTERN.match(line)
        if match is None:
            raise ScoreError(f"Invalid body metadata at line {line_index + 1}.")
        field_name, raw_value = match.groups()
        value = raw_value.strip()
        prefix_match: re.Match[str] | None = None
        if field_name == "M":
            prefix_match = re.match(r"\d+\s*/\s*\d+", value)
        elif field_name == "L":
            prefix_match = re.match(r"1\s*/\s*\d+", value)
        elif field_name == "Q":
            prefix_match = TEMPO_PATTERN.match(value)
        elif field_name == "K":
            key_match = re.match(r"[A-G](?:##|bb|#|b)?m?", value)
            if key_match is not None:
                suffix = value[key_match.end():]
                if not suffix or suffix.startswith((
                    '"', "'", ",", "^", "_", "=", "z", "Z", "#"
                )):
                    prefix_match = key_match
        if prefix_match is None:
            self._apply_body_field(field_name, value, voice, line_index)
            return

        field_value = prefix_match.group().strip()
        suffix = value[prefix_match.end():].strip()
        self._apply_body_field(field_name, field_value, voice, line_index)
        if not suffix:
            return
        if field_name == "K" and suffix.startswith("#"):
            suffix = suffix[1:].strip()
            self._add_diagnostic(
                "warning",
                voice.name,
                len(voice.measures) + 1,
                f"line {line_index + 1}",
                f"Key field has a duplicated trailing sharp before {suffix[:16]!r}.",
                f"Use K:{field_value} and continue parsing the suffix from its first music token.",
            )
            if not suffix:
                return
        if self._is_music_suffix(suffix):
            self._add_diagnostic(
                "warning",
                voice.name,
                len(voice.measures) + 1,
                f"line {line_index + 1}",
                f"ABC music tokens are attached to the {field_name}: metadata field.",
                "Split the valid metadata prefix and parse the remaining music tokens without changing them.",
                code="merged_metadata_music",
            )
            self._parse_music_line(line_index, suffix, voice)
            return
        self.metadata_trailing_comments[line_index] = suffix
        self._add_diagnostic(
            "warning",
            voice.name,
            len(voice.measures) + 1,
            f"line {line_index + 1}",
            f"Trailing text {suffix!r} is attached to a {field_name}: metadata field.",
            "Keep the valid metadata prefix and preserve the trailing text as a comment.",
        )

    def _is_music_suffix(self, suffix: str) -> bool:
        """Return whether a metadata suffix is a complete tokenizable music fragment."""
        measures, _, terminated = self._split_music_measures(suffix)
        if not measures:
            return False
        if not terminated and not measures[-1]:
            return False
        return all(
            token.kind != "unknown"
            for measure in measures
            for token in self._tokenize_measure(measure)
        )

    def _parse_music_line(self, line_index: int, line: str, voice: VoiceScore) -> None:
        """Tokenize barline variants and parse measures from one source line."""
        code, separator, comment = line.partition("%")
        code = code.strip()
        if not code:
            return
        raw_measures, markers, terminated = self._split_music_measures(code)
        if not terminated and self.repair_mode == "strict":
            raise ScoreError(f"Music line {line_index + 1} does not end with a bar marker.")
        if not terminated:
            self._add_diagnostic(
                "warning",
                voice.name,
                None,
                f"line {line_index + 1}",
                "Music line is missing its final bar marker.",
                "Treat the remaining content as a complete measure.",
            )
        if any(marker != "|" for marker in markers):
            if self.repair_mode == "strict":
                raise ScoreError(f"Unsupported repeat or alternate barline at line {line_index + 1}.")
            self._add_diagnostic(
                "warning",
                voice.name,
                None,
                f"line {line_index + 1}",
                "Music line uses repeat or alternate barline notation.",
                "Treat each barline token as a single measure boundary; repeat playback is not modeled.",
            )
        if not raw_measures:
            if self.repair_mode == "strict":
                raise ScoreError(f"Empty ABC measure at line {line_index + 1}.")
            raw_measures = ["Z"]
            self._add_diagnostic(
                "warning",
                voice.name,
                None,
                f"line {line_index + 1}",
                "Music line contains no note tokens.",
                "Represent the empty line as a full-measure rest.",
            )
        measure_indices: list[int] = []
        for raw_measure in raw_measures:
            measure_indices.extend(self._parse_measure(raw_measure, voice, line_index))
        self.line_records.append(
            LineRecord(
                line_index=line_index,
                voice=voice.name,
                measure_indices=measure_indices,
                comment=comment if separator else None,
            )
        )
        self.partial_state.line_records = self.line_records

    def _split_music_measures(self, code: str) -> tuple[list[str], list[str], bool]:
        """Split on barline tokens outside quoted chord labels."""
        measures: list[str] = []
        markers: list[str] = []
        current: list[str] = []
        index = 0
        last_was_marker = False
        while index < len(code):
            char = code[index]
            if char == '"':
                chord = _match_chord_symbol(code, index)
                if chord is not None:
                    current.extend(chord.group())
                    index = chord.end()
                else:
                    current.append(char)
                    index += 1
                last_was_marker = False
                continue
            if char in "|:":
                start = index
                while index < len(code) and code[index] in "|:":
                    index += 1
                marker = code[start:index]
                markers.append(marker)
                content = "".join(current).strip()
                if content:
                    measures.append(content)
                current.clear()
                last_was_marker = True
                continue
            current.append(char)
            if not char.isspace():
                last_was_marker = False
            index += 1
        trailing = "".join(current).strip()
        if trailing:
            measures.append(trailing)
        return measures, markers, last_was_marker

    def _parse_measure(self, text: str, voice: VoiceScore, line_index: int) -> list[int]:
        """Parse one measure or expand an ABC multi-measure rest."""
        multi_rest = BAR_REST_PATTERN.fullmatch(text)
        if multi_rest:
            count = _parse_bounded_int(multi_rest.group(1), "multi-measure rest count") if multi_rest.group(1) else 1
            if count <= 0 or count > MAX_MEASURES:
                raise ScoreError(f"Invalid multi-measure rest {text!r} at line {line_index + 1}.")
            indices: list[int] = []
            for _ in range(count):
                indices.append(self._append_measure(voice, line_index, []))
            return indices

        chord_rest = CHORD_PREFIX_REST_PATTERN.fullmatch(text)
        if chord_rest and all(
            _match_chord_symbol(chord_rest.group("chords"), match.start()) is not None
            for match in CHORD_PATTERN.finditer(chord_rest.group("chords"))
        ):
            self._record_chord_quality(chord_rest.group("chords"), voice.name, line_index)
            events = [
                self._new_event(
                    kind="chord",
                    onset=voice.cursor,
                    duration=Fraction(0),
                    voice=voice.name,
                    measure_index=len(voice.measures),
                    line_number=line_index + 1,
                    chord=match.group(),
                )
                for match in CHORD_PATTERN.finditer(chord_rest.group("chords"))
            ]
            return [self._append_measure(voice, line_index, events)]

        width = _measure_duration(voice.meter)
        start = voice.cursor
        tokens = self._tokenize_measure(text)
        rest_suffix = BAR_REST_PATTERN.fullmatch(tokens[-1].raw) if tokens else None
        if (
            self.repair_mode == "extended"
            and len(tokens) > 1
            and tokens[-1].kind == "note"
            and rest_suffix is not None
        ):
            prefix = tokens[:-1]
            rest_count = (
                _parse_bounded_int(rest_suffix.group(1), "multi-measure rest count")
                if rest_suffix.group(1)
                else 1
            )
            if rest_count > MAX_MEASURES:
                raise ScoreError(f"Invalid multi-measure rest {tokens[-1].raw!r} at line {line_index + 1}.")
            prefix_candidates = self._generate_token_candidates(prefix, voice, line_index)
            split_candidates: list[tuple[RepairCandidate, list[ScoreEvent], Fraction]] = []
            for candidate in prefix_candidates:
                try:
                    events, actual_duration = self._parse_token_candidate(
                        prefix,
                        candidate,
                        voice,
                        line_index,
                        start,
                    )
                except ScoreError:
                    continue
                if actual_duration <= width:
                    split_candidates.append((candidate, events, actual_duration))
            if split_candidates:
                candidate, events, actual_duration = min(
                    split_candidates,
                    key=lambda item: item[0].cost,
                )
                boundary = start + width
                first_events = [event for event in events if event.onset < boundary]
                next_events = [event for event in events if event.onset >= boundary]
                for event in next_events:
                    event.measure_index = len(voice.measures) + 1
                self._fit_measure(first_events, start, width, voice, line_index, actual_duration)
                first_index = self._append_measure(voice, line_index, first_events)
                indices = [first_index]
                if next_events:
                    indices.append(self._append_measure(voice, line_index, next_events))
                for _ in range(rest_count):
                    indices.append(self._append_measure(voice, line_index, []))
                for diagnostic in candidate.diagnostics:
                    self._add_diagnostic(
                        diagnostic.severity,
                        diagnostic.voice,
                        diagnostic.measure,
                        diagnostic.location,
                        diagnostic.problem,
                        diagnostic.repair,
                        diagnostic.code,
                    )
                self._add_diagnostic(
                    "warning",
                    voice.name,
                    first_index + 1,
                    f"line {line_index + 1}",
                    f"Uppercase {tokens[-1].raw} rest follows other tokens without a measure boundary.",
                    "Insert a barline before the multi-measure rest, pad the preceding bar safely, "
                    "and preserve pitched events.",
                )
                self.partial_state.measure_events = first_events + next_events
                return indices
        candidates = self._generate_token_candidates(tokens, voice, line_index)
        if not candidates:
            invalid = next((token for token in tokens if token.kind == "unknown"), None)
            if invalid is None:
                raise ScoreError(f"Unable to tokenize ABC measure at line {line_index + 1}.")
            self.candidate_log.append(
                {
                    "type": "token_recovery",
                    "voice": voice.name,
                    "line_number": line_index + 1,
                    "source_tokens": [token.raw for token in tokens],
                    "status": "failed",
                    "reason": "No bounded token repair matched the supported ABC grammar.",
                    "alternatives": [],
                }
            )
            prefix = tokens[: tokens.index(invalid)]
            if prefix:
                partial_candidate = RepairCandidate(
                    "partial_parse",
                    (),
                    (),
                    (0, 0, 0, 0, 0, 0),
                    (),
                )
                self.partial_state.measure_events, _ = self._parse_token_candidate(
                    prefix,
                    partial_candidate,
                    voice,
                    line_index,
                    voice.cursor,
                )
            chord_text = _match_chord_symbol(text, invalid.start) if invalid.raw == '"' else None
            broad_chord = CHORD_PATTERN.match(text, invalid.start) if invalid.raw == '"' else None
            if invalid.raw == '"' and broad_chord is not None and chord_text is None:
                problem = f"Unsupported chord symbol {broad_chord.group()!r} at line {line_index + 1}."
            elif invalid.raw == '"':
                problem = f"Unterminated chord symbol at line {line_index + 1}."
            else:
                problem = f"Unsupported ABC notation at line {line_index + 1}, near {invalid.raw[:24]!r}."
            self._add_diagnostic(
                "error",
                voice.name,
                len(voice.measures) + 1,
                f"line {line_index + 1}",
                problem,
                "No candidate met the token grammar and event-preservation constraints.",
            )
            if invalid.raw == '"':
                raise ScoreError(problem)
            raise ScoreError(problem)

        parsed_candidates: list[tuple[RepairCandidate, list[ScoreEvent], Fraction]] = []
        candidate_errors: list[tuple[RepairCandidate, str]] = []
        for candidate in sorted(candidates, key=lambda item: item.cost):
            try:
                events, actual_duration = self._parse_token_candidate(
                    tokens,
                    candidate,
                    voice,
                    line_index,
                    start,
                )
                parsed_candidates.append((candidate, events, actual_duration))
            except ScoreError as exc:
                candidate_errors.append((candidate, str(exc)))
        if not parsed_candidates:
            self.candidate_log.append(
                {
                    "type": "token_recovery",
                    "voice": voice.name,
                    "line_number": line_index + 1,
                    "source_tokens": [token.raw for token in tokens],
                    "status": "failed",
                    "reason": "Every generated token candidate failed event parsing.",
                    "alternatives": [
                        {
                            "action": item.action,
                            "cost": list(item.cost),
                            "status": "rejected",
                            "reason": reason,
                            "diagnostics": [diagnostic.to_dict() for diagnostic in item.diagnostics],
                        }
                        for item, reason in candidate_errors
                    ],
                }
            )
            if candidate_errors:
                raise ScoreError(candidate_errors[0][1])
            raise ScoreError(f"No valid event candidate for measure at line {line_index + 1}.")

        candidate, events, actual_duration = min(
            parsed_candidates,
            key=lambda item: item[0].cost,
        )
        logged_candidates = [
            {
                "action": item.action,
                "cost": list(item.cost),
                "status": "accepted",
                "selected": item is candidate,
                "duration": str(duration),
                "diagnostics": [diagnostic.to_dict() for diagnostic in item.diagnostics],
                "onset_overrides": [[order, str(onset)] for order, onset in item.onset_overrides],
                "duration_overrides": [[order, str(value)] for order, value in item.duration_overrides],
                "note_token_overrides": [list(value) for value in item.note_token_overrides],
            }
            for item, _, duration in parsed_candidates
            if item.action != "exact_tokens" or item.diagnostics
        ]
        logged_candidates.extend(
            {
                "action": item.action,
                "cost": list(item.cost),
                "status": "rejected",
                "selected": False,
                "reason": reason,
                "diagnostics": [diagnostic.to_dict() for diagnostic in item.diagnostics],
            }
            for item, reason in candidate_errors
            if item.action != "exact_tokens" or item.diagnostics
        )
        if logged_candidates:
            self.candidate_log.append(
                {
                    "type": "token_recovery",
                    "voice": voice.name,
                    "line_number": line_index + 1,
                    "source_tokens": [token.raw for token in tokens],
                    "status": "selected",
                    "selected_action": candidate.action,
                    "alternatives": logged_candidates,
                }
            )
        self.partial_state.measure_events = events
        self._fit_measure(events, start, width, voice, line_index, actual_duration)
        for diagnostic in candidate.diagnostics:
            self._add_diagnostic(
                diagnostic.severity,
                diagnostic.voice,
                diagnostic.measure,
                diagnostic.location,
                diagnostic.problem,
                diagnostic.repair,
                diagnostic.code,
            )
        return [self._append_measure(voice, line_index, events)]

    def _tokenize_measure(self, text: str) -> list[ABCToken]:
        """Lex chord, note, and unknown tokens while retaining source offsets."""
        tokens: list[ABCToken] = []
        cursor = 0
        while cursor < len(text):
            if text[cursor].isspace():
                cursor += 1
                continue
            if text[cursor] == '"':
                chord = _match_chord_symbol(text, cursor)
                if chord is not None:
                    tokens.append(ABCToken("chord", chord.group(), cursor, chord.end()))
                    cursor = chord.end()
                    continue
                quoted = CHORD_PATTERN.match(text, cursor)
                if quoted is not None and quoted.group() == '""':
                    tokens.append(ABCToken("unknown", quoted.group(), cursor, quoted.end()))
                    cursor = quoted.end()
                    continue
                if quoted is not None and NOTE_PATTERN.fullmatch(quoted.group()[1:-1]):
                    tokens.append(ABCToken("unknown", quoted.group(), cursor, quoted.end()))
                    cursor = quoted.end()
                    continue
                tokens.append(ABCToken("unknown", '"', cursor, cursor + 1))
                cursor += 1
                continue
            previous_token = tokens[-1] if tokens else None
            previous_is_quoted_note = (
                previous_token is not None
                and previous_token.kind == "unknown"
                and len(previous_token.raw) >= 3
                and previous_token.raw.startswith('"')
                and previous_token.raw.endswith('"')
                and NOTE_PATTERN.fullmatch(previous_token.raw[1:-1]) is not None
            )
            if (
                text[cursor] in "ABCDEFG"
                and cursor > 0
                and text[cursor - 1] == '"'
                and previous_is_quoted_note
            ):
                closing_quote = text.find('"', cursor)
                if closing_quote > cursor:
                    chord_body = text[cursor:closing_quote]
                    wrapped_chord = f'"{chord_body}"'
                    if (
                        len(chord_body) > 1
                        and not _is_note_sequence(chord_body)
                        and _match_chord_symbol(wrapped_chord) is not None
                    ):
                        tokens.append(
                            ABCToken(
                                "chord",
                                wrapped_chord,
                                cursor,
                                closing_quote + 1,
                                "Insert a missing opening quote around the chord symbol; "
                                "preserve its text and event position.",
                            )
                        )
                        cursor = closing_quote + 1
                        continue
            note = NOTE_PATTERN.match(text, cursor)
            if note is not None:
                tokens.append(ABCToken("note", note.group(), cursor, note.end()))
                cursor = note.end()
                continue
            next_cursor = cursor + 1
            while next_cursor < len(text):
                if text[next_cursor] == '"' or NOTE_PATTERN.match(text, next_cursor):
                    break
                next_cursor += 1
            tokens.append(ABCToken("unknown", text[cursor:next_cursor], cursor, next_cursor))
            cursor = next_cursor
        return tokens

    def _generate_token_candidates(
        self,
        tokens: list[ABCToken],
        voice: VoiceScore,
        line_index: int,
    ) -> list[RepairCandidate]:
        """Generate a bounded set of context-checked token recovery proposals."""
        unknown_indices = [index for index, token in enumerate(tokens) if token.kind == "unknown"]
        lexical_repairs = [
            Diagnostic(
                "warning",
                voice.name,
                len(voice.measures) + 1,
                f"line {line_index + 1}",
                "A chord symbol is missing its opening quote after a quoted token.",
                token.repair,
                "missing_chord_opening_quote",
            )
            for token in tokens
            if token.repair
        ]
        if not unknown_indices:
            if not lexical_repairs:
                return [RepairCandidate("exact_tokens", (), (), (0, 0, 0, 0, 0, 0), ())]
            return [
                RepairCandidate(
                    "token_repair",
                    (),
                    (),
                    (0, 0, 0, 0, 0, len(lexical_repairs)),
                    tuple(lexical_repairs),
                )
            ]
        if self.repair_mode != "extended":
            return []

        ignored: list[int] = []
        note_overrides: list[tuple[int, str]] = []
        diagnostics: list[Diagnostic] = list(lexical_repairs)
        cost = [0, 0, 0, 0, 0, 0]
        cost[5] += len(lexical_repairs)
        for index in unknown_indices:
            token = tokens[index]
            if token.raw == '""':
                ignored.append(index)
                cost[3] += 1
                cost[5] += 1
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        voice.name,
                        len(voice.measures) + 1,
                        f"line {line_index + 1}",
                        "Empty quoted chord token has no chord content.",
                        "Drop the empty chord label; no note or timing event changes.",
                    )
                )
                continue
            if token.raw in ("[", "]") and index in (0, len(tokens) - 1):
                ignored.append(index)
                cost[2] += 1
                cost[5] += 1
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        voice.name,
                        len(voice.measures) + 1,
                        f"line {line_index + 1}",
                        f"Unmatched ABC ending marker {token.raw!r}.",
                        "Drop the unmatched ending marker; note and chord events remain unchanged.",
                    )
                )
                continue
            if (
                len(token.raw) >= 3
                and token.raw.startswith('"')
                and token.raw.endswith('"')
                and NOTE_PATTERN.fullmatch(token.raw[1:-1]) is not None
            ):
                note_overrides.append((index, token.raw[1:-1]))
                cost[5] += 1
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        voice.name,
                        len(voice.measures) + 1,
                        f"line {line_index + 1}",
                        "A quoted token matches one complete ABC note and is not a valid chord symbol.",
                        "Remove the quote delimiters at token level; preserve the note pitch, duration, and tie.",
                    )
                )
                continue
            if (
                token.raw == '"'
                and index + 1 < len(tokens)
                and tokens[index + 1].kind == "note"
                and tokens[index + 1].raw[:1].islower()
                and len(tokens[index + 1].raw) > 1
            ):
                ignored.append(index)
                cost[3] += 1
                cost[5] += 1
                diagnostics.append(
                    Diagnostic(
                        "warning",
                        voice.name,
                        len(voice.measures) + 1,
                        f"line {line_index + 1}",
                        "Unmatched opening quote precedes a lowercase ABC note token.",
                        "Drop the orphan quote as a token-level repair; no note pitch or duration is inferred.",
                    )
                )
                continue
            return []
        return [
            RepairCandidate(
                action="token_repair",
                onset_overrides=(),
                duration_overrides=(),
                cost=tuple(cost),
                diagnostics=tuple(diagnostics),
                ignored_tokens=tuple(ignored),
                note_token_overrides=tuple(note_overrides),
            )
        ]

    def _parse_token_candidate(
        self,
        tokens: list[ABCToken],
        candidate: RepairCandidate,
        voice: VoiceScore,
        line_index: int,
        start: Fraction,
    ) -> tuple[list[ScoreEvent], Fraction]:
        """Convert one candidate token stream into exact timeline events."""
        events: list[ScoreEvent] = []
        offset = Fraction(0)
        measure_accidentals: dict[str, int] = {}
        accidental_bar = 0
        measure_width = _measure_duration(voice.meter)
        ignored = set(candidate.ignored_tokens)
        note_overrides = dict(candidate.note_token_overrides)
        for index, token in enumerate(tokens):
            if index in ignored:
                continue
            if index in note_overrides:
                token_kind = "note"
                token_raw = note_overrides[index]
            else:
                token_kind = token.kind
                token_raw = token.raw
            if token_kind == "chord":
                self._record_chord_quality(token.raw, voice.name, line_index)
                events.append(
                    self._new_event(
                        kind="chord",
                        onset=start + offset,
                        duration=Fraction(0),
                        voice=voice.name,
                        measure_index=len(voice.measures),
                        line_number=line_index + 1,
                        chord=token.raw,
                    )
                )
                self.partial_state.measure_events = events
                continue
            if token_kind == "unknown":
                raise ScoreError(f"Unsupported ABC notation near {token_raw[:24]!r}.")
            match = NOTE_PATTERN.fullmatch(token_raw)
            if match is None:
                raise ScoreError(f"Invalid note token {token_raw!r} at line {line_index + 1}.")
            note, accidental, octave_marks, length_text, tied_after = (
                match.group("note"),
                match.group("acc"),
                match.group("oct").replace(" ", ""),
                match.group("length"),
                bool(match.group("tie")),
            )
            if note in ("Z",):
                raise ScoreError(f"Uppercase Z rest must fill a complete measure at line {line_index + 1}.")
            dot_count = len(match.group("dots"))
            dot_multiplier = Fraction((2 ** (dot_count + 1)) - 1, 2**dot_count)
            duration = voice.note_length * _parse_fraction(length_text) * dot_multiplier
            if duration <= 0:
                raise ScoreError(f"ABC note duration must be positive at line {line_index + 1}.")
            if note.lower() == "z":
                if accidental or octave_marks or tied_after:
                    raise ScoreError(f"Invalid rest token at line {line_index + 1}.")
                event = self._new_event(
                    kind="rest",
                    onset=start + offset,
                    duration=duration,
                    voice=voice.name,
                    measure_index=len(voice.measures),
                    line_number=line_index + 1,
                )
            else:
                current_bar = int(offset // measure_width)
                if current_bar != accidental_bar:
                    measure_accidentals.clear()
                    accidental_bar = current_bar
                pitch, _ = _pitch_from_token(
                    note,
                    octave_marks,
                    accidental,
                    _parse_key_signature(voice.key),
                    measure_accidentals,
                )
                event = self._new_event(
                    kind="note",
                    onset=start + offset,
                    duration=duration,
                    voice=voice.name,
                    measure_index=len(voice.measures),
                    line_number=line_index + 1,
                    pitch=pitch,
                    spelling=note + octave_marks,
                    accidental=accidental,
                    tied_after=tied_after,
                )
            events.append(event)
            offset += duration
            self.partial_state.measure_events = events
        return events, offset

    def _record_chord_quality(self, chord: str, voice_name: str, line_index: int) -> None:
        """Record unsupported but lexically preserved chord qualities once per label."""
        if chord in self.unsupported_chord_labels or _chord_quality_supported(chord):
            return
        self.unsupported_chord_labels.add(chord)
        self._add_diagnostic(
            "warning",
            voice_name,
            len(self.partial_state.voices[voice_name].measures) + 1,
            f"line {line_index + 1}",
            f"Chord label {chord!r} is lexically valid but its quality is not supported by YuE.",
            "Preserve the opaque chord label unchanged; do not infer a substitute chord.",
            code="unsupported_chord_quality",
        )

    def _fit_measure(
        self,
        events: list[ScoreEvent],
        start: Fraction,
        width: Fraction,
        voice: VoiceScore,
        line_index: int,
        actual_duration: Fraction,
    ) -> None:
        """Pad a short measure and defer extended overflows to candidate validation."""
        difference = width - actual_duration
        measure_number = len(voice.measures) + 1
        location = f"line {line_index + 1}"
        if difference == 0:
            return
        if difference > 0:
            if self.repair_mode == "strict":
                raise ScoreError(
                    f"Measure {measure_number} in {voice.name} is short by {difference} whole-note units."
                )
            trailing_event = next(
                (
                    event
                    for event in reversed(events)
                    if event.kind in ("note", "rest") and event.duration > 0
                ),
                None,
            )
            ends_with_tie = (
                trailing_event is not None
                and trailing_event.kind == "note"
                and trailing_event.tied_after
            )
            self.short_measures.append(
                ShortMeasureObservation(
                    voice.name,
                    len(voice.measures),
                    start,
                    actual_duration,
                    width,
                    ends_with_tie,
                )
            )
            if ends_with_tie and self.repair_mode == "extended":
                self._add_diagnostic(
                    "warning",
                    voice.name,
                    measure_number,
                    location,
                    f"Measure is short by {difference} whole-note units and ends with a tie.",
                    "Defer padding until aligned Vocal and Ins measures can be checked for a missing meter change.",
                )
                return
            if ends_with_tie:
                raise ScoreError(
                    f"Measure {measure_number} in {voice.name} is short by {difference} whole-note units "
                    "and ends with a tie; padding would break the tie and shift the next onset."
                )
            self._add_diagnostic(
                "warning",
                voice.name,
                measure_number,
                location,
                f"Measure is short by {difference} whole-note units.",
                "Append a rest to complete the meter.",
            )
            events.append(
                self._new_event(
                    kind="rest",
                    onset=start + actual_duration,
                    duration=difference,
                    voice=voice.name,
                    measure_index=measure_number - 1,
                    line_number=line_index + 1,
                )
            )
            return
        if self.repair_mode == "strict":
            raise ScoreError(
                f"Measure {measure_number} in {voice.name} exceeds its meter by {abs(difference)} whole-note units."
            )
        if self.repair_mode == "extended":
            self.pending_overflows.append(
                (voice.name, len(voice.measures), abs(difference))
            )
            return
        excess_in_l_units = abs(difference) / voice.note_length
        repairable = self._trim_trailing_rests(events, abs(difference))
        if not repairable:
            raise ScoreError(
                f"Measure {measure_number} in {voice.name} exceeds its meter by {excess_in_l_units} L: units; "
                "no trailing rest can be safely trimmed."
            )
        self._add_diagnostic(
            "warning",
            voice.name,
            measure_number,
            location,
            f"Measure exceeds its meter by {excess_in_l_units} L: units.",
            "Trim only trailing rest duration; preserve all pitched events.",
        )

    def _resolve_overfull_measures(self, voices: dict[str, VoiceScore]) -> None:
        """Score bounded event-level proposals for every overfull measure."""
        for voice_name, measure_index, excess in self.pending_overflows:
            voice = voices[voice_name]
            measure = voice.measures[measure_index]
            boundary = measure.onset + measure.duration
            measure_number = measure_index + 1
            crossing_rests = [
                event
                for event in measure.events
                if event.kind == "rest" and event.onset + event.duration > boundary
            ]
            crossing_notes = [
                event
                for event in measure.events
                if event.kind == "note" and event.onset + event.duration > boundary
            ]
            for event in crossing_rests:
                event.duration = max(Fraction(0), boundary - event.onset)
            voice.events = [
                event
                for event in voice.events
                if event.kind != "rest" or event.duration > 0
            ]
            if not crossing_notes:
                self._add_diagnostic(
                    "warning",
                    voice_name,
                    measure_number,
                    f"line {measure.line_number}",
                    f"Measure duration exceeds its meter by {excess} whole-note units.",
                    "Clip excess trailing rest duration at the barline; pitched events are unchanged.",
                )
                continue

            for event in crossing_notes:
                end = event.onset + event.duration
                width = measure.duration
                if end - boundary > width * MAX_REPAIR_LOOKAHEAD_MEASURES:
                    raise ScoreError(
                        f"Measure {measure_number} in {voice_name} overruns by more than "
                        f"{MAX_REPAIR_LOOKAHEAD_MEASURES} measures."
                    )

            candidate = self._choose_overflow_candidate(
                voice,
                measure_index,
                voices["ins"].events,
            )
            if candidate is None:
                raise ScoreError(
                    f"Measure {measure_number} in {voice_name} has an overhanging note that overlaps "
                    "another note; no candidate preserves Ins and the monophonic timeline."
                )
            overrides = dict(candidate.duration_overrides)
            onset_overrides = dict(candidate.onset_overrides)
            for event in voice.events:
                if event.source_order in overrides:
                    event.duration = overrides[event.source_order]
                if event.source_order in onset_overrides:
                    event.onset = onset_overrides[event.source_order]
            if candidate.action == "preserve_overhang":
                repair = "Retain note pitch, onset, and duration; serialize the overhang with bar-crossing ties."
            else:
                repair = candidate.action
            for item in candidate.diagnostics:
                self.diagnostics.append(item)
            self._add_diagnostic(
                "warning",
                voice_name,
                measure_number,
                f"line {measure.line_number}",
                f"Measure duration exceeds its meter by {excess} whole-note units.",
                repair,
            )

    def _check_short_measure_hypotheses(self) -> None:
        """Find aligned short-bar runs before treating their deficit as a rest."""
        by_voice = {
            voice_name: {item.measure_index: item for item in self.short_measures if item.voice == voice_name}
            for voice_name in ("vocal", "ins")
        }
        for vocal in self.short_measures:
            if vocal.voice != "vocal":
                continue
            ins = next(
                (
                    item
                    for item in by_voice["ins"].values()
                    if item.onset == vocal.onset
                    and item.actual_duration == vocal.actual_duration
                    and item.expected_duration == vocal.expected_duration
                ),
                None,
            )
            if ins is None:
                continue
            run_length = 1
            vocal_next = vocal
            ins_next = ins
            while True:
                next_vocal = by_voice["vocal"].get(vocal_next.measure_index + 1)
                next_ins = by_voice["ins"].get(ins_next.measure_index + 1)
                if (
                    next_vocal is None
                    or next_ins is None
                    or next_vocal.onset != vocal_next.onset + vocal_next.expected_duration
                    or next_ins.onset != ins_next.onset + ins_next.expected_duration
                    or next_vocal.actual_duration != vocal.actual_duration
                    or next_ins.actual_duration != ins.actual_duration
                    or next_vocal.expected_duration != vocal.expected_duration
                    or next_ins.expected_duration != ins.expected_duration
                ):
                    break
                vocal_next = next_vocal
                ins_next = next_ins
                run_length += 1
            if run_length < 2:
                continue
            self._add_diagnostic(
                "warning",
                "score",
                None,
                f"timeline {vocal.onset}",
                f"Vocal and Ins each contain {run_length} aligned short measures of "
                f"{vocal.actual_duration} whole-note units.",
                "Treat this as a possible missing meter change; refuse to pad because the meter grouping is ambiguous.",
                code="possible_missing_meter_change",
            )
            raise ScoreError(
                "Aligned short measures in Vocal and Ins suggest a missing meter change; "
                "the time-signature grouping is ambiguous, so no rest was added."
            )

        tied_observation = next(
            (item for item in self.short_measures if item.ends_with_tie),
            None,
        )
        if tied_observation is not None:
            measure_number = tied_observation.measure_index + 1
            difference = tied_observation.expected_duration - tied_observation.actual_duration
            raise ScoreError(
                f"Measure {measure_number} in {tied_observation.voice} is short by {difference} whole-note units "
                "and ends with a tie; no aligned Vocal and Ins meter pattern resolves the missing duration, "
                "so padding would break the tie."
            )

    def _choose_overflow_candidate(
        self,
        voice: VoiceScore,
        measure_index: int,
        ins_events: list[ScoreEvent],
    ) -> RepairCandidate | None:
        """Use a bounded beam search and rank proposals lexicographically."""
        initial_transfer_cost = -self._transfer_fragment_count(voice.events, ins_events, {}, {})
        rejection_reasons: set[str] = set()
        beam = [
            RepairCandidate(
                action="preserve_overhang",
                onset_overrides=(),
                duration_overrides=(),
                cost=(0, 0, 0, 0, initial_transfer_cost, 0),
                diagnostics=(),
            )
        ]
        completed: list[RepairCandidate] = []
        seen: set[
            tuple[
                tuple[tuple[int, Fraction], ...],
                tuple[tuple[int, Fraction], ...],
            ]
        ] = set()
        for _ in range(MAX_REPAIR_LOOKAHEAD_MEASURES):
            next_beam: list[RepairCandidate] = []
            for candidate in beam:
                onset_overrides = dict(candidate.onset_overrides)
                overrides = dict(candidate.duration_overrides)
                notes = [
                    event
                    for event in voice.events
                    if event.kind == "note"
                    and measure_index <= event.measure_index
                    <= measure_index + MAX_REPAIR_LOOKAHEAD_MEASURES
                ]
                notes.sort(
                    key=lambda event: (
                        onset_overrides.get(event.source_order, event.onset),
                        event.source_order,
                    )
                )
                conflict = next(
                    (
                        (left, right)
                        for left, right in zip(notes, notes[1:])
                        if left.measure_index == measure_index
                        if onset_overrides.get(left.source_order, left.onset)
                        + overrides.get(left.source_order, left.duration)
                        > onset_overrides.get(right.source_order, right.onset)
                    ),
                    None,
                )
                if conflict is None:
                    completed.append(candidate)
                    continue
                if voice.name != "vocal":
                    rejection_reasons.add(
                        "Ins event changes are not proposed under instrumental_priority."
                    )
                    continue
                left, right = conflict
                left_onset = onset_overrides.get(left.source_order, left.onset)
                right_onset = onset_overrides.get(right.source_order, right.onset)
                left_end = left_onset + overrides.get(left.source_order, left.duration)
                overlap = left_end - right_onset
                note_length = voice.measures[left.measure_index].note_length
                measure_gap = right.measure_index - left.measure_index
                if overlap <= 0 or measure_gap > MAX_REPAIR_LOOKAHEAD_MEASURES:
                    continue
                shifted = dict(onset_overrides)
                shift_limit = voice.measures[left.measure_index].duration * MAX_REPAIR_LOOKAHEAD_MEASURES
                shiftable = [
                    event
                    for event in voice.events
                    if onset_overrides.get(event.source_order, event.onset) >= right_onset
                ]
                if shiftable and len(shiftable) <= MAX_REPAIR_SUFFIX_EVENTS and all(
                    onset_overrides.get(event.source_order, event.onset) + overlap - event.onset
                    <= shift_limit
                    for event in shiftable
                ):
                    for event in shiftable:
                        shifted[event.source_order] = (
                            onset_overrides.get(event.source_order, event.onset) + overlap
                        )
                    onset_signature = tuple(sorted(shifted.items()))
                    signature = (onset_signature, tuple(sorted(overrides.items())))
                    if signature not in seen:
                        seen.add(signature)
                        moved_chords = sum(event.kind == "chord" for event in shiftable)
                        next_beam.append(
                            RepairCandidate(
                                action=(
                                    f"Heuristically shift the Vocal event suffix from line {right.line_number} "
                                    f"by {overlap} whole-note units to remove a note overlap."
                                ),
                                onset_overrides=onset_signature,
                                duration_overrides=tuple(sorted(overrides.items())),
                                cost=(
                                    0,
                                    candidate.cost[1] + 1,
                                    candidate.cost[2],
                                    candidate.cost[3] + moved_chords,
                                    -self._transfer_fragment_count(
                                        voice.events,
                                        ins_events,
                                        shifted,
                                        overrides,
                                    ),
                                    candidate.cost[5] + 1,
                                ),
                                diagnostics=(),
                            )
                        )

                trim_limit = self.max_repair_units * note_length
                if overlap <= trim_limit:
                    shortened = dict(overrides)
                    shortened[left.source_order] = right_onset - left_onset
                    onset_signature = tuple(sorted(onset_overrides.items()))
                    signature = (onset_signature, tuple(sorted(shortened.items())))
                    if signature not in seen:
                        seen.add(signature)
                        next_beam.append(
                            RepairCandidate(
                                action=(
                                    f"Heuristically shorten Vocal note at line {left.line_number} by {overlap} "
                                    "whole-note units to the next note onset."
                                ),
                                onset_overrides=onset_signature,
                                duration_overrides=signature[1],
                                cost=(
                                    0,
                                    candidate.cost[1] + 1,
                                    candidate.cost[2] + 1,
                                    candidate.cost[3],
                                    -self._transfer_fragment_count(
                                        voice.events,
                                        ins_events,
                                        onset_overrides,
                                        shortened,
                                    ),
                                    candidate.cost[5] + 1,
                                ),
                                diagnostics=(),
                            )
                        )
                else:
                    rejection_reasons.add(
                        f"Shortening would exceed the {self.max_repair_units} L: unit limit."
                    )
            if completed:
                break
            beam = sorted(next_beam, key=lambda item: item.cost)[:MAX_REPAIR_CANDIDATES]
            if not beam:
                break
        if not completed:
            if not rejection_reasons:
                rejection_reasons.add(
                    "No candidate resolved the overlap within the bounded lookahead and event limits."
                )
            self.candidate_log.append(
                {
                    "type": "overfull_measure",
                    "voice": voice.name,
                    "measure": measure_index + 1,
                    "status": "failed",
                    "reason": "No candidate preserved the monophonic timeline and Ins priority.",
                    "alternatives": [],
                    "rejected_reasons": sorted(rejection_reasons),
                }
            )
            return None

        selected = min(completed, key=lambda item: item.cost)
        alternatives = [
            {
                "action": candidate.action,
                "cost": list(candidate.cost),
                "selected": candidate is selected,
                "onset_overrides": [[order, str(onset)] for order, onset in candidate.onset_overrides],
                "duration_overrides": [[order, str(value)] for order, value in candidate.duration_overrides],
            }
            for candidate in completed
        ]
        if selected.action != "preserve_overhang" or len(completed) > 1:
            self.candidate_log.append(
                {
                    "type": "overfull_measure",
                    "voice": voice.name,
                    "measure": measure_index + 1,
                    "status": "selected",
                    "selected_action": selected.action,
                    "alternatives": alternatives,
                    "rejected_reasons": sorted(rejection_reasons),
                }
            )
        return selected

    def _transfer_fragment_count(
        self,
        vocal_events: list[ScoreEvent],
        ins_events: list[ScoreEvent],
        onset_overrides: dict[int, Fraction],
        duration_overrides: dict[int, Fraction],
    ) -> int:
        """Count free Vocal fragments for the candidate-transfer priority."""
        occupied = _merge_intervals(
            [
                (event.onset, event.onset + event.duration)
                for event in ins_events
                if event.kind == "note"
            ]
        )
        return sum(
            len(
                _subtract_intervals(
                    onset_overrides.get(event.source_order, event.onset),
                    onset_overrides.get(event.source_order, event.onset)
                    + duration_overrides.get(event.source_order, event.duration),
                    occupied,
                )
            )
            for event in vocal_events
            if event.kind == "note"
        )

    def _trim_trailing_rests(self, events: list[ScoreEvent], amount: Fraction) -> bool:
        """Trim only trailing rest duration for a conservative safe repair."""
        remaining = amount
        for event in reversed(events):
            if event.kind == "chord":
                continue
            if event.kind != "rest":
                break
            reduction = min(event.duration, remaining)
            event.duration -= reduction
            remaining -= reduction
            if remaining == 0:
                return True
        return False

    def _append_measure(
        self,
        voice: VoiceScore,
        line_index: int,
        events: list[ScoreEvent],
    ) -> int:
        """Append a normalized measure and advance the voice timeline."""
        measure = Measure(
            onset=voice.cursor,
            duration=_measure_duration(voice.meter),
            meter=voice.meter,
            note_length=voice.note_length,
            key=voice.key,
            line_number=line_index + 1,
            events=events,
        )
        measure_index = len(voice.measures)
        voice.measures.append(measure)
        voice.events.extend(event for event in events if event.duration > 0 or event.kind == "chord")
        voice.cursor += measure.duration
        return measure_index

    def _resolve_ties(self, voice: VoiceScore) -> None:
        """Merge valid tied notes and remove only invalid tie marks in repair modes."""
        sounding = sorted(
            (event for event in voice.events if event.kind in ("note", "rest")),
            key=lambda event: (event.onset, event.source_order),
        )
        resolved: list[ScoreEvent] = []
        for event in sounding:
            if resolved and resolved[-1].tied_after:
                previous = resolved[-1]
                contiguous = previous.onset + previous.duration == event.onset
                both_notes = previous.kind == event.kind == "note"
                same_pitch = both_notes and previous.pitch == event.pitch
                tied_pitch = (
                    both_notes
                    and previous.spelling == event.spelling
                    and event.accidental is None
                )
                if contiguous and (same_pitch or tied_pitch):
                    if tied_pitch and not same_pitch:
                        event.pitch = previous.pitch
                    previous.duration += event.duration
                    previous.tied_after = event.tied_after
                    continue
                if self.repair_mode == "strict":
                    raise ScoreError(
                        f"Invalid tie in {voice.name} at line {previous.line_number}: "
                        "the next event is not the same contiguous pitch."
                    )
                previous.tied_after = False
                self._add_diagnostic(
                    "warning",
                    voice.name,
                    previous.measure_index + 1,
                    f"line {previous.line_number}",
                    "Tie does not continue into the same contiguous pitch.",
                    "Remove the invalid tie marker and preserve both events.",
                )
            resolved.append(event)
        if resolved and resolved[-1].tied_after:
            last = resolved[-1]
            if self.repair_mode == "strict":
                raise ScoreError(f"Unfinished tie at the end of {voice.name}.")
            last.tied_after = False
            self._add_diagnostic(
                "warning",
                voice.name,
                last.measure_index + 1,
                f"line {last.line_number}",
                "Tie has no following note.",
                "Remove the dangling tie marker and preserve the note.",
            )
        voice.events = [event for event in voice.events if event.kind == "chord"] + resolved
        voice.events.sort(key=lambda event: (event.onset, event.source_order))

    def _new_event(
        self,
        kind: Literal["note", "rest", "chord"],
        onset: Fraction,
        duration: Fraction,
        voice: str,
        measure_index: int,
        line_number: int,
        pitch: int | None = None,
        spelling: str = "",
        accidental: str | None = None,
        chord: str = "",
        tied_after: bool = False,
    ) -> ScoreEvent:
        """Create a timeline event with a stable source order."""
        self._event_order += 1
        return ScoreEvent(
            kind=kind,
            onset=onset,
            duration=duration,
            voice=voice,
            measure_index=measure_index,
            line_number=line_number,
            pitch=pitch,
            spelling=spelling,
            accidental=accidental,
            chord=chord,
            tied_after=tied_after,
            source_order=self._event_order,
        )

    def _repair_or_raise(
        self,
        voice: str,
        measure: int | None,
        location: str,
        problem: str,
        repair: str,
    ) -> None:
        """Record an issue in repair modes or reject it in strict mode."""
        if self.repair_mode == "strict":
            raise ScoreError(problem)
        self._add_diagnostic("warning", voice, measure, location, problem, repair)

    def _add_diagnostic(
        self,
        severity: str,
        voice: str,
        measure: int | None,
        location: str,
        problem: str,
        repair: str,
        code: str = "",
    ) -> None:
        """Append one structured parser diagnostic."""
        diagnostic = Diagnostic(severity, voice, measure, location, problem, repair, code)
        self.diagnostics.append(diagnostic)
        self.partial_state.diagnostics = self.diagnostics

    def _check_limits(self, voices: dict[str, VoiceScore]) -> None:
        """Reject unusually large scores before timeline operations allocate more data."""
        measure_count = sum(len(voice.measures) for voice in voices.values())
        event_count = sum(len(voice.events) for voice in voices.values())
        if measure_count > MAX_MEASURES * len(voices):
            raise ScoreError(f"ABC score exceeds the {MAX_MEASURES} measure-per-voice limit.")
        if event_count > MAX_EVENTS:
            raise ScoreError(f"ABC score exceeds the {MAX_EVENTS} event limit.")

    def _validate_voice_grids(self, voices: dict[str, VoiceScore]) -> None:
        """Require Vocal and Ins to share measure timing, meter, and key in strict mode."""
        vocal_grid = [
            (measure.onset, measure.duration, measure.meter, measure.key)
            for measure in voices["vocal"].measures
        ]
        ins_grid = [
            (measure.onset, measure.duration, measure.meter, measure.key)
            for measure in voices["ins"].measures
        ]
        if vocal_grid != ins_grid:
            raise ScoreError("Vocal and Ins do not share the same measure, meter, and key timeline.")

    def _normalized_tempos(self) -> list[TempoChange]:
        """Deduplicate simultaneous tempo declarations and sort them by onset."""
        grouped: dict[Fraction, list[TempoChange]] = {}
        for change in self.tempo_changes:
            grouped.setdefault(change.onset, []).append(change)
        normalized: list[TempoChange] = []
        for onset in sorted(grouped):
            changes = grouped[onset]
            voice_changes = [change for change in changes if change.voice is not None]
            unique_values = {(change.beat_unit, change.bpm) for change in voice_changes}
            if len(unique_values) > 1:
                raise ScoreError(f"Vocal and Ins declare conflicting tempos at whole-note position {onset}.")
            normalized.append((voice_changes or changes)[-1])
        return normalized


def _subtract_intervals(
    start: Fraction,
    end: Fraction,
    occupied: list[tuple[Fraction, Fraction]],
) -> list[tuple[Fraction, Fraction]]:
    """Return free subintervals against sorted, disjoint occupied intervals."""
    if end <= start or not occupied:
        return [(start, end)] if end > start else []
    index = bisect_right(occupied, start, key=lambda interval: interval[0])
    if index and occupied[index - 1][1] > start:
        index -= 1
    pieces: list[tuple[Fraction, Fraction]] = []
    cursor = start
    while index < len(occupied):
        occupied_start, occupied_end = occupied[index]
        index += 1
        if occupied_start >= end:
            break
        if occupied_start > cursor:
            pieces.append((cursor, min(occupied_start, end)))
        cursor = max(cursor, occupied_end)
        if cursor >= end:
            break
    if cursor < end:
        pieces.append((cursor, end))
    return pieces


def _merge_intervals(
    intervals: list[tuple[Fraction, Fraction]],
) -> list[tuple[Fraction, Fraction]]:
    """Merge overlapping or adjacent intervals into a sorted disjoint timeline."""
    merged: list[tuple[Fraction, Fraction]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _assert_monophonic(events: list[ScoreEvent], voice_name: str) -> None:
    """Reject overlapping pitched events that cannot be serialized as one melody."""
    notes = sorted((event for event in events if event.kind == "note"), key=lambda event: event.onset)
    for previous, current in zip(notes, notes[1:]):
        if previous.onset + previous.duration > current.onset:
            raise ScoreError(
                f"{voice_name.title()} contains overlapping pitched notes at "
                f"{previous.onset} and {current.onset}."
            )


def _transfer_melody(
    score: ParsedScore,
    policy: MelodyPolicy,
) -> tuple[list[ScoreEvent], dict[str, int]]:
    """Apply the selected overlap policy and return the final Ins notes."""
    vocal = score.voices["vocal"]
    ins = score.voices["ins"]
    vocal_notes = [event for event in vocal.events if event.kind == "note"]
    ins_notes = [event for event in ins.events if event.kind == "note"]
    _assert_monophonic(ins_notes, "Ins")
    _assert_monophonic(vocal_notes, "Vocal")
    stats = {
        "vocal_notes": len(vocal_notes),
        "fully_transferred": 0,
        "partially_transferred": 0,
        "not_transferred": 0,
        "ins_notes_preserved": len(ins_notes),
        "ins_notes_changed": 0,
        "ins_notes_repaired": 0,
        "ins_changes_unknown": 0,
        "ins_preservation_verified": 0,
    }
    if policy == "mute_vocal_only":
        stats["not_transferred"] = len(vocal_notes)
        return list(ins_notes), stats
    if policy == "instrumental_priority":
        result = list(ins_notes)
        occupied = sorted((note.onset, note.onset + note.duration) for note in ins_notes)
        for note in sorted(vocal_notes, key=lambda event: event.onset):
            pieces = _subtract_intervals(note.onset, note.onset + note.duration, occupied)
            if not pieces:
                stats["not_transferred"] += 1
                continue
            if len(pieces) == 1 and pieces[0] == (note.onset, note.onset + note.duration):
                stats["fully_transferred"] += 1
            else:
                stats["partially_transferred"] += 1
            for start, end in pieces:
                transferred = ScoreEvent(
                    kind="note",
                    onset=start,
                    duration=end - start,
                    voice="ins",
                    measure_index=note.measure_index,
                    line_number=note.line_number,
                    pitch=note.pitch,
                    spelling=note.spelling,
                    accidental=note.accidental,
                    source_order=note.source_order,
                )
                result.append(transferred)
        _assert_monophonic(result, "Ins")
        return sorted(result, key=lambda event: (event.onset, event.source_order)), stats
    if policy != "vocal_priority":
        raise ScoreError(f"Unknown melody policy {policy!r}.")

    vocal_intervals = sorted((note.onset, note.onset + note.duration) for note in vocal_notes)
    result = []
    for note in ins_notes:
        pieces = _subtract_intervals(note.onset, note.onset + note.duration, vocal_intervals)
        if pieces != [(note.onset, note.onset + note.duration)]:
            stats["ins_notes_changed"] += 1
        for start, end in pieces:
            result.append(
                ScoreEvent(
                    kind="note",
                    onset=start,
                    duration=end - start,
                    voice="ins",
                    measure_index=note.measure_index,
                    line_number=note.line_number,
                    pitch=note.pitch,
                    spelling=note.spelling,
                    accidental=note.accidental,
                    source_order=note.source_order,
                )
            )
    for note in vocal_notes:
        result.append(
            ScoreEvent(
                kind="note",
                onset=note.onset,
                duration=note.duration,
                voice="ins",
                measure_index=note.measure_index,
                line_number=note.line_number,
                pitch=note.pitch,
                spelling=note.spelling,
                accidental=note.accidental,
                source_order=note.source_order,
            )
        )
    stats["ins_notes_preserved"] -= stats["ins_notes_changed"]
    _assert_monophonic(result, "Ins")
    stats["fully_transferred"] = len(vocal_notes)
    return sorted(result, key=lambda event: (event.onset, event.source_order)), stats


def _format_length(duration: Fraction, note_length: Fraction) -> str:
    """Format an exact event duration as a YuE2 ABC length multiplier."""
    multiplier = duration / note_length
    if multiplier <= 0:
        raise ScoreError("Cannot serialize a zero or negative ABC duration.")
    if multiplier.denominator == 1:
        return "" if multiplier.numerator == 1 else str(multiplier.numerator)
    if multiplier.numerator == 1:
        return f"/{multiplier.denominator}"
    return f"{multiplier.numerator}/{multiplier.denominator}"


def _upstream_length_unit(
    measure: Measure,
    events: list[ScoreEvent],
    chords: list[ScoreEvent],
) -> Fraction:
    """Choose a binary ABC note unit that exactly represents one measure's boundaries."""
    start = measure.onset
    end = start + measure.duration
    boundaries = {start, end}
    for event in events:
        if event.kind == "note" and event.onset < end and event.onset + event.duration > start:
            boundaries.add(max(start, event.onset))
            boundaries.add(min(end, event.onset + event.duration))
    for chord in chords:
        if start <= chord.onset < end:
            boundaries.add(chord.onset)
    required_denominator = math.lcm(
        *(value.denominator for value in sorted(boundaries))
    )
    current_denominator = measure.note_length.denominator
    required_denominator = math.lcm(required_denominator, current_denominator)
    if required_denominator > MAX_UPSTREAM_NOTE_LENGTH_DENOMINATOR:
        return measure.note_length
    if required_denominator & (required_denominator - 1):
        return measure.note_length
    return Fraction(1, required_denominator)


def _duration_parts(duration: Fraction, note_length: Fraction) -> tuple[int, ...] | None:
    """Split a duration into exact YuE-supported integer ABC multipliers."""
    multiplier = duration / note_length
    if multiplier.denominator != 1 or multiplier.numerator <= 0:
        return None
    remaining = multiplier.numerator
    parts: list[int] = []
    for unit in YU2_SUPPORTED_DURATION_UNITS:
        while remaining >= unit:
            parts.append(unit)
            remaining -= unit
    if remaining:
        raise ScoreError(f"Unable to decompose ABC duration multiplier {multiplier}.")
    return tuple(parts)


def _format_supported_note(
    spelling: str,
    duration: Fraction,
    note_length: Fraction,
    tie_after: bool,
    continuation_spelling: str | None = None,
) -> str:
    """Format a pitched duration, retaining accidental state across tied chunks."""
    parts = _duration_parts(duration, note_length)
    if parts is None:
        return spelling + _format_length(duration, note_length) + ("-" if tie_after else "")
    tokens = []
    for index, part in enumerate(parts):
        suffix = "" if part == 1 else str(part)
        continues = index + 1 < len(parts) or tie_after
        note_spelling = spelling if index == 0 or continuation_spelling is None else continuation_spelling
        tokens.append(f"{note_spelling}{suffix}{'-' if continues else ''}")
    return "".join(tokens)


def _format_supported_rest(duration: Fraction, note_length: Fraction) -> str:
    """Format a rest duration with supported units without adding sound events."""
    parts = _duration_parts(duration, note_length)
    if parts is None:
        return "z" + _format_length(duration, note_length)
    return "".join("z" + ("" if part == 1 else str(part)) for part in parts)


def _serialize_note_spelling(
    note: ScoreEvent,
    measure: Measure,
    measure_accidentals: dict[str, int],
    preserve_source_accidental: bool,
    diagnostics: list[Diagnostic] | None,
) -> tuple[str, str]:
    """Choose the source note spelling and only add accidentals needed for its exact pitch."""
    if note.pitch is None:
        raise ScoreError("A pitched note event has no MIDI pitch.")
    match = re.fullmatch(r"(?P<note>[A-Ga-g])(?P<octave>[' ,]*)", note.spelling)
    if match is None:
        fallback = _pitch_to_token(note.pitch)
        fallback_match = NOTE_PATTERN.fullmatch(fallback)
        if fallback_match is None:
            raise ScoreError(f"Unable to spell MIDI pitch {note.pitch} as ABC.")
        accidental = fallback_match.group("acc") or ""
        base_spelling = fallback_match.group("note") + fallback_match.group("oct")
        measure_accidentals[base_spelling[0].upper()] = {
            "=": 0,
            "^": 1,
            "^^": 2,
            "_": -1,
            "__": -2,
        }.get(accidental, 0)
        if diagnostics is not None:
            diagnostics.append(
                Diagnostic(
                    "warning",
                    note.voice,
                    note.measure_index + 1,
                    f"line {note.line_number}",
                    f"Source spelling {note.spelling!r} cannot represent MIDI pitch {note.pitch}.",
                    f"Use the minimal supported alternative {fallback!r}.",
                    "source_spelling_fallback",
                )
            )
        return fallback, base_spelling

    letter_token = match.group("note")
    letter = letter_token.upper()
    octave_marks = match.group("octave").replace(" ", "")
    natural_pitch = MIDI_C4 + NATURAL_PITCHES[letter]
    if letter_token.islower():
        natural_pitch += SEMITONES_PER_OCTAVE
    natural_pitch += SEMITONES_PER_OCTAVE * (
        octave_marks.count("'") - octave_marks.count(",")
    )
    alteration = note.pitch - natural_pitch
    accidental_symbols = {0: "=", 1: "^", 2: "^^", -1: "_", -2: "__"}
    if alteration not in accidental_symbols:
        fallback = _pitch_to_token(note.pitch)
        fallback_match = NOTE_PATTERN.fullmatch(fallback)
        if fallback_match is None:
            raise ScoreError(f"Unable to spell MIDI pitch {note.pitch} as ABC.")
        fallback_base = fallback_match.group("note") + fallback_match.group("oct").replace(" ", "")
        accidental = fallback_match.group("acc") or ""
        measure_accidentals[fallback_match.group("note").upper()] = {
            "=": 0,
            "^": 1,
            "^^": 2,
            "_": -1,
            "__": -2,
        }.get(accidental, 0)
        if diagnostics is not None:
            diagnostics.append(
                Diagnostic(
                    "warning",
                    note.voice,
                    note.measure_index + 1,
                    f"line {note.line_number}",
                    f"Source spelling {note.spelling!r} requires unsupported alteration {alteration}.",
                    f"Use the minimal supported alternative {fallback!r} for MIDI pitch {note.pitch}.",
                    "source_spelling_fallback",
                )
            )
        return fallback, fallback_base

    key_signature = _parse_key_signature(measure.key)
    current_alteration = measure_accidentals.get(letter, key_signature[letter])
    source_accidental = note.accidental if preserve_source_accidental else None
    source_values = {"=": 0, "^": 1, "^^": 2, "_": -1, "__": -2}
    if source_accidental is not None and source_values.get(source_accidental) == alteration:
        accidental = source_accidental
    elif current_alteration == alteration:
        accidental = ""
    else:
        accidental = accidental_symbols[alteration]
    if accidental:
        measure_accidentals[letter] = source_values[accidental]
    source_spelling = letter_token + octave_marks
    if diagnostics is not None and accidental and accidental != source_accidental:
        diagnostics.append(
            Diagnostic(
                "warning",
                note.voice,
                note.measure_index + 1,
                f"line {note.line_number}",
                f"Added accidental {accidental!r} to keep MIDI pitch {note.pitch} after output ordering "
                "or key changes.",
                f"Retain source spelling {source_spelling!r} and emit the accidental required by the "
                "output context.",
                "source_spelling_accidental_adjusted",
            )
        )
    return accidental + source_spelling, source_spelling


def _render_measure(
    measure: Measure,
    events: list[ScoreEvent],
    chords: list[ScoreEvent],
    spelling_diagnostics: list[Diagnostic] | None = None,
) -> str:
    """Render one measure from absolute events, retaining chord timing."""
    start = measure.onset
    end = start + measure.duration
    notes = [
        event
        for event in events
        if event.kind == "note" and event.onset < end and event.onset + event.duration > start
    ]
    note_boundaries = {start, end}
    for note in notes:
        note_boundaries.add(max(start, note.onset))
        note_boundaries.add(min(end, note.onset + note.duration))
    chord_positions: dict[Fraction, list[str]] = {}
    for chord in chords:
        if start <= chord.onset < end:
            chord_positions.setdefault(chord.onset, []).append(chord.chord)
            note_boundaries.add(chord.onset)
    boundaries = sorted(note_boundaries)
    pieces: list[str] = []
    has_note_or_chord = False
    measure_accidentals: dict[str, int] = {}
    for position, next_position in zip(boundaries, boundaries[1:]):
        chord_text = "".join(chord_positions.get(position, []))
        if chord_text:
            has_note_or_chord = True
            pieces.append(chord_text)
        active_notes = [
            event
            for event in notes
            if event.onset <= position < event.onset + event.duration
        ]
        if active_notes:
            active_notes.sort(key=lambda event: (event.onset, event.source_order))
            note = active_notes[0]
            has_note_or_chord = True
            continues = note.onset + note.duration > next_position
            spelling, continuation_spelling = _serialize_note_spelling(
                note,
                measure,
                measure_accidentals,
                position == note.onset,
                spelling_diagnostics,
            )
            pieces.append(
                _format_supported_note(
                    spelling,
                    next_position - position,
                    measure.note_length,
                    continues,
                    continuation_spelling,
                )
            )
        else:
            pieces.append(_format_supported_rest(next_position - position, measure.note_length))
    if not has_note_or_chord:
        return "Z"
    return "".join(pieces)


def _compress_measure_rests(rendered: list[str]) -> list[str]:
    """Use compact ZN notation for adjacent fully silent measures."""
    result: list[str] = []
    index = 0
    while index < len(rendered):
        if rendered[index] != "Z":
            result.append(rendered[index])
            index += 1
            continue
        end = index + 1
        while end < len(rendered) and rendered[end] == "Z":
            end += 1
        count = end - index
        result.append("Z" if count == 1 else f"Z{count}")
        index = end
    return result


def _metadata_field_lines(
    measure: Measure,
    previous: tuple[tuple[int, int], Fraction, str],
) -> tuple[list[str], tuple[tuple[int, int], Fraction, str]]:
    """Emit only the M, L, and K changes needed to follow a canonical measure."""
    meter, note_length, key = previous
    fields: list[str] = []
    if measure.meter != meter:
        fields.append(f"M:{measure.meter[0]}/{measure.meter[1]}")
    if measure.note_length != note_length:
        fields.append(f"L:1/{measure.note_length.denominator}")
    if measure.key != key:
        fields.append(f"K:{measure.key}")
    return fields, (measure.meter, measure.note_length, measure.key)


def _build_grid(score: ParsedScore) -> list[Measure]:
    """Use Ins measures as the common grid and extend it to cover both voices."""
    ins = score.voices["ins"]
    vocal = score.voices["vocal"]
    source_grid = ins.measures or vocal.measures
    if not source_grid:
        raise ScoreError("ABC score has no output measure grid.")
    grid = [
        Measure(
            onset=measure.onset,
            duration=measure.duration,
            meter=measure.meter,
            note_length=measure.note_length,
            key=measure.key,
            line_number=measure.line_number,
        )
        for measure in source_grid
    ]
    target_end = max(ins.cursor, vocal.cursor)
    for voice in score.voices.values():
        for event in voice.events:
            target_end = max(target_end, event.onset + event.duration)
    while grid[-1].onset + grid[-1].duration < target_end:
        if len(grid) >= MAX_MEASURES:
            raise ScoreError(f"Converted score exceeds the {MAX_MEASURES} measure limit.")
        previous = grid[-1]
        grid.append(
            Measure(
                onset=previous.onset + previous.duration,
                duration=previous.duration,
                meter=previous.meter,
                note_length=previous.note_length,
                key=previous.key,
                line_number=previous.line_number,
            )
        )
    return grid


def _render_score(
    score: ParsedScore,
    instrumental_notes: list[ScoreEvent],
    vocal_notes: list[ScoreEvent] | None = None,
    section_comment_overrides: dict[int, str | None] | None = None,
    add_fallback_section_comments: bool = False,
    spelling_diagnostics: list[Diagnostic] | None = None,
) -> str:
    """Serialize aligned one-to-four-bar voice groups in exact ABC timelines."""
    grid = _build_grid(score)
    all_notes = instrumental_notes + (vocal_notes or [])
    vocal_chords = [
        event
        for voice in score.voices.values()
        for event in voice.events
        if event.kind == "chord"
    ]
    vocal_chords.sort(key=lambda event: (event.onset, event.source_order))
    units = [_upstream_length_unit(measure, all_notes, vocal_chords) for measure in grid]
    common_denominator = math.lcm(*(unit.denominator for unit in units))
    note_length = (
        Fraction(1, common_denominator)
        if common_denominator <= MAX_UPSTREAM_NOTE_LENGTH_DENOMINATOR
        and common_denominator & (common_denominator - 1) == 0
        else score.initial_note_length
    )
    for measure in grid:
        measure.note_length = note_length

    tempo_changes = {change.onset: change for change in score.tempos[1:]}
    grid_onsets = {measure.onset for measure in grid}
    if any(onset not in grid_onsets for onset in tempo_changes):
        raise ScoreError("A tempo change falls inside a measure; it cannot be serialized without moving its onset.")
    initial_tempo = score.tempos[0]
    quarter_bpm = initial_tempo.bpm * float(initial_tempo.beat_unit / Fraction(1, 4))
    header = [
        "X:1",
        "T:",
        f"M:{score.initial_meter[0]}/{score.initial_meter[1]}",
        f"L:1/{note_length.denominator}",
        f"Q:1/4={quarter_bpm:g}",
        'V: Vocal clef=treble name="Vocal Melody" snm="Vocal"',
        'V: Ins clef=treble name="Ins Melody" snm="Inst."',
        f"K:{score.initial_key}",
    ]

    record_offsets: dict[int, int] = {}
    cursors = {"vocal": 0, "ins": 0}
    for record in score.line_records:
        record_offsets[record.line_index] = cursors[record.voice]
        cursors[record.voice] += len(record.measure_indices)
    comments_by_measure: dict[int, list[str]] = {}
    source_comments = [
        (index, line.lstrip()[1:].strip())
        for index, line in enumerate(score.source_lines)
        if line.lstrip().startswith("%")
    ]
    for line_index, comment in source_comments:
        if section_comment_overrides is not None and line_index in section_comment_overrides:
            comment = section_comment_overrides[line_index] or ""
        target = next(
            (record for record in score.line_records if record.line_index > line_index),
            None,
        )
        if target is not None and comment:
            target_index = record_offsets[target.line_index]
            comments_by_measure.setdefault(target_index, []).append(comment)
    for record in score.line_records:
        if record.comment is not None and record.comment.strip():
            target_index = record_offsets[record.line_index] + len(record.measure_indices)
            if target_index < len(grid):
                comments_by_measure.setdefault(target_index, []).append(record.comment.strip())
    for line_index, comment in score.metadata_trailing_comments.items():
        target = next(
            (record for record in score.line_records if record.line_index > line_index),
            None,
        )
        if target is not None:
            comments_by_measure.setdefault(record_offsets[target.line_index], []).append(comment.strip())

    if add_fallback_section_comments:
        comments_by_measure.setdefault(0, []).insert(0, "Intro")
        trailing_section_comments = ["Outro"]
    else:
        trailing_section_comments = []

    output_lines = list(header)
    metadata = {
        name: (score.initial_meter, note_length, score.initial_key)
        for name in ("vocal", "ins")
    }
    index = 0
    while index < len(grid):
        end = index + 1
        while end < len(grid) and end - index < 4:
            if grid[end].meter != grid[index].meter or grid[end].key != grid[index].key:
                break
            if grid[end].onset in tempo_changes or end in comments_by_measure:
                break
            end += 1
        measures = grid[index:end]
        output_lines.extend(f"% {comment}" for comment in comments_by_measure.get(index, []))
        output_lines.append("V: Vocal")
        if measures[0].onset in tempo_changes:
            change = tempo_changes[measures[0].onset]
            beat = f"{change.beat_unit.numerator}/{change.beat_unit.denominator}"
            output_lines.append(f"Q:{beat}={change.bpm:g}")
        fields, metadata["vocal"] = _metadata_field_lines(measures[0], metadata["vocal"])
        output_lines.extend(fields)
        vocal_bars = [
            _render_measure(measure, vocal_notes or [], vocal_chords, spelling_diagnostics)
            for measure in measures
        ]
        output_lines.append("|".join(_compress_measure_rests(vocal_bars)) + "|")
        output_lines.append("V: Ins")
        fields, metadata["ins"] = _metadata_field_lines(measures[0], metadata["ins"])
        output_lines.extend(fields)
        ins_bars = [
            _render_measure(measure, instrumental_notes, [], spelling_diagnostics)
            for measure in measures
        ]
        output_lines.append("|".join(_compress_measure_rests(ins_bars)) + "|")
        index = end

    output_lines.extend(f"% {comment}" for comment in trailing_section_comments)
    result = score.newline.join(output_lines) + score.newline
    return result


def _score_end(score: ParsedScore) -> Fraction:
    """Return the final timeline position across both voices."""
    return max(
        (
            max(
                voice.cursor,
                max(
                    (event.onset + event.duration for event in voice.events),
                    default=Fraction(0),
                ),
            )
            for voice in score.voices.values()
        ),
        default=Fraction(0),
    )


def _full_measure_silence_intervals(score: ParsedScore) -> list[tuple[Fraction, Fraction]]:
    """Return aligned source bars covered only by full-measure rests in both voices."""
    silent_intervals: list[tuple[Fraction, Fraction]] = []
    for measure in _build_grid(score):
        start = measure.onset
        end = start + measure.duration
        voices_are_silent = True
        for voice_name in ("vocal", "ins"):
            voice = score.voices[voice_name]
            source_measure = next(
                (
                    item
                    for item in voice.measures
                    if item.onset == start and item.duration == measure.duration
                ),
                None,
            )
            if source_measure is None:
                voices_are_silent = False
                break
            if any(
                event.kind == "note"
                and event.onset < end
                and event.onset + event.duration > start
                for event in source_measure.events
            ):
                voices_are_silent = False
                break
            rest_intervals = sorted(
                (
                    max(start, event.onset),
                    min(end, event.onset + event.duration),
                )
                for event in source_measure.events
                if event.kind == "rest"
                and event.onset < end
                and event.onset + event.duration > start
            )
            cursor = start
            for rest_start, rest_end in rest_intervals:
                if rest_start > cursor:
                    break
                cursor = max(cursor, rest_end)
            if cursor < end:
                voices_are_silent = False
                break
        if voices_are_silent:
            silent_intervals.append((start, end))
    return silent_intervals


def _event_timeline_signature(event: ScoreEvent) -> tuple[str, Fraction, Fraction, int | None, str]:
    """Return the musical identity used to compare trusted source timeline events."""
    return event.kind, event.onset, event.duration, event.pitch, event.chord


def _smart_trusted_prefix_snapshot(
    abc: str,
    max_repair_units: int,
) -> dict[str, dict[str, object]]:
    """Capture completely parsed per-voice bars before Smart repairs a damaged source."""
    parser = ABCScoreParser("extended", max_repair_units)
    try:
        score = parser.parse(abc)
        voices = score.voices
        parse_completed = True
    except ScoreError:
        voices = parser.partial_state.voices
        parse_completed = False
    uncertain_onsets: list[Fraction] = []
    for observation in parser.short_measures:
        voice = voices.get(observation.voice)
        if voice is not None and observation.measure_index < len(voice.measures):
            uncertain_onsets.append(voice.measures[observation.measure_index].onset)
    for voice_name, measure_index, _ in parser.pending_overflows:
        voice = voices.get(voice_name)
        if voice is not None and measure_index < len(voice.measures):
            uncertain_onsets.append(voice.measures[measure_index].onset)
    earliest_uncertain_onset = min(uncertain_onsets, default=None)
    snapshot: dict[str, dict[str, object]] = {}
    for voice_name, voice in voices.items():
        if not voice.measures:
            continue
        eligible_indices = set(range(len(voice.measures)))
        if earliest_uncertain_onset is not None:
            eligible_indices = {
                index
                for index in eligible_indices
                if voice.measures[index].onset + voice.measures[index].duration
                <= earliest_uncertain_onset
            }
        if not eligible_indices:
            continue
        trusted_measure_count = max(eligible_indices) + 1
        endpoint = voice.measures[trusted_measure_count - 1].onset + voice.measures[
            trusted_measure_count - 1
        ].duration
        pending_tie_at_end = any(
            event.kind == "note"
            and event.tied_after
            and event.onset + event.duration == endpoint
            for event in voice.events
            if event.measure_index < trusted_measure_count
        )
        resolved_voice = copy.deepcopy(voice)
        ABCScoreParser("extended", max_repair_units)._resolve_ties(resolved_voice)
        events = [
            event
            for event in resolved_voice.events
            if event.kind in {"note", "rest", "chord"}
            and (event.onset < endpoint if event.kind == "chord" else event.onset + event.duration <= endpoint)
            and not (
                pending_tie_at_end
                and event.kind == "note"
                and event.onset + event.duration == endpoint
            )
        ]
        snapshot[voice_name] = {
            "end": endpoint,
            "grid": [
                (item.onset, item.duration, item.meter, item.key)
                for item in voice.measures[:trusted_measure_count]
            ],
            "events": Counter(_event_timeline_signature(event) for event in events),
            "complete": parse_completed,
        }
    return snapshot


def _trusted_prefix_mismatch(
    candidate_score: ParsedScore,
    snapshot: dict[str, dict[str, object]],
) -> str | None:
    """Explain whether a candidate changed a complete prefix from the original source."""
    for voice_name, expected in snapshot.items():
        voice = candidate_score.voices.get(voice_name)
        if voice is None:
            return f"Smart candidate removed the trusted {voice_name} voice."
        expected_grid = expected["grid"]
        assert isinstance(expected_grid, list)
        actual_grid = [
            (item.onset, item.duration, item.meter, item.key)
            for item in voice.measures[:len(expected_grid)]
        ]
        if actual_grid != expected_grid:
            return f"Smart candidate changed the trusted {voice_name} measure grid."
        end = expected["end"]
        assert isinstance(end, Fraction)
        actual_events = Counter(
            _event_timeline_signature(event)
            for event in voice.events
            if event.kind in {"note", "rest", "chord"}
            and (event.onset < end if event.kind == "chord" else event.onset + event.duration <= end)
        )
        expected_events = expected["events"]
        assert isinstance(expected_events, Counter)
        if actual_events != expected_events:
            return f"Smart candidate changed trusted {voice_name} events before {end}."
    return None


def _remap_smart_patch_details(
    details: dict[str, object],
    original_abc: str,
    candidate_abc: str,
) -> dict[str, object]:
    """Map a Smart repair span back to the exact token in the original ABC source."""
    remapped = copy.deepcopy(details)
    change = remapped.get("duration_change")
    if not isinstance(change, dict):
        return remapped
    span = change.get("source_span")
    if not isinstance(span, dict):
        return remapped
    start = int(span.get("start_offset", -1))
    end = int(span.get("end_offset", -1))
    if start < 0 or end < start or end > len(candidate_abc):
        return remapped
    matcher = difflib.SequenceMatcher(a=original_abc, b=candidate_abc, autojunk=True)
    overlapping = [
        (tag, original_start, original_end, candidate_start, candidate_end)
        for tag, original_start, original_end, candidate_start, candidate_end in matcher.get_opcodes()
        if candidate_start < end and candidate_end > start
    ]
    exact_match = next(
        (
            (original_start + start - candidate_start, original_start + end - candidate_start)
            for tag, original_start, original_end, candidate_start, candidate_end in overlapping
            if tag == "equal" and candidate_start <= start and end <= candidate_end
        ),
        None,
    )
    if exact_match is not None:
        original_start, original_end = exact_match
    elif overlapping:
        original_start = min(item[1] for item in overlapping)
        original_end = max(item[2] for item in overlapping)
    else:
        return remapped
    source_token = original_abc[original_start:original_end]
    source_line_number = original_abc.count("\n", 0, original_start) + 1
    span.update(
        {
            "start_offset": original_start,
            "end_offset": original_end,
            "line": source_line_number,
            "text": source_token,
        }
    )
    voice_name = str(change.get("voice", "")).lower()
    original_duration = _source_note_duration(original_abc, original_start, voice_name)
    if original_duration is not None:
        change["original_duration"] = str(original_duration)
    return remapped


def _source_note_duration(abc: str, offset: int, voice_name: str) -> Fraction | None:
    """Read the source note length active at a duration-repair token offset."""
    line_index = abc.count("\n", 0, offset)
    lines = abc.splitlines()
    if not 0 <= line_index < len(lines):
        return None
    parser = ABCScoreParser("extended", DEFAULT_MAX_REPAIR_UNITS)
    try:
        headers, header_end = parser._read_header(lines)
        note_length = parser._parse_note_length(headers["L"])
    except (ScoreError, KeyError):
        return None
    active_voice: str | None = None
    for line in lines[header_end + 1:line_index + 1]:
        voice_match = VOICE_PATTERN.fullmatch(line.strip())
        if voice_match is not None:
            token = voice_match.group(1).lower()
            active_voice = token if token in {"vocal", "ins"} else VOICE_ALIASES.get(token)
            continue
        if active_voice != voice_name:
            continue
        field_match = BODY_FIELD_PREFIX_PATTERN.match(line.strip())
        if field_match is None or field_match.group(1) != "L":
            continue
        try:
            note_length = parser._parse_note_length(field_match.group(2))
        except ScoreError:
            return None
    line_start = abc.rfind("\n", 0, offset) + 1
    line_end = abc.find("\n", offset)
    source_line = abc[line_start:] if line_end < 0 else abc[line_start:line_end]
    code = source_line.partition("%")[0]
    token_offset = offset - line_start
    token_match = next(
        (match for match in NOTE_PATTERN.finditer(code) if match.start() == token_offset),
        None,
    )
    if token_match is None:
        return None
    match = NOTE_PATTERN.fullmatch(token_match.group())
    if match is None:
        return None
    try:
        duration = note_length * _parse_fraction(match.group("length"))
    except ScoreError:
        return None
    dots = len(match.group("dots"))
    return duration * Fraction((2 ** (dots + 1)) - 1, 2**dots)


def _score_seconds(score: ParsedScore) -> float:
    """Integrate score duration across all supported tempo changes."""
    end = _score_end(score)
    tempos = sorted(score.tempos, key=lambda item: item.onset)
    if not tempos:
        raise ScoreError("ABC score has no valid tempo.")
    current = tempos[0]
    cursor = Fraction(0)
    seconds = 0.0
    for change in tempos[1:]:
        if change.onset <= cursor:
            current = change
            continue
        segment_end = min(change.onset, end)
        if segment_end > cursor:
            seconds += float((segment_end - cursor) / current.beat_unit) * 60.0 / current.bpm
            cursor = segment_end
        current = change
        if cursor >= end:
            break
    if cursor < end:
        seconds += float((end - cursor) / current.beat_unit) * 60.0 / current.bpm
    return seconds


def _verify_result(
    abc: str,
    max_repair_units: int,
    original_ins: list[tuple[Fraction, Fraction, int, bool, str]],
    original_chords: list[tuple[Fraction, str]],
    preserve_ins: bool,
    source_score: ParsedScore | None = None,
    expected_ins_notes: list[ScoreEvent] | None = None,
) -> None:
    """Reparse output and enforce the no-vocal and Ins-preservation invariants."""
    verified = ABCScoreParser("strict", max_repair_units).parse(abc)
    vocal_grid = [
        (measure.onset, measure.duration, measure.meter, measure.key)
        for measure in verified.voices["vocal"].measures
    ]
    ins_grid = [
        (measure.onset, measure.duration, measure.meter, measure.key)
        for measure in verified.voices["ins"].measures
    ]
    if vocal_grid != ins_grid:
        raise ScoreError("Final Vocal and Ins measures do not share the same meter and key timeline.")
    vocal_notes = [event for event in verified.voices["vocal"].events if event.kind == "note"]
    if vocal_notes:
        raise ScoreError("Final score still contains pitched Vocal notes.")
    actual_chords = [
        (event.onset, event.chord)
        for event in verified.voices["vocal"].events
        if event.kind == "chord"
    ]
    actual_chords.sort(key=lambda item: (item[0], item[1]))
    if actual_chords != original_chords:
        raise ScoreError("Final score changed Vocal chord symbols or their timing.")
    ins_notes = [event for event in verified.voices["ins"].events if event.kind == "note"]
    _assert_monophonic(ins_notes, "Ins")
    if expected_ins_notes is not None:
        expected_timeline = Counter(
            (event.onset, event.duration, event.pitch)
            for event in expected_ins_notes
            if event.kind == "note"
        )
        actual_timeline = Counter(
            (event.onset, event.duration, event.pitch)
            for event in ins_notes
        )
        if actual_timeline != expected_timeline:
            raise ScoreError(
                "Final Ins onset, duration, and pitch do not exactly match the source-preserving transfer."
            )
    if preserve_ins:
        final_ins = Counter(
            (event.onset, event.duration, event.pitch or 0, event.tied_after, event.spelling)
            for event in ins_notes
        )
        if Counter(original_ins) - final_ins:
            raise ScoreError("Final score changed an original Ins note under instrumental_priority.")
    if source_score is not None:
        source_grid = _build_grid(source_score)
        verified_grid = _build_grid(verified)
        source_grid_end = source_grid[-1].onset + source_grid[-1].duration
        verified_grid_end = verified_grid[-1].onset + verified_grid[-1].duration
        if verified_grid_end != source_grid_end:
            raise ScoreError("Final score duration differs from the parsed source timeline.")
        for start, end in _full_measure_silence_intervals(source_score):
            if any(
                event.onset < end and event.onset + event.duration > start
                for event in ins_notes
            ):
                raise ScoreError(
                    f"Final Ins contains a sounding note in source full-measure silence [{start}, {end})."
                )


def _convert_abc_checked(
    abc: str,
    repair_mode: RepairMode = "extended",
    melody_policy: MelodyPolicy = "instrumental_priority",
    lyrics_mode: LyricsMode = "sections_only",
    padding_seconds: float = DEFAULT_PADDING_SECONDS,
    max_repair_units: int = DEFAULT_MAX_REPAIR_UNITS,
) -> ConversionResult:
    """Convert one ABC score and validate its serialized representation."""
    if lyrics_mode not in {"sections_only", "empty", "instrumental_sections", "instrumental_only"}:
        raise ScoreError(f"Unsupported lyrics mode {lyrics_mode!r}.")
    if max_repair_units > MAX_REPAIR_UNIT_LIMIT:
        raise ScoreError(f"max_repair_units must not exceed {MAX_REPAIR_UNIT_LIMIT}.")
    if not math.isfinite(float(padding_seconds)) or padding_seconds < 0:
        raise ScoreError("padding_seconds must be a finite non-negative number.")
    if padding_seconds > MAX_PADDING_SECONDS:
        raise ScoreError(f"padding_seconds must not exceed {MAX_PADDING_SECONDS:g} seconds.")

    parser = ABCScoreParser(repair_mode, max_repair_units)
    score = parser.parse(abc)
    ins_before = [event for event in score.voices["ins"].events if event.kind == "note"]
    vocal_before = [event for event in score.voices["vocal"].events if event.kind == "note"]
    original_ins = [
        (event.onset, event.duration, event.pitch or 0, event.tied_after, event.spelling)
        for event in ins_before
    ]
    original_chords = [
        (event.onset, event.chord)
        for voice in score.voices.values()
        for event in voice.events
        if event.kind == "chord"
    ]
    original_chords.sort(key=lambda item: (item[0], item[1]))
    score_seconds = _score_seconds(score)
    lyrics, section_comment_overrides, lyrics_diagnostics, add_fallback_section_comments = _build_lyrics(
        score.sections,
        score.source_lines,
        lyrics_mode,
        score_seconds,
        True,
    )
    try:
        repaired = _render_score(
            score,
            ins_before,
            vocal_before,
            section_comment_overrides,
            add_fallback_section_comments,
        )
    except Exception as exc:
        repaired = ""
        parser.candidate_log.append(
            {
                "type": "repaired_serialization",
                "status": "failed",
                "reason": str(exc),
            }
        )
        LOGGER.error(
            f"Unable to serialize the repaired pre-transfer score: {exc}\n{traceback.format_exc()}"
        )

    instrumental_notes, transfer_stats = _transfer_melody(score, melody_policy)
    spelling_diagnostics: list[Diagnostic] = []
    converted = _render_score(
        score,
        instrumental_notes,
        section_comment_overrides=section_comment_overrides,
        add_fallback_section_comments=add_fallback_section_comments,
        spelling_diagnostics=spelling_diagnostics,
    )
    ins_repairs_that_change_notes = [
        item
        for item in score.diagnostics
        if item.voice == "ins"
        and (
            item.repair.startswith("Trim the minimum trailing duration")
            or item.repair.startswith("Keep the previous meter")
            or item.repair.startswith("Keep the previous key")
        )
    ]
    preserve_ins = melody_policy == "instrumental_priority" and not ins_repairs_that_change_notes
    transfer_stats["ins_notes_repaired"] = sum(
        1
        for item in ins_repairs_that_change_notes
        if item.repair.startswith("Trim the minimum trailing duration")
    )
    transfer_stats["ins_changes_unknown"] = int(
        any(
            item.voice == "ins"
            and (
                item.repair.startswith("Keep the previous meter")
                or item.repair.startswith("Keep the previous key")
            )
            for item in score.diagnostics
        )
    )
    transfer_stats["ins_preservation_verified"] = int(preserve_ins)
    _verify_result(
        converted,
        max_repair_units,
        original_ins,
        original_chords,
        preserve_ins,
        source_score=score,
        expected_ins_notes=instrumental_notes,
    )

    max_duration = float(
        max(
            MIN_MAX_DURATION_SECONDS,
            min(MAX_DURATION_SECONDS, math.ceil(score_seconds + padding_seconds)),
        )
    )
    grid = _build_grid(score)
    sections = score.sections
    initial_tempo = score.tempos[0]
    bpm = initial_tempo.bpm
    transfer_description = (
        f"Vocal notes: {transfer_stats['vocal_notes']} | "
        f"transferred: {transfer_stats['fully_transferred']} | "
        f"partially transferred: {transfer_stats['partially_transferred']} | "
        f"not transferred: {transfer_stats['not_transferred']}"
    )
    info = (
        f"{bpm:g} BPM | {len(grid)} bars | theoretical {score_seconds:.2f}s | "
        f"max_duration {max_duration:.1f}s | sections: {' > '.join(sections)} | {transfer_description}"
    )
    diagnostics = [*score.diagnostics, *spelling_diagnostics, *lyrics_diagnostics]
    warnings = json.dumps(
        {
            "diagnostics": [item.to_dict() for item in diagnostics],
            "transfer": transfer_stats,
            "conversion": {
                "conversion_status": "OK",
                "mode": "error",
                "raw_source_preserved": False,
                "unprocessed_spans": [],
                "source_musical_event_changes": {
                    "repair_diagnostics": [item.to_dict() for item in diagnostics],
                    "ins_notes_changed": transfer_stats["ins_notes_changed"],
                    "ins_notes_repaired": transfer_stats["ins_notes_repaired"],
                    "ins_changes_unknown": bool(transfer_stats["ins_changes_unknown"]),
                },
                "internal_validator": "passed",
                "independent_checker": "not_run_by_converter",
                "vocal_absence_confirmed": True,
                "yue2_runtime_tested": False,
                "downstream_risk": "YuE2 runtime behavior was not tested.",
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    repair_count = sum(1 for item in diagnostics if item.severity == "warning")
    return ConversionResult(
        converted,
        lyrics,
        max_duration,
        float(score_seconds),
        len(grid),
        float(bpm),
        info,
        warnings,
        int(repair_count),
        diagnostics,
        repaired,
        list(parser.candidate_log),
    )


class _SmartRecoveryStrategy:
    """Search a bounded set of source-preserving musical repair hypotheses."""

    def __init__(
        self,
        repair_mode: RepairMode,
        melody_policy: MelodyPolicy,
        lyrics_mode: LyricsMode,
        padding_seconds: float,
        max_repair_units: int,
    ) -> None:
        """Keep the validated conversion settings for candidate evaluation."""
        self.repair_mode = repair_mode
        self.melody_policy = melody_policy
        self.lyrics_mode = lyrics_mode
        self.padding_seconds = padding_seconds
        self.max_repair_units = max_repair_units

    def run(self, abc: str, initial_error: str) -> ConversionResult:
        """Try ranked text candidates, then structural partial conversion, then raw fallback."""
        started = time.perf_counter()
        candidate_log: list[dict[str, object]] = [
            {
                "type": "smart_search",
                "status": "started",
                "strategy": "bounded_text_beam",
                "initial_extended_error": initial_error,
                "max_depth": MAX_SMART_DEPTH,
                "max_candidates": MAX_SMART_CANDIDATES,
                "beam_width": MAX_SMART_BEAM_WIDTH,
                "time_limit_seconds": MAX_SMART_SECONDS,
            }
        ]
        trusted_prefix = _smart_trusted_prefix_snapshot(abc, self.max_repair_units)
        beam: list[
            tuple[str, tuple[str, ...], tuple[int, int, int], tuple[dict[str, object], ...]]
        ] = [(abc, (), (0, 0, 0), ())]
        seen = {abc}
        attempts = 0
        for depth in range(MAX_SMART_DEPTH):
            next_beam: list[
                tuple[str, tuple[str, ...], tuple[int, int, int], tuple[dict[str, object], ...]]
            ] = []
            successes: list[
                tuple[tuple[int, ...], ConversionResult, list[str], list[dict[str, object]]]
            ] = []
            for current_text, applied, total_cost, applied_details in beam:
                if time.perf_counter() - started >= MAX_SMART_SECONDS:
                    break
                parser = ABCScoreParser("extended", self.max_repair_units)
                parse_error = ""
                try:
                    parser.parse(current_text)
                except ScoreError as exc:
                    parse_error = str(exc)
                try:
                    proposals = self._generate_candidates(current_text, parser, parse_error)
                except Exception as exc:
                    LOGGER.error(
                        f"Unable to generate smart repair candidates: {exc}\n{traceback.format_exc()}"
                    )
                    candidate_log.append(
                        {
                            "type": "smart_candidate_generation",
                            "status": "error",
                            "reason": str(exc),
                        }
                    )
                    proposals = []
                for proposal in proposals:
                    if proposal.text in seen:
                        continue
                    if attempts >= MAX_SMART_CANDIDATES:
                        break
                    if time.perf_counter() - started >= MAX_SMART_SECONDS:
                        break
                    seen.add(proposal.text)
                    attempts += 1
                    candidate_id = f"smart-{attempts:03d}"
                    reasons = applied + (proposal.reason,)
                    patch_details = _remap_smart_patch_details(proposal.details, abc, current_text)
                    selected_details = applied_details + ((patch_details,) if patch_details else ())
                    cost = tuple(left + right for left, right in zip(total_cost, proposal.cost))
                    try:
                        result = _convert_abc_checked(
                            proposal.text,
                            self.repair_mode,
                            self.melody_policy,
                            self.lyrics_mode,
                            self.padding_seconds,
                            self.max_repair_units,
                        )
                    except ScoreError as exc:
                        candidate_log.append(
                            {
                                "type": "smart_candidate",
                                "candidate_id": candidate_id,
                                "depth": depth + 1,
                                "category": proposal.category,
                                "status": "rejected",
                                "reason": str(exc),
                                "patch": proposal.reason,
                                "patch_details": patch_details,
                                "patch_cost": list(proposal.cost),
                                "changed_character_count": _changed_character_count(current_text, proposal.text),
                            }
                        )
                        if depth + 1 < MAX_SMART_DEPTH:
                            next_beam.append((proposal.text, reasons, cost, selected_details))
                        continue
                    except Exception as exc:
                        LOGGER.error(
                            f"Unexpected smart candidate failure for {candidate_id}: {exc}\n"
                            f"{traceback.format_exc()}"
                        )
                        candidate_log.append(
                            {
                                "type": "smart_candidate",
                                "candidate_id": candidate_id,
                                "depth": depth + 1,
                                "category": proposal.category,
                                "status": "error",
                                "reason": str(exc),
                                "patch": proposal.reason,
                                "patch_details": patch_details,
                            }
                        )
                        continue
                    warning_details = json.loads(result.warnings) if result.warnings else {}
                    transfer = warning_details.get("transfer", {})
                    try:
                        candidate_parser = ABCScoreParser("extended", self.max_repair_units)
                        candidate_score = candidate_parser.parse(proposal.text)
                        prefix_mismatch = _trusted_prefix_mismatch(candidate_score, trusted_prefix)
                        if prefix_mismatch:
                            candidate_log.append(
                                {
                                    "type": "smart_candidate",
                                    "candidate_id": candidate_id,
                                    "depth": depth + 1,
                                    "category": proposal.category,
                                    "status": "rejected",
                                    "reason": prefix_mismatch,
                                    "patch": proposal.reason,
                                    "patch_details": patch_details,
                                    "patch_cost": list(proposal.cost),
                                }
                            )
                            continue
                        vocal_grid = [
                            (item.onset, item.duration, item.meter, item.key)
                            for item in candidate_score.voices["vocal"].measures
                        ]
                        ins_grid = [
                            (item.onset, item.duration, item.meter, item.key)
                            for item in candidate_score.voices["ins"].measures
                        ]
                        grid_mismatch = int(vocal_grid != ins_grid)
                        synthetic_rest_count = sum(
                            item.repair.startswith("Append a rest")
                            for item in candidate_score.diagnostics
                        )
                    except ScoreError as exc:
                        grid_mismatch = 1
                        synthetic_rest_count = 0
                        candidate_log.append(
                            {
                                "type": "smart_candidate_scoring",
                                "candidate_id": candidate_id,
                                "status": "partial",
                                "reason": str(exc),
                            }
                        )
                    validation_score = (
                        int(transfer.get("ins_notes_changed", 0))
                        + int(transfer.get("ins_notes_repaired", 0)),
                        grid_mismatch,
                        synthetic_rest_count,
                        int(transfer.get("not_transferred", 0)),
                        result.repair_count,
                        cost[0],
                        cost[1],
                        cost[2],
                    )
                    successes.append((validation_score, result, list(reasons), list(selected_details)))
                    candidate_log.append(
                        {
                            "type": "smart_candidate",
                            "candidate_id": candidate_id,
                            "depth": depth + 1,
                            "category": proposal.category,
                            "status": "accepted",
                            "reason": "Internal strict serialization validation passed.",
                            "patch": proposal.reason,
                            "patch_details": patch_details,
                            "patch_cost": list(proposal.cost),
                            "validation_score": list(validation_score),
                        }
                    )
                if attempts >= MAX_SMART_CANDIDATES or time.perf_counter() - started >= MAX_SMART_SECONDS:
                    break
            if successes:
                _, result, reasons, selected_details = min(successes, key=lambda item: item[0])
                self._mark_smart_recovered(
                    result,
                    candidate_log,
                    reasons,
                    attempts,
                    selected_details,
                )
                return result
            next_beam.sort(key=lambda item: (len(item[1]), item[2], item[0]))
            beam = next_beam[:MAX_SMART_BEAM_WIDTH]
            if not beam or attempts >= MAX_SMART_CANDIDATES:
                break
        elapsed = time.perf_counter() - started
        candidate_log.append(
            {
                "type": "smart_search",
                "status": "exhausted",
                "candidates_checked": attempts,
                "elapsed_seconds": round(elapsed, 6),
                "reason": "No candidate passed internal strict validation.",
            }
        )
        try:
            partial = _smart_partial_result(
                abc,
                initial_error,
                self.melody_policy,
                self.max_repair_units,
                candidate_log,
                self.lyrics_mode,
            )
        except Exception as exc:
            LOGGER.error(f"Unable to create SMART_PARTIAL result: {exc}\n{traceback.format_exc()}")
            candidate_log.append(
                {"type": "smart_partial", "status": "error", "reason": str(exc)}
            )
            partial = None
        return partial if partial is not None else _partial_raw_result(
            abc,
            "smart",
            initial_error,
            candidate_log,
            self.lyrics_mode,
        )

    def _mark_smart_recovered(
        self,
        result: ConversionResult,
        candidate_log: list[dict[str, object]],
        reasons: list[str],
        attempts: int,
        selected_details: list[dict[str, object]],
    ) -> None:
        """Mark a candidate as fully recovered and attach its search evidence."""
        result.conversion_status = "SMART_RECOVERED"
        result.candidate_log = [*result.candidate_log, *candidate_log]
        details = json.loads(result.warnings) if result.warnings else {}
        conversion = details.get("conversion", {})
        event_modifications = sum(
            1
            for reason in reasons
            if "extend the last tied note" in reason.lower()
            or "shorten the crossing note" in reason.lower()
        )
        duration_changes = [
            item["duration_change"]
            for item in selected_details
            if isinstance(item.get("duration_change"), dict)
        ]
        result.diagnostics.extend(
            Diagnostic(
                "warning",
                "score",
                None,
                "smart search",
                "The bounded smart search selected a source-text repair hypothesis.",
                reason,
                "smart_hypothesis",
            )
            for reason in reasons
        )
        result.repair_count += len(reasons)
        details["diagnostics"] = [item.to_dict() for item in result.diagnostics]
        conversion.update(
            {
                "conversion_status": "SMART_RECOVERED",
                "mode": "smart",
                "recovery_strategy": "bounded_text_beam",
                "candidate_count": attempts,
                "selected_hypotheses": reasons,
                "source_musical_event_changes": {
                    **conversion.get("source_musical_event_changes", {}),
                    "removed": 0,
                    "events_modified": event_modifications,
                    "duration_changes": duration_changes,
                    "event_change_basis": "selected duration hypotheses; no event deletion is proposed",
                },
                "raw_source_preserved": False,
                "unprocessed_spans": [],
                "vocal_absence_confirmed": True,
            }
        )
        details["conversion"] = conversion
        result.warnings = json.dumps(details, ensure_ascii=False, sort_keys=True)
        result.info = f"SMART_RECOVERED | {result.info}"

    def _generate_candidates(
        self,
        abc: str,
        parser: ABCScoreParser,
        parse_error: str,
    ) -> list[_SmartTextCandidate]:
        """Produce deterministic syntax, timing, metadata, chord, and barline hypotheses."""
        candidates: list[_SmartTextCandidate] = []
        seen: set[str] = set()

        def add(
            text: str,
            category: str,
            reason: str,
            cost: tuple[int, int, int],
            details: dict[str, object] | None = None,
        ) -> None:
            if text != abc and text not in seen:
                seen.add(text)
                candidates.append(_SmartTextCandidate(text, category, reason, cost, details or {}))

        lines = abc.splitlines()
        newline = "\r\n" if "\r\n" in abc else "\n"
        candidates.extend(self._missing_meter_candidates(abc, lines, newline))
        candidates.extend(self._quote_candidates(abc, lines, newline))
        candidates.extend(self._metadata_candidates(abc, lines, newline))
        for candidate in self._tied_duration_candidates(abc, parser):
            add(candidate.text, candidate.category, candidate.reason, candidate.cost, candidate.details)
        for candidate in self._overflow_candidates(abc, parser):
            add(candidate.text, candidate.category, candidate.reason, candidate.cost, candidate.details)
        for candidate in self._barline_candidates(abc, parser, lines, newline):
            add(candidate.text, candidate.category, candidate.reason, candidate.cost)
        candidates.extend(self._chord_candidates(abc, lines, newline))
        for candidate in candidates:
            if candidate.text != abc:
                seen.add(candidate.text)
        ordered: list[_SmartTextCandidate] = []
        unique: set[str] = set()
        for candidate in candidates:
            if candidate.text not in unique:
                ordered.append(candidate)
                unique.add(candidate.text)
        ordered.sort(key=lambda item: (item.cost, item.category, item.reason))
        if parse_error:
            LOGGER.debug(f"Smart recovery generated {len(ordered)} candidates after: {parse_error}")
        return ordered[:MAX_SMART_BEAM_WIDTH + 4]

    def _missing_meter_candidates(
        self,
        abc: str,
        lines: list[str],
        newline: str,
    ) -> list[_SmartTextCandidate]:
        """Offer common meter hypotheses only when the header has no M: field."""
        headers: dict[str, str] = {}
        key_index: int | None = None
        for index, line in enumerate(lines):
            match = FIELD_PATTERN.fullmatch(line)
            if match is None:
                continue
            field_name, value = match.groups()
            if field_name in ("M", "L", "Q", "K"):
                headers[field_name] = value
            if field_name == "K":
                key_index = index
                break
        if "M" in headers or key_index is None:
            return []
        candidates: list[_SmartTextCandidate] = []
        for priority, meter in enumerate(("4/4", "3/4", "2/4", "6/8", "5/4", "7/8")):
            repaired = list(lines)
            repaired.insert(key_index, f"M:{meter}")
            candidates.append(
                _SmartTextCandidate(
                    newline.join(repaired) + (newline if abc.endswith(("\n", "\r")) else ""),
                    "missing_meter",
                    f"Insert M:{meter} as a meter hypothesis; score validation and cross-voice alignment.",
                    (3, priority, len(meter)),
                )
            )
        return candidates

    def _quote_candidates(
        self,
        abc: str,
        lines: list[str],
        newline: str,
    ) -> list[_SmartTextCandidate]:
        """Try quote boundaries that retain a valid chord prefix and tokenizable suffix."""
        candidates: list[_SmartTextCandidate] = []
        for line_index, line in enumerate(lines):
            code, marker, comment = line.partition("%")
            if code.count('"') % 2 == 0:
                continue
            opening = code.rfind('"')
            scan_end = min(len(code), opening + MAX_SMART_CHORD_SCAN_CHARS)
            endpoints = [
                match.start()
                for match in NOTE_PATTERN.finditer(code, opening + 1, scan_end)
            ]
            endpoints.append(len(code))
            valid_endpoints: list[int] = []
            for end in endpoints:
                if end <= opening + 1:
                    continue
                chord_text = code[opening:end].rstrip()
                candidate_chord = f'{chord_text}"'
                if (
                    _match_chord_symbol(candidate_chord) is None
                    or not _chord_quality_supported(candidate_chord)
                ):
                    continue
                suffix = code[opening + len(chord_text):]
                if suffix and not suffix[0].isspace() and NOTE_PATTERN.match(suffix) is None:
                    continue
                if suffix.strip() and not _smart_suffix_is_tokenizable(
                    suffix,
                    self.max_repair_units,
                ):
                    continue
                valid_endpoints.append(opening + len(chord_text))
            for endpoint in valid_endpoints[-4:]:
                repaired_line = code[:endpoint] + '"' + code[endpoint:] + (marker + comment if marker else "")
                repaired = list(lines)
                repaired[line_index] = repaired_line
                candidates.append(
                    _SmartTextCandidate(
                        newline.join(repaired) + (newline if abc.endswith(("\n", "\r")) else ""),
                        "chord_quote",
                        f"Close the unmatched chord quote on source line {line_index + 1} at a valid token boundary.",
                        (1, len(code) - endpoint, 1),
                    )
                )
                if len(candidates) >= MAX_SMART_PROPOSALS_PER_CATEGORY:
                    return candidates
        return candidates

    def _metadata_candidates(
        self,
        abc: str,
        lines: list[str],
        newline: str,
    ) -> list[_SmartTextCandidate]:
        """Repair a key accidentally labeled as a body meter or split merged metadata."""
        candidates: list[_SmartTextCandidate] = []
        active_voice: str | None = None
        for line_index, line in enumerate(lines):
            stripped = line.strip()
            voice_match = VOICE_PATTERN.fullmatch(stripped)
            if voice_match is not None:
                token = voice_match.group(1).lower()
                active_voice = token if token in ("vocal", "ins") else VOICE_ALIASES.get(token)
                continue
            if active_voice is None:
                continue
            field_match = BODY_FIELD_PREFIX_PATTERN.match(stripped)
            if field_match is None or field_match.group(1) not in ("M", "L", "K", "Q"):
                continue
            field_name, raw_value = field_match.groups()
            value = raw_value.strip()
            if field_name == "M" and value in KEY_SIGNATURES:
                repaired = list(lines)
                leading = line[:len(line) - len(line.lstrip())]
                repaired[line_index] = f"{leading}K:{value}"
                candidates.append(
                    _SmartTextCandidate(
                        newline.join(repaired) + (newline if abc.endswith(("\n", "\r")) else ""),
                        "metadata_field",
                        f"Interpret body M:{value} as a key field because it is a supported key, not a meter.",
                        (2, 0, len(value)),
                    )
                )
                if len(candidates) >= MAX_SMART_PROPOSALS_PER_CATEGORY:
                    return candidates
            prefix: str | None = None
            if field_name == "M":
                match = re.match(r"\d+\s*/\s*\d+", value)
                prefix = match.group() if match else None
            elif field_name == "L":
                match = re.match(r"1\s*/\s*\d+", value)
                prefix = match.group() if match else None
            elif field_name == "Q":
                match = TEMPO_PATTERN.match(value)
                prefix = match.group() if match else None
            elif field_name == "K":
                key = next((item for item in sorted(KEY_SIGNATURES, key=len, reverse=True)
                            if value.startswith(item)), None)
                prefix = key
            if not prefix:
                continue
            suffix = value[len(prefix):].strip()
            if not suffix:
                continue
            tokens = ABCScoreParser("extended", self.max_repair_units)._tokenize_measure(
                suffix.rstrip("|:")
            )
            if not tokens or any(token.kind == "unknown" for token in tokens):
                continue
            if not any(token.kind == "note" for token in tokens):
                continue
            leading = line[:len(line) - len(line.lstrip())]
            replacement = f"{leading}{field_name}:{prefix}{newline}{leading}{suffix}"
            repaired = list(lines)
            repaired[line_index] = replacement
            candidates.append(
                _SmartTextCandidate(
                    newline.join(repaired) + (newline if abc.endswith(("\n", "\r")) else ""),
                    "merged_metadata",
                    f"Split a valid {field_name}: metadata prefix from an adjacent, tokenizable music fragment.",
                    (1, len(suffix), 1),
                )
            )
            if len(candidates) >= MAX_SMART_PROPOSALS_PER_CATEGORY:
                return candidates
        return candidates

    def _tied_duration_candidates(
        self,
        abc: str,
        parser: ABCScoreParser,
    ) -> list[_SmartTextCandidate]:
        """Extend only a final tied note by the exact incomplete measure deficit."""
        candidates: list[_SmartTextCandidate] = []
        lines = abc.splitlines()
        for observation in parser.short_measures:
            if not observation.ends_with_tie:
                continue
            voice = parser.partial_state.voices.get(observation.voice)
            if voice is None or observation.measure_index >= len(voice.measures):
                continue
            measure = voice.measures[observation.measure_index]
            record = next(
                (
                    item
                    for item in parser.line_records
                    if item.voice == observation.voice and observation.measure_index in item.measure_indices
                ),
                None,
            )
            if record is None or record.line_index >= len(lines):
                continue
            candidates.extend(
                self._replace_measure_duration(
                    abc,
                    lines,
                    record,
                    observation.measure_index,
                    measure.note_length,
                    observation.expected_duration - observation.actual_duration,
                    require_tie=True,
                    category="tied_duration",
                    reason_prefix="Extend the last tied note by the exact bar deficit",
                )
            )
            if len(candidates) >= MAX_SMART_PROPOSALS_PER_CATEGORY:
                break
        return candidates

    def _overflow_candidates(
        self,
        abc: str,
        parser: ABCScoreParser,
    ) -> list[_SmartTextCandidate]:
        """Shorten a crossing note only to the next barline, retaining its pitch and order."""
        candidates: list[_SmartTextCandidate] = []
        lines = abc.splitlines()
        for voice_name, measure_index, _ in parser.pending_overflows:
            voice = parser.partial_state.voices.get(voice_name)
            if voice is None or measure_index >= len(voice.measures):
                continue
            measure = voice.measures[measure_index]
            boundary = measure.onset + measure.duration
            crossing = [
                event
                for event in measure.events
                if event.kind == "note" and event.onset < boundary < event.onset + event.duration
            ]
            if not crossing:
                continue
            target = crossing[-1]
            new_duration = boundary - target.onset
            if new_duration <= 0:
                continue
            record = next(
                (
                    item
                    for item in parser.line_records
                    if item.voice == voice_name and measure_index in item.measure_indices
                ),
                None,
            )
            if record is None or record.line_index >= len(lines):
                continue
            candidates.extend(
                self._replace_measure_event_duration(
                    abc,
                    lines,
                    record,
                    measure_index,
                    target,
                    measure.note_length,
                    new_duration,
                    "overfull_note",
                    "Shorten the crossing note exactly to the barline; retain pitch, onset, and event order",
                )
            )
            if len(candidates) >= MAX_SMART_PROPOSALS_PER_CATEGORY:
                break
        return candidates

    def _replace_measure_duration(
        self,
        abc: str,
        lines: list[str],
        record: LineRecord,
        measure_index: int,
        note_length: Fraction,
        delta: Fraction,
        require_tie: bool,
        category: str,
        reason_prefix: str,
    ) -> list[_SmartTextCandidate]:
        """Create an exact duration patch for the final note in one source measure."""
        if delta <= 0:
            return []
        line = lines[record.line_index]
        code = line.partition("%")[0]
        segment = _source_measure_span(code, record.measure_indices.index(measure_index))
        if segment is None:
            return []
        segment_start, segment_end = segment
        segment_text = code[segment_start:segment_end]
        parser = ABCScoreParser("extended", self.max_repair_units)
        tokens = [token for token in parser._tokenize_measure(segment_text) if token.kind == "note"]
        if not tokens:
            return []
        token = tokens[-1]
        match = NOTE_PATTERN.fullmatch(token.raw)
        if match is None or (require_tie and not match.group("tie")):
            return []
        old_multiplier = _parse_fraction(match.group("length"))
        dots = len(match.group("dots"))
        old_duration = note_length * old_multiplier * Fraction((2 ** (dots + 1)) - 1, 2**dots)
        replacement = (
            f"{match.group('acc') or ''}{match.group('note')}{match.group('oct')}"
            f"{_format_length(old_duration + delta, note_length)}{match.group('tie')}"
        )
        absolute_start = line.find(code) + segment_start + token.start
        absolute_end = absolute_start + len(token.raw)
        newline = "\r\n" if "\r\n" in abc else "\n"
        source_offset = sum(len(item) + len(newline) for item in lines[:record.line_index]) + absolute_start
        candidate_line = line[:absolute_start] + replacement + line[absolute_end:]
        repaired = list(lines)
        repaired[record.line_index] = candidate_line
        new_text = newline.join(repaired) + (newline if abc.endswith(("\n", "\r")) else "")
        return [
            _SmartTextCandidate(
                new_text,
                category,
                f"{reason_prefix} by {delta} whole-note units on source line {record.line_index + 1}.",
                (2, len(replacement) - len(token.raw), 1),
                {
                    "duration_change": {
                        "voice": record.voice,
                        "original_duration": str(old_duration),
                        "repaired_duration": str(old_duration + delta),
                        "reason": (
                            f"{reason_prefix} by {delta} whole-note units to complete the source measure."
                        ),
                        "source_span": {
                            "start_offset": source_offset,
                            "end_offset": source_offset + len(token.raw),
                            "line": record.line_index + 1,
                            "text": token.raw,
                        },
                    }
                },
            )
        ]

    def _replace_measure_event_duration(
        self,
        abc: str,
        lines: list[str],
        record: LineRecord,
        measure_index: int,
        event: ScoreEvent,
        note_length: Fraction,
        new_duration: Fraction,
        category: str,
        reason: str,
    ) -> list[_SmartTextCandidate]:
        """Map one parsed measure note back to its source token and change only duration."""
        line = lines[record.line_index]
        code = line.partition("%")[0]
        segment = _source_measure_span(code, record.measure_indices.index(measure_index))
        if segment is None:
            return []
        segment_start, segment_end = segment
        segment_text = code[segment_start:segment_end]
        parser = ABCScoreParser("extended", self.max_repair_units)
        note_tokens = [token for token in parser._tokenize_measure(segment_text) if token.kind == "note"]
        if not note_tokens:
            return []
        # A note crossing its own barline must be the final sounding note in that measure.
        token = note_tokens[-1]
        match = NOTE_PATTERN.fullmatch(token.raw)
        if match is None:
            return []
        old_duration = note_length * _parse_fraction(match.group("length"))
        dots = len(match.group("dots"))
        old_duration *= Fraction((2 ** (dots + 1)) - 1, 2**dots)
        source_spelling = match.group("note") + match.group("oct").replace(" ", "")
        if source_spelling != event.spelling or old_duration != event.duration:
            return []
        new_tie = match.group("tie")
        replacement = (
            f"{match.group('acc') or ''}{match.group('note')}{match.group('oct')}"
            f"{_format_length(new_duration, note_length)}{new_tie}"
        )
        absolute_start = line.find(code) + segment_start + token.start
        absolute_end = absolute_start + len(token.raw)
        newline = "\r\n" if "\r\n" in abc else "\n"
        source_offset = sum(len(item) + len(newline) for item in lines[:record.line_index]) + absolute_start
        repaired = list(lines)
        repaired[record.line_index] = line[:absolute_start] + replacement + line[absolute_end:]
        new_text = newline.join(repaired) + (newline if abc.endswith(("\n", "\r")) else "")
        return [
            _SmartTextCandidate(
                new_text,
                category,
                f"{reason} on source line {record.line_index + 1}.",
                (3, len(replacement) - len(token.raw), 1),
                {
                    "duration_change": {
                        "voice": record.voice,
                        "original_duration": str(old_duration),
                        "repaired_duration": str(new_duration),
                        "reason": reason,
                        "source_span": {
                            "start_offset": source_offset,
                            "end_offset": source_offset + len(token.raw),
                            "line": record.line_index + 1,
                            "text": token.raw,
                        },
                    }
                },
            )
        ]

    def _barline_candidates(
        self,
        abc: str,
        parser: ABCScoreParser,
        lines: list[str],
        newline: str,
    ) -> list[_SmartTextCandidate]:
        """Insert barlines only at exact meter boundaries between parsed note tokens."""
        try:
            headers, _ = parser._read_header(lines)
            initial_meter = _parse_meter(headers["M"])
            initial_length = parser._parse_note_length(headers["L"])
        except (ScoreError, KeyError):
            return []
        meters = {name: initial_meter for name in ("vocal", "ins")}
        lengths = {name: initial_length for name in ("vocal", "ins")}
        active_voice: str | None = None
        repaired_lines = list(lines)
        changes: list[str] = []
        for line_index, line in enumerate(lines):
            voice_match = VOICE_PATTERN.fullmatch(line.strip())
            if voice_match is not None:
                token = voice_match.group(1).lower()
                active_voice = token if token in ("vocal", "ins") else VOICE_ALIASES.get(token)
                continue
            if active_voice is None:
                continue
            field_match = BODY_FIELD_PREFIX_PATTERN.match(line.strip())
            if field_match is not None:
                field_name, field_value = field_match.groups()
                if field_name == "M":
                    meter_match = METER_PATTERN.match(field_value.strip())
                    if meter_match is not None:
                        try:
                            meters[active_voice] = _parse_meter(meter_match.group())
                        except ScoreError:
                            pass
                elif field_name == "L":
                    length_match = NOTE_LENGTH_PATTERN.match(field_value.strip())
                    if length_match is not None:
                        try:
                            lengths[active_voice] = Fraction(1, _parse_bounded_int(
                                length_match.group(1), "note length denominator"
                            ))
                        except ScoreError:
                            pass
                continue
            code, marker, comment = line.partition("%")
            if not code.strip():
                continue
            spans = _source_measure_spans(code)
            insertions: list[int] = []
            for segment_start, segment_end in spans:
                segment = code[segment_start:segment_end].strip()
                if not segment:
                    continue
                raw_start = code.find(segment, segment_start, segment_end)
                tokens = parser._tokenize_measure(segment)
                if not tokens or any(token.kind == "unknown" for token in tokens):
                    continue
                width = _measure_duration(meters[active_voice])
                total = Fraction(0)
                token_boundaries: list[tuple[Fraction, int]] = []
                invalid = False
                for token_index, token in enumerate(tokens):
                    if token.kind != "note":
                        continue
                    note_match = NOTE_PATTERN.fullmatch(token.raw)
                    if note_match is None:
                        invalid = True
                        break
                    note_name = note_match.group("note")
                    if note_name == "Z":
                        rest_length = note_match.group("length")
                        if rest_length and not rest_length.isdecimal():
                            invalid = True
                            break
                        rest_count = int(rest_length) if rest_length else 1
                        if rest_count < 1 or rest_count > MAX_MEASURES:
                            invalid = True
                            break
                        duration = width * rest_count
                    else:
                        multiplier = _parse_fraction(note_match.group("length"))
                        dots = len(note_match.group("dots"))
                        duration = lengths[active_voice] * multiplier * Fraction(
                            (2 ** (dots + 1)) - 1,
                            2**dots,
                        )
                    total += duration
                    token_boundaries.append((total, token.end))
                if invalid or total <= width:
                    continue
                if total % width:
                    continue
                measure_index = 0
                for cumulative, token_end in token_boundaries:
                    if cumulative and cumulative % width == 0 and cumulative < total:
                        measure_index += 1
                        insertions.append(raw_start + token_end)
            if insertions:
                for offset in sorted(set(insertions), reverse=True):
                    code = code[:offset] + "|" + code[offset:]
                repaired_lines[line_index] = code + (marker + comment if marker else "")
                changes.append(f"Insert exact-duration barlines on source line {line_index + 1}.")
        if not changes:
            return []
        return [
            _SmartTextCandidate(
                newline.join(repaired_lines) + (newline if abc.endswith(("\n", "\r")) else ""),
                "missing_barline",
                " ".join(changes) + " Other causes or boundary placements may be possible.",
                (1, len(changes), 1),
            )
        ]

    def _chord_candidates(
        self,
        abc: str,
        lines: list[str],
        newline: str,
    ) -> list[_SmartTextCandidate]:
        """Normalize only unambiguous slash-quality and lowercase bass spellings."""
        candidates: list[_SmartTextCandidate] = []
        quality_pattern = re.compile(
            r"^(?P<root>[A-G](?:#{1,2}|b{1,2})?)/"
            r"(?P<quality>maj7|m7b5|7sus4|sus4|sus2|dim7|m7|dim|aug|m6|6|7)$"
        )
        bass_pattern = re.compile(r"^(?P<root>[A-G](?:#{1,2}|b{1,2})?)/(?P<bass>[a-g])(?P<acc>#{1,2}|b{1,2})?$")
        for line_index, line in enumerate(lines):
            replacements: list[tuple[str, str, str]] = []
            for match in CHORD_PATTERN.finditer(line):
                raw = match.group()
                content = raw[1:-1]
                quality = quality_pattern.fullmatch(content)
                bass = bass_pattern.fullmatch(content)
                if quality is not None:
                    repaired = f'"{quality.group("root")}{quality.group("quality")}"'
                    if _match_chord_symbol(repaired) is not None:
                        replacements.append((raw, repaired, "move a recognized chord quality after its root"))
                elif bass is not None:
                    repaired = f'"{bass.group("root")}/{bass.group("bass").upper()}{bass.group("acc") or ""}"'
                    if _match_chord_symbol(repaired) is not None:
                        replacements.append((raw, repaired, "capitalize a lower-case slash-bass note"))
            if not replacements:
                continue
            repaired_line = line
            reasons: list[str] = []
            for original, replacement, reason in replacements:
                repaired_line = repaired_line.replace(original, replacement, 1)
                reasons.append(reason)
            repaired_lines = list(lines)
            repaired_lines[line_index] = repaired_line
            candidates.append(
                _SmartTextCandidate(
                    newline.join(repaired_lines) + (newline if abc.endswith(("\n", "\r")) else ""),
                    "chord_normalization",
                    f"Contextually normalize chord symbol on line {line_index + 1}: {', '.join(reasons)}.",
                    (1, len(replacements), 1),
                )
            )
            if len(candidates) >= MAX_SMART_PROPOSALS_PER_CATEGORY:
                return candidates
        return candidates


def _changed_character_count(original: str, candidate: str) -> int:
    """Estimate a repair's text edit size without expensive sequence matching."""
    common_prefix = 0
    while common_prefix < min(len(original), len(candidate)) and original[common_prefix] == candidate[common_prefix]:
        common_prefix += 1
    common_suffix = 0
    max_suffix = min(len(original) - common_prefix, len(candidate) - common_prefix)
    while common_suffix < max_suffix and original[-common_suffix - 1] == candidate[-common_suffix - 1]:
        common_suffix += 1
    return (len(original) - common_prefix - common_suffix) + (len(candidate) - common_prefix - common_suffix)


def _smart_suffix_is_tokenizable(suffix: str, max_repair_units: int) -> bool:
    """Check a proposed chord suffix as music fragments separated by valid barlines."""
    parser = ABCScoreParser("extended", max_repair_units)
    measures, _, _ = parser._split_music_measures(suffix)
    tokens = [token for measure in measures for token in parser._tokenize_measure(measure)]
    return bool(tokens) and all(token.kind != "unknown" for token in tokens)


def _source_measure_spans(code: str) -> list[tuple[int, int]]:
    """Return source offsets for measure bodies, ignoring barline-like chord content."""
    spans: list[tuple[int, int]] = []
    start = 0
    index = 0
    while index < len(code):
        if code[index] == '"':
            chord = _match_chord_symbol(code, index)
            if chord is not None:
                index = chord.end()
                continue
        if code[index] in "|:":
            end = index
            while start < end and code[start].isspace():
                start += 1
            while end > start and code[end - 1].isspace():
                end -= 1
            spans.append((start, end))
            while index < len(code) and code[index] in "|:":
                index += 1
            start = index
            continue
        index += 1
    end = len(code)
    while start < end and code[start].isspace():
        start += 1
    while end > start and code[end - 1].isspace():
        end -= 1
    if start < end:
        spans.append((start, end))
    return spans


def _source_measure_span(code: str, measure_index: int) -> tuple[int, int] | None:
    """Return one source measure span by zero-based index."""
    spans = _source_measure_spans(code)
    if measure_index < 0 or measure_index >= len(spans):
        return None
    return spans[measure_index]


def _source_measure_counts(abc: str) -> dict[str, int]:
    """Count source measure fragments independently in the Vocal and Ins voices."""
    lines = abc.splitlines()
    voice_music: dict[str, list[str]] = {"vocal": [], "ins": []}
    active_voice: str | None = None
    for line in lines:
        voice_match = VOICE_PATTERN.fullmatch(line.strip())
        if voice_match is not None:
            token = voice_match.group(1).lower()
            active_voice = token if token in voice_music else VOICE_ALIASES.get(token)
            continue
        if active_voice is None:
            continue
        code = line.partition("%")[0].strip()
        field_match = BODY_FIELD_PREFIX_PATTERN.match(code)
        if not code or field_match is not None:
            continue
        voice_music[active_voice].append(code)
    return {
        voice_name: len(_source_measure_spans(" ".join(lines_for_voice)))
        for voice_name, lines_for_voice in voice_music.items()
    }


def _resynchronized_segment_candidates(
    abc: str,
    header_lines: list[str],
    after_lines: dict[str, int],
    melody_policy: MelodyPolicy,
    max_repair_units: int,
    lyrics_mode: LyricsMode,
    candidate_log: list[dict[str, object]],
) -> list[dict[str, object]]:
    """Strictly convert later voice segments that restart at matching meter declarations."""
    lines = abc.splitlines()
    voice_lines: dict[str, list[tuple[int, str]]] = {"vocal": [], "ins": []}
    meter_events: dict[str, list[tuple[int, int, tuple[int, int], dict[str, str]]]] = {
        "vocal": [],
        "ins": [],
    }
    try:
        headers, _ = ABCScoreParser("extended", max_repair_units)._read_header(lines)
        base_context = {name: headers[name] for name in ("M", "L", "Q", "K")}
    except (ScoreError, KeyError):
        candidate_log.append(
            {"type": "smart_resynchronization", "status": "rejected", "reason": "Invalid ABC header."}
        )
        return []

    contexts = {name: base_context.copy() for name in voice_lines}
    active_voice: str | None = None
    for line_index, line in enumerate(lines):
        voice_match = VOICE_PATTERN.fullmatch(line.strip())
        if voice_match is not None:
            token = voice_match.group(1).lower()
            active_voice = token if token in voice_lines else VOICE_ALIASES.get(token)
            continue
        if active_voice is None:
            continue
        voice_lines[active_voice].append((line_index, line))
        field_match = BODY_FIELD_PREFIX_PATTERN.match(line.strip())
        if field_match is None:
            continue
        field_name, field_value = field_match.groups()
        if field_name not in contexts[active_voice]:
            continue
        contexts[active_voice][field_name] = field_value.strip()
        if field_name != "M" or line_index <= after_lines[active_voice]:
            continue
        try:
            meter = _parse_meter(field_value)
        except ScoreError:
            continue
        entry_index = len(voice_lines[active_voice]) - 1
        meter_events[active_voice].append(
            (line_index, entry_index, meter, contexts[active_voice].copy())
        )

    vocal_events = meter_events["vocal"]
    ins_events = meter_events["ins"]
    pairs: list[tuple[tuple[int, int, tuple[int, int], dict[str, str]], ...]] = []
    next_ins = 0
    for vocal_event in vocal_events:
        while next_ins < len(ins_events) and ins_events[next_ins][2] != vocal_event[2]:
            next_ins += 1
        if next_ins >= len(ins_events):
            break
        pairs.append((vocal_event, ins_events[next_ins]))
        next_ins += 1

    diagnostic_segments: list[dict[str, object]] = []
    for segment_index, (vocal_event, ins_event) in enumerate(pairs, start=1):
        vocal_context = vocal_event[3]
        ins_context = ins_event[3]
        if any(vocal_context[name] != ins_context[name] for name in ("M", "L", "Q", "K")):
            candidate_log.append(
                {
                    "type": "smart_resynchronization",
                    "segment": segment_index,
                    "status": "rejected",
                    "reason": "Voice metadata differs at the proposed meter boundary.",
                }
            )
            continue

        source_parts: dict[str, list[str]] = {}
        source_line_ranges: dict[str, list[int]] = {}
        for voice_name, event in (("vocal", vocal_event), ("ins", ins_event)):
            entries = voice_lines[voice_name]
            next_event = next(
                (item for item in meter_events[voice_name] if item[1] > event[1]),
                None,
            )
            end_entry = next_event[1] if next_event is not None else len(entries)
            selected = entries[event[1]:end_entry]
            if not selected:
                source_parts[voice_name] = []
                source_line_ranges[voice_name] = []
                continue
            source_parts[voice_name] = [item[1] for item in selected]
            source_line_ranges[voice_name] = [selected[0][0] + 1, selected[-1][0] + 1]

        if not source_parts["vocal"] or not source_parts["ins"]:
            continue
        context_header: list[str] = []
        for line in header_lines:
            field_match = BODY_FIELD_PREFIX_PATTERN.match(line.strip())
            if field_match is not None and field_match.group(1) in vocal_context:
                field_name = field_match.group(1)
                prefix = line[:line.find(field_match.group(2))]
                context_header.append(f"{prefix}{vocal_context[field_name]}")
            else:
                context_header.append(line)
        segment_lines = [
            *context_header,
            "V: Vocal",
            *source_parts["vocal"],
            "V: Ins",
            *source_parts["ins"],
        ]
        newline = "\r\n" if "\r\n" in abc else "\n"
        segment_abc = newline.join(segment_lines) + newline
        try:
            converted = _convert_abc_checked(
                segment_abc,
                "strict",
                melody_policy,
                lyrics_mode,
                DEFAULT_PADDING_SECONDS,
                max_repair_units,
            )
        except ScoreError as exc:
            candidate_log.append(
                {
                    "type": "smart_resynchronization",
                    "segment": segment_index,
                    "status": "rejected",
                    "meter": f"{vocal_event[2][0]}/{vocal_event[2][1]}",
                    "source_line_ranges": source_line_ranges,
                    "reason": str(exc),
                }
            )
            continue
        if converted.bars <= 0:
            continue
        diagnostic_segments.append(
            {
                "segment": segment_index,
                "meter": f"{vocal_event[2][0]}/{vocal_event[2][1]}",
                "source_line_ranges": source_line_ranges,
                "converted_measures": converted.bars,
                "abc": converted.abc,
            }
        )
        candidate_log.append(
            {
                "type": "smart_resynchronization",
                "segment": segment_index,
                "status": "converted_diagnostic",
                "meter": f"{vocal_event[2][0]}/{vocal_event[2][1]}",
                "source_line_ranges": source_line_ranges,
                "converted_measures": converted.bars,
                "reason": "Matching meter boundary; isolated segment passed strict conversion.",
            }
        )
    return diagnostic_segments


def _smart_partial_result(
    abc: str,
    reason: str,
    melody_policy: MelodyPolicy,
    max_repair_units: int,
    candidate_log: list[dict[str, object]],
    lyrics_mode: LyricsMode,
) -> ConversionResult | None:
    """Convert a fully aligned prefix and retain the remaining voice blocks verbatim."""
    def reject_partial(reason_text: str) -> None:
        """Record why a conservative structural partial was not possible."""
        candidate_log.append(
            {
                "type": "smart_partial",
                "status": "rejected",
                "reason": reason_text,
            }
        )
        LOGGER.debug(f"SMART_PARTIAL declined: {reason_text}")

    lines = abc.splitlines()
    header_reader = ABCScoreParser("extended", max_repair_units)
    try:
        headers, header_end = header_reader._read_header(lines)
        initial_meter = _parse_meter(headers["M"])
        initial_note_length = header_reader._parse_note_length(headers["L"])
        initial_key = headers["K"]
        _parse_key_signature(initial_key)
        initial_tempo = _parse_tempo(headers["Q"])
    except (ScoreError, KeyError):
        reject_partial("The required ABC header is incomplete or invalid.")
        return None
    header_lines = lines[:header_end + 1]
    bodies: dict[str, list[str]] = {"vocal": [], "ins": []}
    blocks: dict[str, list[tuple[int, int]]] = {"vocal": [], "ins": []}
    active_voice: str | None = None
    active_block_start: int | None = None
    offsets: list[int] = []
    cursor = 0
    for source_line in abc.splitlines(keepends=True):
        offsets.append(cursor)
        cursor += len(source_line)
    offsets.append(cursor)
    for line_index, line in enumerate(lines):
        voice_match = VOICE_PATTERN.fullmatch(line.strip())
        if voice_match is not None:
            if active_voice is not None and active_block_start is not None:
                blocks[active_voice][-1] = (active_block_start, line_index)
            token = voice_match.group(1).lower()
            active_voice = token if token in ("vocal", "ins") else VOICE_ALIASES.get(token)
            active_block_start = line_index if active_voice is not None else None
            if active_voice is not None:
                blocks[active_voice].append((line_index, len(lines)))
            continue
        if active_voice is not None:
            bodies[active_voice].append(line)

    parsed_voices: dict[str, VoiceScore] = {}
    parsed_parsers: dict[str, ABCScoreParser] = {}
    parse_errors: dict[str, str] = {}
    safe_counts: dict[str, int] = {}
    measure_source_lines: dict[str, dict[int, int]] = {"vocal": {}, "ins": {}}
    measure_source_positions: dict[str, dict[int, tuple[int, int, int]]] = {"vocal": {}, "ins": {}}
    body_positions: dict[str, list[int]] = {"vocal": [], "ins": []}
    active_voice = None
    for line_index, line in enumerate(lines):
        voice_match = VOICE_PATTERN.fullmatch(line.strip())
        if voice_match is not None:
            token = voice_match.group(1).lower()
            active_voice = token if token in ("vocal", "ins") else VOICE_ALIASES.get(token)
        elif active_voice is not None:
            body_positions[active_voice].append(line_index)

    for voice_name in ("vocal", "ins"):
        body_lines = bodies[voice_name]
        placeholder_voice = "Ins" if voice_name == "vocal" else "Vocal"
        music_lines = [*header_lines, f"V: {voice_name.title()}", *body_lines]
        if voice_name == "vocal":
            music_lines.extend([f"V: {placeholder_voice}", "Z|"])
        else:
            music_lines = [*header_lines, f"V: {placeholder_voice}", "Z|", f"V: {voice_name.title()}", *body_lines]
        parser = ABCScoreParser("extended", max_repair_units)
        try:
            parser.parse("\n".join(music_lines) + "\n")
        except ScoreError as exc:
            parse_errors[voice_name] = str(exc)
        voice = parser.partial_state.voices.get(voice_name)
        if voice is None:
            continue
        try:
            parser._resolve_ties(voice)
        except ScoreError:
            continue
        records = [item for item in parser.line_records if item.voice == voice_name]
        complete_count = max((max(item.measure_indices, default=-1) + 1 for item in records), default=0)
        synthetic_body_start = len(header_lines) + (1 if voice_name == "vocal" else 3)
        for record in records:
            body_index = record.line_index - synthetic_body_start
            if 0 <= body_index < len(body_positions[voice_name]):
                source_line_index = body_positions[voice_name][body_index]
                for measure_order, measure_index in enumerate(record.measure_indices):
                    measure_source_lines[voice_name][measure_index] = source_line_index
                    measure_source_positions[voice_name][measure_index] = (
                        source_line_index,
                        measure_order,
                        len(record.measure_indices),
                    )
        repaired_measure_numbers = [
            item.measure
            for item in parser.diagnostics
            if item.measure is not None
            and (
                item.repair.startswith("Append a rest")
                or item.repair.startswith("Keep the previous meter")
                or item.repair.startswith("Keep the previous key")
            )
        ]
        if repaired_measure_numbers:
            complete_count = min(complete_count, min(repaired_measure_numbers) - 1)
        if any(item.measure is None and item.code in {"possible_missing_meter_change"} for item in parser.diagnostics):
            complete_count = 0
        if complete_count <= 0:
            continue
        parsed_voices[voice_name] = voice
        parsed_parsers[voice_name] = parser
        safe_counts[voice_name] = min(complete_count, len(voice.measures))

    if len(parsed_voices) != 2:
        reject_partial(
            "Aligned complete-measure prefixes were not available in both voices "
            f"(parsed={sorted(parsed_voices)})."
        )
        return None
    common_count = min(safe_counts.values())
    for index in range(common_count):
        vocal_measure = parsed_voices["vocal"].measures[index]
        ins_measure = parsed_voices["ins"].measures[index]
        if (
            vocal_measure.onset != ins_measure.onset
            or vocal_measure.duration != ins_measure.duration
            or vocal_measure.meter != ins_measure.meter
            or vocal_measure.key != ins_measure.key
        ):
            common_count = index
            break
    if common_count <= 0:
        reject_partial("The two voices have no aligned complete-measure prefix.")
        return None
    prefix_end = parsed_voices["ins"].measures[common_count - 1].onset + parsed_voices["ins"].measures[
        common_count - 1
    ].duration
    voices: dict[str, VoiceScore] = {}
    for voice_name in ("vocal", "ins"):
        source_voice = parsed_voices[voice_name]
        measures = source_voice.measures[:common_count]
        events = [
            event
            for event in source_voice.events
            if event.measure_index < common_count and event.onset + event.duration <= prefix_end
        ]
        voices[voice_name] = VoiceScore(
            name=voice_name,
            meter=measures[-1].meter,
            note_length=measures[-1].note_length,
            key=measures[-1].key,
            cursor=prefix_end,
            measures=measures,
            events=events,
        )
    try:
        _assert_monophonic([event for event in voices["ins"].events if event.kind == "note"], "Ins")
    except ScoreError:
        ins_notes = sorted(
            (event for event in voices["ins"].events if event.kind == "note"),
            key=lambda event: (event.onset, event.source_order),
        )
        conflict = next(
            (
                current
                for previous, current in zip(ins_notes, ins_notes[1:])
                if previous.onset + previous.duration > current.onset
            ),
            None,
        )
        overlap_measure = conflict.measure_index if conflict is not None else 0
        if overlap_measure <= 0:
            reject_partial("Ins overlaps begin in the first recoverable measure.")
            return None
        common_count = overlap_measure
        prefix_end = voices["ins"].measures[common_count - 1].onset + voices["ins"].measures[common_count - 1].duration
        for voice_name in ("vocal", "ins"):
            voices[voice_name].measures = voices[voice_name].measures[:common_count]
            voices[voice_name].events = [
                event
                for event in voices[voice_name].events
                if event.measure_index < common_count and event.onset + event.duration <= prefix_end
            ]
            voices[voice_name].cursor = prefix_end
    tempo_changes: dict[Fraction, TempoChange] = {Fraction(0): initial_tempo}
    for parser in parsed_parsers.values():
        for tempo in parser.tempo_changes:
            if 0 < tempo.onset < prefix_end:
                previous = tempo_changes.get(tempo.onset)
                if previous is not None and (previous.beat_unit, previous.bpm) != (tempo.beat_unit, tempo.bpm):
                    reject_partial("The voices declare conflicting tempo changes in the recoverable prefix.")
                    return None
                tempo_changes[tempo.onset] = tempo
    newline = "\r\n" if "\r\n" in abc else "\n"
    partial_line_records: list[LineRecord] = []
    for voice_name, parser in parsed_parsers.items():
        synthetic_body_start = len(header_lines) + (1 if voice_name == "vocal" else 3)
        for record in parser.line_records:
            body_index = record.line_index - synthetic_body_start
            if not 0 <= body_index < len(body_positions[voice_name]):
                continue
            measure_indices = [index for index in record.measure_indices if index < common_count]
            if not measure_indices:
                continue
            partial_line_records.append(
                LineRecord(
                    body_positions[voice_name][body_index],
                    voice_name,
                    measure_indices,
                    record.comment,
                )
            )
    partial_line_records.sort(key=lambda item: item.line_index)
    partial_record_offsets: dict[int, int] = {}
    partial_cursors = {"vocal": 0, "ins": 0}
    for record in partial_line_records:
        partial_record_offsets[record.line_index] = partial_cursors[record.voice]
        partial_cursors[record.voice] += len(record.measure_indices)
    partial_section_lines = list(lines)
    for line_index, line in enumerate(lines):
        if SECTION_TAG_PATTERN.fullmatch(line) is None:
            continue
        target = next(
            (record for record in partial_line_records if record.line_index > line_index),
            None,
        )
        if target is None or partial_record_offsets[target.line_index] >= common_count:
            partial_section_lines[line_index] = ""
    partial_sections = _extract_sections(partial_section_lines)
    score = ParsedScore(
        source_lines=lines,
        line_records=partial_line_records,
        voices=voices,
        diagnostics=[],
        tempos=[tempo_changes[onset] for onset in sorted(tempo_changes)],
        sections=partial_sections,
        initial_meter=initial_meter,
        initial_note_length=initial_note_length,
        initial_key=initial_key,
        metadata_trailing_comments={},
        newline=newline,
    )
    score_seconds = _score_seconds(score)
    lyrics, section_comment_overrides, lyrics_diagnostics, add_fallback_section_comments = _build_lyrics(
        partial_sections,
        partial_section_lines,
        lyrics_mode,
        score_seconds,
        False,
    )
    spelling_diagnostics: list[Diagnostic] = []
    try:
        instrumental_notes, transfer = _transfer_melody(score, melody_policy)
        converted = _render_score(
            score,
            instrumental_notes,
            section_comment_overrides=section_comment_overrides,
            add_fallback_section_comments=add_fallback_section_comments,
            spelling_diagnostics=spelling_diagnostics,
        )
        _verify_result(
            converted,
            max_repair_units,
            [
                (event.onset, event.duration, event.pitch or 0, event.tied_after, event.spelling)
                for event in voices["ins"].events
                if event.kind == "note"
            ],
            [
                (event.onset, event.chord)
                for voice in voices.values()
                for event in voice.events
                if event.kind == "chord"
            ],
            melody_policy == "instrumental_priority",
            source_score=score,
            expected_ins_notes=instrumental_notes,
        )
    except ScoreError as exc:
        reject_partial(f"The aligned prefix could not be serialized safely: {exc}")
        return None
    spans: list[dict[str, object]] = []
    for voice_name in ("vocal", "ins"):
        voice_blocks = blocks.get(voice_name, [])
        if not voice_blocks:
            continue
        safe_measure_count = common_count
        safe_line = measure_source_lines[voice_name].get(safe_measure_count - 1, -1)
        has_unprocessed_source = bool(parse_errors.get(voice_name)) or safe_measure_count < len(
            parsed_voices[voice_name].measures
        )
        if not has_unprocessed_source:
            continue
        position = measure_source_positions[voice_name].get(safe_measure_count - 1)
        for block_start, block_end in voice_blocks:
            if block_end <= safe_line + 1:
                continue
            start_line = block_start
            start_offset = offsets[block_start]
            if block_start <= safe_line < block_end:
                start_line = safe_line + 1
                start_offset = offsets[start_line] if start_line < block_end else offsets[block_end]
                if position is not None and position[0] == safe_line and position[1] + 1 < position[2]:
                    source_line_index, measure_order, _ = position
                    line_code = lines[source_line_index].partition("%")[0]
                    source_spans = _source_measure_spans(line_code)
                    if measure_order >= len(source_spans):
                        reject_partial("Could not map the preserved suffix back to its original source line.")
                        return None
                    start_line = source_line_index
                    start_offset = offsets[source_line_index] + source_spans[measure_order][1]
            if start_offset >= offsets[block_end]:
                continue
            end_offset = offsets[block_end]
            span_text = abc[start_offset:end_offset]
            if not span_text:
                continue
            spans.append(
                {
                    "start_offset": start_offset,
                    "end_offset": end_offset,
                    "start_line": start_line + 1,
                    "end_line": block_end,
                    "text": span_text,
                    "voice": voice_name,
                    "reason": (
                        "The fragment follows the last aligned, fully parsed measure "
                        "and was retained verbatim."
                    ),
                }
            )
    spans.sort(key=lambda item: int(item["start_offset"]))
    if not spans:
        reject_partial("No unresolved source span remained after the converted prefix.")
        return None
    vocal_count = sum(event.kind == "note" for event in voices["vocal"].events)
    modified_events = sum(
        item.get("status") == "accepted" for item in candidate_log if item.get("type") == "smart_candidate"
    )
    prefix_end_lines = {
        voice_name: measure_source_lines[voice_name].get(common_count - 1, -1)
        for voice_name in ("vocal", "ins")
    }
    diagnostic_segments = _resynchronized_segment_candidates(
        abc,
        header_lines,
        prefix_end_lines,
        melody_policy,
        max_repair_units,
        lyrics_mode,
        candidate_log,
    )
    source_measure_count = max(_source_measure_counts(abc).values(), default=0)
    converted_measures = common_count + sum(
        int(segment["converted_measures"]) for segment in diagnostic_segments
    )
    candidate_log = [
        *candidate_log,
        {
            "type": "smart_partial",
            "status": "raw_fallback_with_diagnostics",
            "reason": "The complete source is the only safe main output; converted sections are diagnostics.",
            "aligned_prefix_measures": common_count,
            "resynchronized_segments": len(diagnostic_segments),
            "verbatim_spans": len(spans),
        },
    ]
    diagnostic = Diagnostic(
        "warning",
        "score",
        common_count,
        "PARTIAL_RAW",
        "The complete source was preserved because a hybrid score could not be assembled safely.",
        "Use the strictly validated prefix and resynchronized segments as diagnostic artifacts only.",
        "smart_partial",
    )
    result = _partial_raw_result(
        abc,
        "smart",
        reason,
        candidate_log,
        lyrics_mode,
    )
    result.repaired_abc = converted
    result.diagnostic_segments = diagnostic_segments
    result.repair_count = modified_events
    result.diagnostics.append(diagnostic)
    result.info += (
        f"; {common_count} aligned measures validated for diagnostics; "
        f"{len(diagnostic_segments)} later segments independently converted; "
        "main ABC preserves the complete source"
    )
    warning_details = json.loads(result.warnings) if result.warnings else {}
    warning_details["transfer"] = transfer
    warning_details.setdefault("diagnostics", []).extend(
        item.to_dict() for item in [*spelling_diagnostics, diagnostic]
    )
    conversion = warning_details.get("conversion", {})
    conversion.update(
        {
            "conversion_status": "PARTIAL_RAW",
            "source_conversion_status": "SMART_PARTIAL",
            "recovery_strategy": "full_source_with_validated_diagnostic_segments",
            "converted_measures": converted_measures,
            "raw_preserved_measures": source_measure_count,
            "resynchronized_segments": len(diagnostic_segments),
            "source_music_coverage": 1.0,
            "output_contains_full_source_music": True,
            "duration_known": False,
            "verified_diagnostic_prefix": bool(converted),
            "diagnostic_segments": [
                {
                    key: value
                    for key, value in segment.items()
                    if key != "abc"
                }
                for segment in diagnostic_segments
            ],
            "internal_validator": "diagnostic_segments_strictly_validated; full source retained raw",
            "vocal_absence_confirmed": False,
            "vocal_notes_in_preserved_prefix": vocal_count,
            "downstream_risk": (
                "The complete raw ABC may contain Vocal notes or unsupported syntax; "
                "diagnostic conversions do not validate the main output."
            ),
        }
    )
    warning_details["conversion"] = conversion
    result.warnings = json.dumps(warning_details, ensure_ascii=False, sort_keys=True)
    return result


def _partial_raw_result(
    abc: str,
    mode: str,
    reason: str,
    candidate_log: list[dict[str, object]] | None = None,
    lyrics_mode: LyricsMode = "sections_only",
    duration_hint: ConversionResult | None = None,
) -> ConversionResult:
    """Return the complete input verbatim when a safe partial conversion is unavailable."""
    lines = abc.splitlines(keepends=True)
    end_line = max(1, len(lines))
    span: dict[str, object] = {
        "start_offset": 0,
        "end_offset": len(abc),
        "start_line": 1,
        "end_line": end_line,
        "text": abc,
        "reason": reason,
    }
    lyrics, section_comment_overrides, lyrics_diagnostics, add_fallback_section_comments = _build_lyrics(
        _extract_sections(abc.splitlines()),
        abc.splitlines(),
        lyrics_mode,
        None,
        False,
    )
    output_abc = (
        _rewrite_raw_section_comments(abc, section_comment_overrides, add_fallback_section_comments)
        if lyrics_mode == "instrumental_sections"
        else abc
    )
    source_preserved = output_abc == abc
    numeric_outputs_known = duration_hint is not None
    max_duration = (
        duration_hint.max_duration
        if duration_hint is not None
        else DEFAULT_PARTIAL_RAW_MAX_DURATION_SECONDS
    )
    score_seconds = duration_hint.score_seconds if duration_hint is not None else 0.0
    bars = duration_hint.bars if duration_hint is not None else 0
    bpm = duration_hint.bpm if duration_hint is not None else 0.0
    numeric_output_source = "safe_parser_estimate" if numeric_outputs_known else "default_300_seconds"
    numeric_output_note = (
        "best-effort values from the safe parse; the raw ABC remains unvalidated"
        if numeric_outputs_known
        else (
            f"duration unknown; max_duration defaulted to {DEFAULT_PARTIAL_RAW_MAX_DURATION_SECONDS:g} seconds; "
            "score_seconds, bars, and bpm remain zero sentinels"
        )
    )
    warnings = json.dumps(
        {
            "diagnostics": [item.to_dict() for item in lyrics_diagnostics],
            "transfer": {},
            "conversion": {
                "conversion_status": "PARTIAL_RAW",
                "mode": mode,
                "raw_source_preserved": source_preserved,
                "unprocessed_spans": [span],
                "converted_measures": 0,
                "raw_preserved_measures": max(_source_measure_counts(abc).values(), default=0),
                "resynchronized_segments": 0,
                "source_music_coverage": 1.0,
                "output_contains_full_source_music": True,
                "duration_known": numeric_outputs_known,
                "source_musical_event_changes": {
                    "removed": 0,
                    "changed": 0,
                    "note": (
                        "the complete input was returned verbatim"
                        if source_preserved
                        else "the complete source was retained with section comment labels rewritten"
                    ),
                },
                "internal_validator": "not_passed",
                "independent_checker": "not_run",
                "vocal_absence_confirmed": False,
                "yue2_runtime_tested": False,
                "section_comments_rewritten": not source_preserved,
                "numeric_output_source": numeric_output_source,
                "numeric_output_note": numeric_output_note,
                "downstream_risk": "YuE2 may reject or misinterpret the unvalidated raw ABC.",
                "unknown_numeric_outputs_use_zero_sentinel": not numeric_outputs_known,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return ConversionResult(
        output_abc,
        lyrics,
        max_duration,
        score_seconds,
        bars,
        bpm,
        "PARTIAL_RAW | "
        + (
            "original ABC preserved verbatim"
            if source_preserved
            else "original ABC retained with section comments rewritten"
        )
        + f"; {numeric_output_note}; Vocal absence is not confirmed.",
        warnings,
        0,
        [],
        "",
        [
            *(candidate_log or []),
            {"type": "raw_fallback", "status": "preserved", "reason": reason},
        ],
        "PARTIAL_RAW",
        [span],
    )


def convert_abc(
    abc: str,
    repair_mode: RepairMode = "extended",
    melody_policy: MelodyPolicy = "instrumental_priority",
    lyrics_mode: LyricsMode = "sections_only",
    padding_seconds: float = DEFAULT_PADDING_SECONDS,
    max_repair_units: int = DEFAULT_MAX_REPAIR_UNITS,
    on_unrecoverable: str = "error",
) -> ConversionResult:
    """Convert a YuE2 ABC score and report whether it is validated or preserved raw."""
    if on_unrecoverable not in {"error", "smart", "continue"}:
        raise ScoreError(f"Unsupported on_unrecoverable policy {on_unrecoverable!r}.")
    if not abc or not abc.strip():
        return ConversionResult("", "", 0.0, 0.0, 0, 0.0, "", "", 0, [])
    if repair_mode not in ("strict", "safe", "extended"):
        raise ScoreError(f"Unknown repair mode {repair_mode!r}.")
    if melody_policy not in ("instrumental_priority", "vocal_priority", "mute_vocal_only"):
        raise ScoreError(f"Unknown melody policy {melody_policy!r}.")
    if lyrics_mode not in {"sections_only", "empty", "instrumental_sections", "instrumental_only"}:
        raise ScoreError(f"Unsupported lyrics mode {lyrics_mode!r}.")
    if max_repair_units < 0:
        raise ScoreError("max_repair_units must be non-negative.")
    if max_repair_units > MAX_REPAIR_UNIT_LIMIT:
        raise ScoreError(f"max_repair_units must not exceed {MAX_REPAIR_UNIT_LIMIT}.")
    if not math.isfinite(float(padding_seconds)) or padding_seconds < 0:
        raise ScoreError("padding_seconds must be a finite non-negative number.")
    if padding_seconds > MAX_PADDING_SECONDS:
        raise ScoreError(f"padding_seconds must not exceed {MAX_PADDING_SECONDS:g} seconds.")

    attempt_mode: RepairMode = (
        "extended" if on_unrecoverable == "smart" else "safe" if on_unrecoverable == "continue" else repair_mode
    )
    try:
        result = _convert_abc_checked(
            abc,
            attempt_mode,
            melody_policy,
            lyrics_mode,
            padding_seconds,
            max_repair_units,
        )
    except ScoreError as exc:
        if on_unrecoverable == "error":
            raise
        if on_unrecoverable == "smart":
            strategy = _SmartRecoveryStrategy(
                repair_mode="extended",
                melody_policy=melody_policy,
                lyrics_mode=lyrics_mode,
                padding_seconds=padding_seconds,
                max_repair_units=max_repair_units,
            )
            return strategy.run(abc, str(exc))
        return _partial_raw_result(abc, on_unrecoverable, str(exc), lyrics_mode=lyrics_mode)

    if result.conversion_status in {"SMART_PARTIAL", "PARTIAL_RAW", "SMART_RECOVERED"}:
        return result

    if on_unrecoverable == "smart":
        problems = [item.problem for item in result.diagnostics]
        has_overflow = any("Measure duration exceeds its meter" in problem for problem in problems)
        has_grid_mismatch = any("source grids differ" in problem for problem in problems)
        if has_overflow and has_grid_mismatch:
            strategy = _SmartRecoveryStrategy(
                repair_mode="extended",
                melody_policy=melody_policy,
                lyrics_mode=lyrics_mode,
                padding_seconds=padding_seconds,
                max_repair_units=max_repair_units,
            )
            parser = ABCScoreParser("extended", max_repair_units)
            newline = "\r\n" if "\r\n" in abc else "\n"
            proposals = strategy._barline_candidates(abc, parser, abc.splitlines(), newline)
            for proposal in proposals:
                try:
                    ABCScoreParser("strict", max_repair_units).parse(proposal.text)
                    candidate = _convert_abc_checked(
                        proposal.text, "extended", melody_policy, lyrics_mode, padding_seconds, max_repair_units
                    )
                except ScoreError:
                    continue
                strategy._mark_smart_recovered(candidate, [], [proposal.reason], len(proposals), [])
                return candidate

    if on_unrecoverable == "continue":
        uncertain_repairs = [
            item
            for item in result.diagnostics
            if item.severity == "warning"
            and item.code not in NON_MUSICAL_WARNING_CODES
            and not item.repair.startswith((
                "Remove the quote delimiters at token level",
                "Insert a missing opening quote around the chord symbol",
                "Drop the orphan quote as a token-level repair",
                "Drop the empty chord label",
                "Keep the valid metadata prefix and preserve the trailing text as a comment",
                "Split the valid metadata prefix and parse the remaining music tokens",
            ))
        ]
        if uncertain_repairs:
            reasons = "; ".join(dict.fromkeys(item.problem for item in uncertain_repairs))
            return _partial_raw_result(
                abc,
                on_unrecoverable,
                f"Continue mode declined heuristic changes: {reasons}",
                lyrics_mode=lyrics_mode,
                duration_hint=result,
            )

    smart_repairs = any(
        item.severity == "warning" and item.code not in NON_MUSICAL_WARNING_CODES and item.repair
        for item in result.diagnostics
    )
    status = "SMART_RECOVERED" if on_unrecoverable == "smart" and smart_repairs else "OK"
    result.conversion_status = status
    details = json.loads(result.warnings) if result.warnings else {}
    conversion = details.get("conversion", {})
    conversion.update(
        {
            "conversion_status": status,
            "mode": on_unrecoverable,
            "raw_source_preserved": False,
            "unprocessed_spans": [],
            "vocal_absence_confirmed": True,
        }
    )
    details["conversion"] = conversion
    result.warnings = json.dumps(details, ensure_ascii=False, sort_keys=True)
    if status == "SMART_RECOVERED":
        result.info = f"SMART_RECOVERED | {result.info}"
    return result


def _save_debug_artifacts(abc: str, result: ConversionResult) -> Path:
    """Save node diagnostics under ComfyUI's output directory without overwriting prior runs."""
    try:
        folder_paths = importlib.import_module("folder_paths")
        output_directory = Path(folder_paths.get_output_directory()).resolve()
        output_directory.mkdir(parents=True, exist_ok=True)
        artifact_directory = Path(
            tempfile.mkdtemp(prefix="yue2_auto_instrumental_", dir=output_directory)
        ).resolve()
        artifact_directory.relative_to(output_directory)
    except Exception as exc:
        LOGGER.error(
            f"Unable to create YuE2 debug artifact directory under the ComfyUI output directory: "
            f"{exc}\n{traceback.format_exc()}"
        )
        raise

    try:
        warning_details = json.loads(result.warnings) if result.warnings else {}
    except json.JSONDecodeError as exc:
        LOGGER.error(
            f"Unable to decode conversion warnings for debug artifacts: {exc}\n{traceback.format_exc()}"
        )
        raise
    partial = result.conversion_status in {"PARTIAL_RAW", "SMART_PARTIAL"}
    repairs = {
        "diagnostics": [diagnostic.to_dict() for diagnostic in result.diagnostics],
        "candidates": result.candidate_log,
        "transfer": warning_details.get("transfer", {}),
        "conversion": warning_details.get("conversion", {}),
        "verbatim_spans": result.verbatim_spans,
        "diagnostic_segments": [
            {key: value for key, value in segment.items() if key != "abc"}
            for segment in result.diagnostic_segments
        ],
    }
    validation = {
        "status": result.conversion_status,
        "category": result.conversion_status,
        "source_conversion_status": warning_details.get("conversion", {}).get(
            "source_conversion_status",
            "",
        ),
        "internal_validator": (
            warning_details.get("conversion", {}).get("internal_validator", "not_passed")
            if partial
            else "passed"
        ),
        "independent_yue_checker": "not_run",
        "yue2_runtime_tested": False,
        "source_sha256": hashlib.sha256(abc.encode("utf-8")).hexdigest(),
        "score_seconds": (
            result.score_seconds
            if result.conversion_status == "SMART_PARTIAL" or not partial
            else None
        ),
        "bars": result.bars if result.conversion_status == "SMART_PARTIAL" or not partial else None,
        "repairs": result.repair_count,
        "vocal_absence_confirmed": (
            warning_details.get("conversion", {}).get("vocal_absence_confirmed", False)
            if partial
            else True
        ),
        "converted_measures": warning_details.get("conversion", {}).get("converted_measures", 0),
        "raw_preserved_measures": warning_details.get("conversion", {}).get("raw_preserved_measures", 0),
        "resynchronized_segments": warning_details.get("conversion", {}).get("resynchronized_segments", 0),
        "source_music_coverage": warning_details.get("conversion", {}).get("source_music_coverage", 0.0),
        "output_contains_full_source_music": warning_details.get("conversion", {}).get(
            "output_contains_full_source_music",
            False,
        ),
        "duration_known": warning_details.get("conversion", {}).get("duration_known", False),
    }
    files = {
        "source.abc": abc,
        "repairs.json": json.dumps(repairs, ensure_ascii=False, indent=2, sort_keys=True),
        "validation.json": json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True),
    }
    if result.conversion_status == "PARTIAL_RAW":
        files["partial_raw.abc"] = result.abc
        if result.repaired_abc:
            files["verified_prefix.abc"] = result.repaired_abc
        for segment in result.diagnostic_segments:
            segment_id = int(segment["segment"])
            files[f"resynchronized_segment_{segment_id:03d}.abc"] = str(segment["abc"])
    elif result.conversion_status == "SMART_PARTIAL":
        files["repaired.abc"] = result.repaired_abc
        files["converted.abc"] = result.abc
        files["partial_smart.abc"] = result.abc
    else:
        files["repaired.abc"] = result.repaired_abc
        files["converted.abc"] = result.abc
    for file_name, content in files.items():
        file_path = artifact_directory / file_name
        try:
            with file_path.open("x", encoding="utf-8", newline="") as handle:
                handle.write(content)
        except Exception as exc:
            LOGGER.error(
                f"Unable to write YuE debug artifact {file_path}: {exc}\n{traceback.format_exc()}"
            )
            raise
    LOGGER.info(f"Saved YuE2 debug artifacts to {artifact_directory}")
    return artifact_directory


class VHSXYuE2AutoInstrumental:
    """Prepare YuE2 ABC input as an instrumental ComfyUI workflow node."""

    RETURN_TYPES = (
        "STRING",
        "STRING",
        "FLOAT",
        "FLOAT",
        "INT",
        "FLOAT",
        "STRING",
        "STRING",
        "INT",
        "STRING",
    )
    RETURN_NAMES = (
        "abc",
        "lyrics",
        "max_duration",
        "score_seconds",
        "bars",
        "bpm",
        "info",
        "warnings",
        "repair_count",
        "conversion_status",
    )
    FUNCTION = "convert"
    CATEGORY = "Video VHS Extended/Audio"

    @classmethod
    def INPUT_TYPES(cls) -> dict[str, dict[str, tuple[object, ...]]]:
        """Describe ComfyUI inputs, defaults, bounds, and user-facing hints."""
        return {
            "required": {
                "abc": (
                    "STRING",
                    {
                        "multiline": True,
                        "forceInput": True,
                        "tooltip": (
                            "YuE2 ABC score with Vocal and Ins voices. Empty input returns empty strings "
                            "and zero numeric outputs."
                        ),
                    },
                ),
                "repair_mode": (
                    ["strict", "safe", "extended"],
                    {
                        "default": "extended",
                        "tooltip": (
                            "strict rejects incomplete or overfull bars and invalid ties; safe applies deterministic "
                            "repairs such as padding short bars or trimming trailing rests; extended also tests "
                            "bounded repairs for missing barlines, overflows, and damaged metadata. Ambiguous Ins "
                            "changes are rejected. on_unrecoverable=smart forces extended; continue forces safe."
                        ),
                    },
                ),
                "melody_policy": (
                    ["instrumental_priority", "vocal_priority", "mute_vocal_only"],
                    {
                        "default": "instrumental_priority",
                        "tooltip": "Choose whether Ins, Vocal, or neither melody wins overlaps.",
                    },
                ),
                "lyrics_mode": (
                    ["sections_only", "empty", "instrumental_sections", "instrumental_only"],
                    {
                        "default": "sections_only",
                        "tooltip": (
                            "sections_only keeps section tags; empty returns blank Lyrics; instrumental_sections "
                            "maps tags in ABC and Lyrics; instrumental_only generates Intro, duration-based "
                            "Instrumental sections, and Outro without changing ABC."
                        ),
                    },
                ),
                "padding_seconds": (
                    "FLOAT",
                    {
                        "default": DEFAULT_PADDING_SECONDS,
                        "min": 0.0,
                        "max": MAX_PADDING_SECONDS,
                        "step": 0.1,
                        "tooltip": "Add seconds before rounding up max_duration.",
                    },
                ),
                "max_repair_units": (
                    "INT",
                    {
                        "default": DEFAULT_MAX_REPAIR_UNITS,
                        "min": 0,
                        "max": MAX_REPAIR_UNIT_LIMIT,
                        "step": 1,
                        "tooltip": (
                            "Limit only heuristic Vocal note shortening in units of the active L: note length."
                        ),
                    },
                ),
                "on_unrecoverable": (
                    ["error", "smart", "continue"],
                    {
                        "default": "error",
                        "tooltip": (
                            "error raises on failure; smart uses extended recovery and may return SMART_RECOVERED "
                            "or PARTIAL_RAW with validated diagnostic segments; continue uses safe and returns "
                            "PARTIAL_RAW for uncertain repairs. "
                            "PARTIAL_RAW keeps the ABC; if safe parsing succeeds, durations are best-effort and "
                            "unvalidated. If duration is unknown, max_duration defaults to 300 seconds; zero "
                            "score_seconds, bars, or bpm values mean unknown. Vocal absence is unconfirmed."
                        ),
                    },
                ),
            },
            "optional": {
                "save_debug_artifacts": (
                    "BOOLEAN",
                    {
                        "default": False,
                        "tooltip": (
                            "Save source and diagnostics under ComfyUI output. PARTIAL_RAW writes partial_raw.abc "
                            "and may include verified_prefix.abc and resynchronized segment files; validated "
                            "results write repaired.abc and converted.abc."
                        ),
                    },
                ),
            },
        }

    def convert(
        self,
        abc: str,
        repair_mode: RepairMode = "extended",
        melody_policy: MelodyPolicy = "instrumental_priority",
        lyrics_mode: LyricsMode = "sections_only",
        padding_seconds: float = DEFAULT_PADDING_SECONDS,
        max_repair_units: int = DEFAULT_MAX_REPAIR_UNITS,
        on_unrecoverable: str = "error",
        save_debug_artifacts: bool = False,
    ) -> tuple[str, str, float, float, int, float, str, str, int, str]:
        """Run conversion and return ComfyUI outputs in their declared order."""
        try:
            result = convert_abc(
                abc,
                repair_mode,
                melody_policy,
                lyrics_mode,
                padding_seconds,
                max_repair_units,
                on_unrecoverable,
            )
            if save_debug_artifacts:
                artifact_directory = _save_debug_artifacts(abc, result)
                result.info = f"{result.info} | debug artifacts: {artifact_directory}"
            return (
                result.abc,
                result.lyrics,
                result.max_duration,
                result.score_seconds,
                result.bars,
                result.bpm,
                result.info,
                result.warnings,
                result.repair_count,
                result.conversion_status,
            )
        except ScoreError as exc:
            LOGGER.error(f"YuE2 Auto Instrumental rejected ABC input: {exc}")
            raise
        except Exception as exc:
            LOGGER.error(
                f"Unexpected YuE2 Auto Instrumental failure: {exc}\n{traceback.format_exc()}"
            )
            raise
