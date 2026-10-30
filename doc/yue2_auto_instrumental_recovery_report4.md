# YuE2 Auto Instrumental recovery evaluation

Corpus: all 329 current files in `tmp/raw_abc`. File IDs 00001–00096 are development; IDs above 00096 are
holdout. Split assignment uses filenames only.

The holdout is retrospective, not blind: one aggregate development run included newly added files before the
corpus update was recognized. Per-file holdout diagnostics were not used to tune recovery.

| Split | N | Strict | Syntax ok | Converted | Output ok | Ins kept | Vocal gone | Rejected | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| development | 96 | 25 | 60 (62.5%) | 59 (61.5%) | 59/59 | 25/25 | 59/59 | 37 | 0 |
| holdout | 233 | 64 | 160 (68.7%) | 157 (67.4%) | 157/157 | 64/64 | 157/157 | 76 | 0 |

Synthetic exact event recovery: 10/10 known-source cases. Development: 6/6; synthetic holdout: 4/4. Expected
safe rejections: 1; false successes: 0.

The previous implementation accepted 56/96 files (58.3%). This pass is compared below; acceptance alone is not
treated as recovery accuracy.

## Corpus counts

| Split | N | Syntax ok | Converted | Repaired | Heuristic | Ins kept | Vocal gone | Rejected | Errors |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| development | 96 | 60 | 59 | 38 | 2 | 25/25 | 59 | 37 | 0 |
| holdout | 233 | 160 | 157 | 97 | 7 | 64/64 | 157 | 76 | 0 |

## Rejection categories

| Split | Category | Count |
|---|---|---:|
| development | chord syntax | 4 |
| development | event overlap | 1 |
| development | timing structure | 9 |
| development | unsupported token | 14 |
| development | voice alignment | 9 |
| holdout | chord syntax | 5 |
| holdout | event overlap | 4 |
| holdout | metadata | 1 |
| holdout | timing structure | 24 |
| holdout | unsupported token | 31 |
| holdout | voice alignment | 11 |

## First rejected inputs per split

Diagnostic examples are shown as bullets so long parser messages remain readable.
- `YuE2_ABC_00003.txt` (timing structure): Measure 41 in vocal is short by 1/32 whole-note units and ends with
  a tie; no aligned Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00005.txt` (timing structure): Measure 14 in vocal is short by 1/32 whole-note units and ends with
  a tie; no aligned Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00007.txt` (unsupported token): Unsupported ABC notation at line 51, near ',4'.
- `YuE2_ABC_00010.txt` (unsupported token): Unsupported ABC notation at line 69, near 'sus4'.
- `YuE2_ABC_00013.txt` (unsupported token): Unsupported ABC notation at line 40, near 'im7'.
- `YuE2_ABC_00014.txt` (unsupported token): Unsupported ABC notation at line 32, near "'16-_".
- `YuE2_ABC_00018.txt` (timing structure): Measure 15 in vocal is short by 1/16 whole-note units and ends with
  a tie; no aligned Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00021.txt` (voice alignment): Measure 19 in ins has an overhanging note that overlaps another
  note; no candidate preserves Ins and the monophonic timeline.
- `YuE2_ABC_00028.txt` (timing structure): Measure 46 in vocal is short by 1/32 whole-note units and ends with
  a tie; no aligned Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00029.txt` (timing structure): Aligned short measures in Vocal and Ins suggest a missing meter
  change; the time-signature grouping is ambiguous, so no rest was added.
- `YuE2_ABC_00031.txt` (voice alignment): Measure 20 in ins has an overhanging note that overlaps another
  note; no candidate preserves Ins and the monophonic timeline.
- `YuE2_ABC_00032.txt` (unsupported token): Unsupported ABC notation at line 96, near ',1,4-'.
- `YuE2_ABC_00097.txt` (chord syntax): Unsupported chord symbol '"A#/ug"' at line 28.
- `YuE2_ABC_00101.txt` (chord syntax): Unterminated chord symbol at line 67.
- `YuE2_ABC_00103.txt` (unsupported token): Unsupported ABC notation at line 74, near "'3'2".
- `YuE2_ABC_00109.txt` (unsupported token): Unsupported ABC notation at line 64, near "'4".
- `YuE2_ABC_00113.txt` (timing structure): Measure 18 in ins is short by 1/8 whole-note units and ends with a
  tie; no aligned Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00114.txt` (timing structure): Measure 21 in ins is short by 1/16 whole-note units and ends with a
  tie; no aligned Vocal and Ins meter pattern resolves the missing duration, so padding would break the tie.
- `YuE2_ABC_00123.txt` (unsupported token): Unsupported ABC notation at line 77, near '""'.
- `YuE2_ABC_00125.txt` (unsupported token): Unsupported ABC notation at line 28, near ',3,3'.
- `YuE2_ABC_00127.txt` (unsupported token): Unsupported ABC notation at line 53, near ',2'.
- `YuE2_ABC_00133.txt` (unsupported token): Unsupported ABC notation at line 37, near ',4-'.
- `YuE2_ABC_00134.txt` (voice alignment): Measure 48 in ins has an overhanging note that overlaps another
  note; no candidate preserves Ins and the monophonic timeline.
- `YuE2_ABC_00136.txt` (chord syntax): Unsupported chord symbol '"A#/e#"' at line 118.

## Synthetic cases

| Split | Case | Event result | Output validation |
|---|---|---|---|
| development | deleted_barline_with_tied_note | exact | valid, Vocal silent |
| development | repeat_barline_variant | exact | valid, Vocal silent |
| development | equivalent_dotted_duration | exact | valid, Vocal silent |
| development | chord_on_full_measure_rest | exact | valid, Vocal silent |
| development | missing_boundary_before_full_bar_rest | exact | valid, Vocal silent |
| development | empty_chord_token | exact | valid, Vocal silent |
| holdout | voice_identifier_alias | exact | valid, Vocal silent |
| holdout | orphan_quote_before_note | exact | valid, Vocal silent |
| holdout | invalid_tie_marker | exact | valid, Vocal silent |
| holdout | compressed_measure_rest | exact | valid, Vocal silent |
| holdout | overlapping_ins_notes_must_reject | safe rejection | n/a |

## Limits

Synthetic coverage measures exact event recovery for the listed transformations. Real damaged corpus files
have no known correct score, so their exact reconstruction accuracy is unknown. Unmatched chord text,
conflicting Ins overlaps, unsupported pitch syntax, and ambiguous metadata remain diagnostic failures rather
than guessed notes.
