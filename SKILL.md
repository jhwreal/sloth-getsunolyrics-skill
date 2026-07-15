---
name: sloth-getsunolyrics-skill
description: Use Computer Use to find a named song in the user's logged-in Suno account, copy its lyrics, download its lyric video and Suno-separated lead vocal, then generate a validated timestamped lyric CSV plus JSON/LRC/SRT/VTT. Use when a user wants reusable lyric timing for games, karaoke, subtitles, editing, or visualization, or when evaluating generated timing against a separate human-reviewed gold timeline.
---

# Sloth Get Suno Lyrics

Turn a Suno song name into a timestamped lyric package. The normal user supplies only the song name and has Suno open and logged in. Do not ask the user to install Demucs or manually prepare media.

Use three independent signals:

1. Lyrics copied from the visible Suno song page are the canonical text, line order, case, punctuation, and repetitions.
2. The lyric MP4 supplies visible highlight/scroll timing and confirms the copied line sequence.
3. The Suno lead-vocal stem supplies vocal activity used to calibrate line starts and reject boundaries that land in silence.

Never accept a reviewed timestamp CSV or TypeScript timeline as a production input. Gold timelines exist only for evaluation after generation.

## Prerequisites

Require only:

- Codex Desktop with Computer Use available.
- Chrome open with the user's Suno account already logged in.
- The named song visible to that account, with lyrics, Video download, and stem extraction access.
- Enough Suno credits or plan entitlement if Suno charges for stem extraction.

Do not request the user's password, cookie, or token. Do not ask a normal user to install a browser extension, Demucs, Python packages, or audio models. If Suno shows a credit charge, purchase, or upgrade before the final extraction action, show the exact cost and ask for confirmation at that point.

## Acquire the song with Computer Use

Read [references/suno-computer-use-workflow.md](references/suno-computer-use-workflow.md). Load the `computer-use:computer-use` skill and use Computer Use for every Chrome/Suno action. Operate the visible interface like a person; do not depend on private API endpoints, undocumented CDN URLs, or brittle DOM selectors.

The acquisition sequence is:

1. Search the user's Suno library for the requested song name.
2. Disambiguate duplicate titles using visible creator, version, date, cover, and duration. Never guess.
3. Open the exact song and copy its displayed lyrics into UTF-8 `lyrics.txt` without timestamps.
4. Download `Video` from the visible Download menu and wait for the completed MP4.
5. Open Suno's stem extraction UI, select the full-song lead vocal, run extraction, and download the completed vocal WAV.
6. Verify the MP4 and vocal stem refer to the same song and begin at the same media time zero.

Keep downloads in the user's lyric-processing workspace, not inside this Skill repository. Treat the original files as read-only.

## Generate the timeline

After acquisition, run:

```bash
python3 scripts/process_song.py \
  --video /absolute/path/song.mp4 \
  --vocals /absolute/path/lead-vocal.wav \
  --lyrics /absolute/path/lyrics.txt \
  --output-dir /absolute/path/song-package \
  --language auto \
  --source-url 'https://suno.com/song/SONG_UUID' \
  --title 'Song title'
```

Use `--language zh` or `--language en` when known. Use `--interval 0.25` for rapidly changing lyrics; the default is `0.5` seconds. Use `--resume` after interruption. Resume is allowed only when the packaged lyric hash still matches; changed lyrics force timeline regeneration.

The primary deliverable is `timeline.csv`:

```csv
id,section,start_time,end_time,lyric
lyric-01,Verse 1,00:12.340,00:15.670,歌词内容
```

The package also contains:

```text
song-package/
├── song.mp4
├── vocals.wav
├── lyrics.txt
├── timeline.csv
├── timeline.json
├── timeline.lrc
├── timeline.srt
├── timeline.vtt
├── vocals.separation.json
├── manifest.json
├── validation.json
└── work/
```

Preserve `work/` until review finishes. It contains OCR frames and observations needed to diagnose alignment.

## Alignment rules

- Preserve the supplied Suno lyric text exactly apart from trimming surrounding whitespace and removing standalone section headings from lyric rows.
- Align canonical lyric lines to OCR anchors in order. Do not replace lyrics with OCR guesses.
- Interpolate a line only when OCR misses it; add `lyrics-line-interpolated-from-video`.
- Add `low-video-lyrics-similarity` when the displayed video text does not sufficiently confirm the copied lyric.
- Calibrate each video boundary within a bounded window using vocal onsets and energy.
- Keep integer milliseconds, half-open intervals `[start_ms, end_ms)`, and strictly increasing starts.
- Preserve warnings for silence, large shifts, overlays, repeats, harmony, and separation artifacts.

Read [references/timeline-schema.md](references/timeline-schema.md) before consuming JSON.

## Validate before delivery

Run:

```bash
python3 scripts/validate_package.py --package-dir /absolute/path/song-package
```

Confirm the lyrics, MP4, and vocal all belong to the same song; media durations are aligned; CSV/JSON/LRC/SRT/VTT contain the same lyric count and text; time intervals are legal; and every warning is reported.

If a human-reviewed answer exists, evaluate only after generation:

```bash
python3 scripts/evaluate_timeline.py \
  --generated /path/song-package/timeline.json \
  --gold-csv /path/reviewed.csv
```

Use `--gold-typescript` for a TypeScript reference. Report text match rate, median absolute timing error, 95th percentile error, and low-confidence ratio. Never claim accuracy improvement without these metrics.

## Developer-only fallback

`scripts/separate_vocals.py` and `requirements-demucs.txt` remain an optional developer fallback for regression work when Suno stems are unavailable. They are not part of the novice workflow and must not be presented as a normal installation requirement.
