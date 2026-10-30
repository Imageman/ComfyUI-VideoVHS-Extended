"""Convert YuE2 ABC scores to instrumental scores without requiring ComfyUI."""

from __future__ import annotations

import hashlib
import importlib
import json
import logging
import math
import re
import tempfile
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
MAX_PADDING_SECONDS = 30.0
MIN_MAX_DURATION_SECONDS = 0.04
MAX_REPAIR_UNIT_LIMIT = 32
MAX_REPAIR_CANDIDATES = 32
MAX_REPAIR_LOOKAHEAD_MEASURES = 4
MAX_REPAIR_SUFFIX_EVENTS = 20_000
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
    """Extract supported section names from ABC percent-comment lines."""
    sections: list[str] = []
    for line in lines:
        match = re.fullmatch(r"\s*%\s*([A-Za-z][A-Za-z _-]*)\s*", line)
        if not match:
            continue
        name = re.sub(r"\s+", " ", match.group(1).strip().lower())
        name = SECTION_ALIASES.get(name, name)
        if name in KNOWN_SECTIONS:
            sections.append(name)
    return sections or ["intro", "outro"]


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
                    spelling=_pitch_to_token(note.pitch or 0),
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
                    spelling=_pitch_to_token(note.pitch or 0),
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
                spelling=_pitch_to_token(note.pitch or 0),
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
) -> str:
    """Format a pitched duration with supported units and ties between exact chunks."""
    parts = _duration_parts(duration, note_length)
    if parts is None:
        return spelling + _format_length(duration, note_length) + ("-" if tie_after else "")
    tokens = []
    for index, part in enumerate(parts):
        suffix = "" if part == 1 else str(part)
        continues = index + 1 < len(parts) or tie_after
        tokens.append(f"{spelling}{suffix}{'-' if continues else ''}")
    return "".join(tokens)


def _format_supported_rest(duration: Fraction, note_length: Fraction) -> str:
    """Format a rest duration with supported units without adding sound events."""
    parts = _duration_parts(duration, note_length)
    if parts is None:
        return "z" + _format_length(duration, note_length)
    return "".join("z" + ("" if part == 1 else str(part)) for part in parts)


def _render_measure(
    measure: Measure,
    events: list[ScoreEvent],
    chords: list[ScoreEvent],
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
            pieces.append(
                _format_supported_note(
                    _pitch_to_token(note.pitch or 0),
                    next_position - position,
                    measure.note_length,
                    continues,
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
            _render_measure(measure, vocal_notes or [], vocal_chords)
            for measure in measures
        ]
        output_lines.append("|".join(_compress_measure_rests(vocal_bars)) + "|")
        output_lines.append("V: Ins")
        fields, metadata["ins"] = _metadata_field_lines(measures[0], metadata["ins"])
        output_lines.extend(fields)
        ins_bars = [_render_measure(measure, instrumental_notes, []) for measure in measures]
        output_lines.append("|".join(_compress_measure_rests(ins_bars)) + "|")
        index = end

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
    original_ins: list[tuple[Fraction, Fraction, int, bool]],
    original_chords: list[tuple[Fraction, str]],
    preserve_ins: bool,
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
    if preserve_ins:
        final_ins = Counter(
            (event.onset, event.duration, event.pitch or 0, event.tied_after)
            for event in ins_notes
        )
        if Counter(original_ins) - final_ins:
            raise ScoreError("Final score changed an original Ins note under instrumental_priority.")


def _convert_abc_checked(
    abc: str,
    repair_mode: RepairMode = "extended",
    melody_policy: MelodyPolicy = "instrumental_priority",
    lyrics_mode: str = "sections_only",
    padding_seconds: float = DEFAULT_PADDING_SECONDS,
    max_repair_units: int = DEFAULT_MAX_REPAIR_UNITS,
) -> ConversionResult:
    """Convert one ABC score and validate its serialized representation."""
    if lyrics_mode != "sections_only":
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
        (event.onset, event.duration, event.pitch or 0, event.tied_after)
        for event in ins_before
    ]
    original_chords = [
        (event.onset, event.chord)
        for voice in score.voices.values()
        for event in voice.events
        if event.kind == "chord"
    ]
    original_chords.sort(key=lambda item: (item[0], item[1]))
    try:
        repaired = _render_score(score, ins_before, vocal_before)
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
    converted = _render_score(score, instrumental_notes)
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
    _verify_result(converted, max_repair_units, original_ins, original_chords, preserve_ins)

    score_seconds = _score_seconds(score)
    max_duration = float(
        max(
            MIN_MAX_DURATION_SECONDS,
            min(MAX_DURATION_SECONDS, math.ceil(score_seconds + padding_seconds)),
        )
    )
    grid = _build_grid(score)
    sections = score.sections
    lyrics = "\n".join(f"[{section}]" for section in sections)
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
    diagnostics = list(score.diagnostics)
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


def _partial_raw_result(abc: str, mode: str, reason: str) -> ConversionResult:
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
    lyrics = "\n".join(f"[{section}]" for section in _extract_sections(abc.splitlines()))
    warnings = json.dumps(
        {
            "diagnostics": [],
            "transfer": {},
            "conversion": {
                "conversion_status": "PARTIAL_RAW",
                "mode": mode,
                "raw_source_preserved": True,
                "unprocessed_spans": [span],
                "source_musical_event_changes": {
                    "removed": 0,
                    "changed": 0,
                    "note": "the complete input was returned verbatim",
                },
                "internal_validator": "not_passed",
                "independent_checker": "not_run",
                "vocal_absence_confirmed": False,
                "yue2_runtime_tested": False,
                "downstream_risk": "YuE2 may reject or misinterpret the unvalidated raw ABC.",
                "unknown_numeric_outputs_use_zero_sentinel": True,
            },
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return ConversionResult(
        abc,
        lyrics,
        0.0,
        0.0,
        0,
        0.0,
        "PARTIAL_RAW | original ABC preserved verbatim; numeric outputs are unknown (0 sentinel); "
        "Vocal absence is not confirmed.",
        warnings,
        0,
        [],
        "",
        [{"type": "raw_fallback", "status": "preserved", "reason": reason}],
        "PARTIAL_RAW",
        [span],
    )


def convert_abc(
    abc: str,
    repair_mode: RepairMode = "extended",
    melody_policy: MelodyPolicy = "instrumental_priority",
    lyrics_mode: str = "sections_only",
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
    if lyrics_mode != "sections_only":
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
        return _partial_raw_result(abc, on_unrecoverable, str(exc))

    if on_unrecoverable == "continue":
        uncertain_repairs = [
            item
            for item in result.diagnostics
            if item.severity == "warning"
            and item.code != "unsupported_chord_quality"
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
            return _partial_raw_result(abc, on_unrecoverable, f"Continue mode declined heuristic changes: {reasons}")

    smart_repairs = any(
        item.severity == "warning" and item.code != "unsupported_chord_quality" and item.repair
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
    partial = result.conversion_status == "PARTIAL_RAW"
    repairs = {
        "diagnostics": [diagnostic.to_dict() for diagnostic in result.diagnostics],
        "candidates": result.candidate_log,
        "transfer": warning_details.get("transfer", {}),
        "conversion": warning_details.get("conversion", {}),
        "verbatim_spans": result.verbatim_spans,
    }
    validation = {
        "status": result.conversion_status,
        "category": result.conversion_status,
        "internal_validator": "not_passed" if partial else "passed",
        "independent_yue_checker": "not_run",
        "yue2_runtime_tested": False,
        "source_sha256": hashlib.sha256(abc.encode("utf-8")).hexdigest(),
        "score_seconds": None if partial else result.score_seconds,
        "bars": None if partial else result.bars,
        "repairs": result.repair_count,
        "vocal_absence_confirmed": not partial,
    }
    files = {
        "source.abc": abc,
        "repairs.json": json.dumps(repairs, ensure_ascii=False, indent=2, sort_keys=True),
        "validation.json": json.dumps(validation, ensure_ascii=False, indent=2, sort_keys=True),
    }
    if partial:
        files["partial_raw.abc"] = result.abc
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
                    {"default": "extended", "tooltip": "Choose how malformed measures and ties are handled."},
                ),
                "melody_policy": (
                    ["instrumental_priority", "vocal_priority", "mute_vocal_only"],
                    {
                        "default": "instrumental_priority",
                        "tooltip": "Choose whether Ins, Vocal, or neither melody wins overlaps.",
                    },
                ),
                "lyrics_mode": (
                    ["sections_only"],
                    {"default": "sections_only", "tooltip": "Keep section tags only; remove sung words."},
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
                            "error raises on failure; smart uses extended recovery; continue preserves the full "
                            "raw ABC when only uncertain repairs remain."
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
                            "Save source and diagnostics under ComfyUI output. PARTIAL_RAW writes partial_raw.abc; "
                            "validated results write repaired.abc and converted.abc."
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
        lyrics_mode: str = "sections_only",
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
