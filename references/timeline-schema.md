# Timeline formats

## Primary CSV

`timeline.csv` is UTF-8 with BOM for reliable Chinese display in spreadsheet apps.

| Column | Meaning |
| --- | --- |
| `id` | Stable one-based row label such as `lyric-01` |
| `section` | Suno section heading, or empty |
| `start_time` | Actual singing start as `MM:SS.mmm` |
| `end_time` | Singing end as `MM:SS.mmm` |
| `lyric` | Canonical text copied from Suno |

## Internal JSON

The extractor writes source filenames (never absolute user paths), media hashes and durations, `pipeline_fingerprint`, `alignment_summary`, and an ordered `cues` array. Each cue contains:

- `index`: one-based lyric order.
- `text`: canonical line copied from Suno.
- `section`: copied section label when present, otherwise `null`.
- `start_ms`, `end_ms`: calibrated half-open interval `[start_ms, end_ms)`.
- `video_start_ms`: coarse boundary inferred from lyric highlight/scroll state.
- `text_source`: `suno_lyrics_confirmed_by_video` or `suno_lyrics_interpolated_from_video`.
- `video_ocr_text`: OCR evidence retained for audit, never the canonical output.
- `lyrics_video_similarity`: normalized comparison between canonical text and OCR evidence.
- `timing_source`: `vocal_alignment` when moved, otherwise `video_highlight`.
- `end_timing_source`: `vocal_offset`, `next_line_start_fallback`, or `media_duration_fallback`.
- `confidence`: combined OCR/text similarity confidence from 0 to 1.
- `flags`: explicit warnings requiring review.

Require non-negative, strictly increasing starts and `start_ms < end_ms <= media_duration_ms`.

`alignment_summary` reports canonical lyric count, OCR anchor count, confirmed count, interpolated count, and confirmed ratio. Structural validity does not mean interpolated lines are accurate; review every interpolated cue.

The package also contains `lyrics.txt`, media hashes, source metadata, CSV/LRC/SRT/VTT exports, and `validation.json`. Validation parses every export and checks its text and timestamps against JSON. A reviewed CSV or TypeScript timeline is never part of the generation package; it may only be passed separately to `evaluate_timeline.py` afterward.
