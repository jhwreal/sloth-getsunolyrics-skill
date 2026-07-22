---
name: sloth-getsunolyrics-skill
description: Use Computer Use to find named songs in the user's logged-in Suno account, copy lyrics, download lyric videos and Suno-separated lead vocals, resolve visible lyric conflicts, generate validated millisecond start-time CSV/JSON/LRC packages, and launch a local multi-song browser workbench for human playback review, per-line timestamp capture, global shifting, saving, and finalization. Use when a user wants reusable lyric starts for games, karaoke, editing, visualization, or a Netease-style timeline confirmation and fine-tuning UI, or when evaluating generated timing against a separate human-reviewed gold timeline.
---

# Sloth Get Suno Lyrics

Turn a Suno song name into a timestamped lyric package. The normal user supplies only the song name and has Suno open and logged in. Do not ask the user to install Demucs or manually prepare media.

Keep these evidence roles separate:

1. Lyrics copied from the visible Suno song page are the provisional text, line order, case, punctuation, and repetitions.
2. The lyric MP4 supplies visible highlight/scroll timing and confirms whether Suno actually used that text. Suno can generate different words and show those changed words in the MP4.
3. Lyric-prompted, offline whisper.cpp DTW supplies content-aware token starts. The supplied lyric remains canonical; recognized text is timing evidence only.
4. The Suno lead-vocal stem supplies vocal activity used to reject impossible DTW backtracks and refine the containing Whisper segment boundary.

When the copied lyrics and visually confirmed MP4 lyrics differ, neither version becomes canonical automatically. Explain the situation and exact differences, then wait for the user's decision.

Never accept a reviewed timestamp CSV or TypeScript timeline as a production input. Gold timelines exist only for evaluation after generation.

## Prerequisites

Require only:

- Codex Desktop with Computer Use available.
- Chrome open with the user's Suno account already logged in.
- The named song visible to that account, with lyrics, Video download, and stem extraction access.
- Enough Suno credits or plan entitlement if Suno charges for stem extraction.
- A Codex runtime with `whisper-cli` and a local whisper.cpp model available; large-v3 is the recommended precision backend. The script auto-discovers the configured executable and model.

Do not request the user's password, cookie, or token. Do not ask a normal user to install a browser extension, Demucs, Python packages, or audio models. If the Codex runtime lacks the local precision backend, report the environment prerequisite instead of silently returning a low-precision timeline. If Suno shows a credit charge, purchase, or upgrade before the final extraction action, show the exact cost and ask for confirmation at that point.

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

## Resolve copied-lyrics versus MP4 conflicts

The extractor compares the copied lines with the unfiltered MP4 OCR anchors before vocal calibration. It always writes `lyrics-comparison.json` and `lyrics-comparison.md`.

If the report says `matched`, continue the existing workflow without asking the user. Missing or weak OCR evidence alone is not proof that Suno changed a lyric; keep the copied text and retain normal low-confidence warnings.

If the command exits with status `3` and `decision_pending: true`:

1. Read `lyrics-comparison.md` and inspect the actual MP4 at every listed timestamp. Check the visible active/highlighted lyric, not OCR text alone.
2. If every candidate is an OCR mistake, rerun with `--resume --lyrics-conflict-resolution verified-ocr-error`. Do not bother the user with OCR noise.
3. If at least one difference is visibly real, give the user a concise table containing the MP4 time, copied lyric, visible MP4 lyric, and difference type. Explain that Suno generated lyrics different from the song-page text. Stop and ask the user to choose; do not start final export in the same turn.
4. Offer these choices:
   - Use the MP4 words actually performed. This is usually best for a timeline used with the generated song. Preserve the original copy as `lyrics.suno.txt`, create a visually corrected `lyrics.resolved.txt`, and rerun with the resolved file as `--lyrics`. Never promote raw OCR without checking the video.
   - Keep the copied Suno lyrics. After explicit confirmation, rerun with `--resume --lyrics-conflict-resolution use-copied`; explain that some exported lines may not be sung and preserve the conflict warnings.
   - Use lyrics revised by the user. Save them separately and rerun with that file as `--lyrics`.

Never infer a choice from the requested output format. A real lyrics conflict is a content decision, not a timing error and not something a fixed offset or vocal alignment can repair.

## Generate the timeline

Resolve the absolute directory containing this `SKILL.md`; never assume the task's current directory is the Skill directory. Keep all song media and packages in the user's working directory. After acquisition, run:

```bash
python3 /absolute/path/to/sloth-getsunolyrics-skill/scripts/process_song.py \
  --video /absolute/path/song.mp4 \
  --vocals /absolute/path/lead-vocal.wav \
  --lyrics /absolute/path/lyrics.txt \
  --output-dir /absolute/path/song-package \
  --language auto \
  --source-url 'https://suno.com/song/SONG_UUID' \
  --title 'Song title'
```

Use `--language zh` or `--language en` when known. Use `--interval 0.25` for rapidly changing lyrics; the default is `0.5` seconds. The command rejects missing streams, mismatched media durations, empty vocals, and invalid lyric files before expensive OCR work.

Use `--resume` after interruption. Timeline reuse requires matching media and lyric hashes, OCR language, sampling interval, pipeline fingerprint, Whisper executable hash, and model hash. OCR and DTW transcripts are cached separately by content and parameters, so an algorithm update can reuse observations while still rebuilding the timeline. Never manually copy an old `timeline.json` into a new song package.

Keep the default `--lyrics-conflict-resolution ask`. Only use an override after the visual checks and decision rules above. A conflict pause is an expected control-flow result, not a failed song extraction.

The primary deliverable is `timeline.csv`:

```csv
id,section,start_time,lyric
lyric-01,Verse 1,00:12.340,歌词内容
```

The package also contains:

```text
song-package/
├── song.mp4
├── vocals.wav
├── lyrics.txt
├── lyrics-comparison.json
├── lyrics-comparison.md
├── timeline.csv
├── timeline.json
├── timeline.lrc
├── vocals.separation.json
├── manifest.json
├── validation.json
├── review/            # created only after the first manual save
└── work/
```

Preserve `work/` until review finishes. It contains OCR frames and observations needed to diagnose alignment.

## Alignment rules

- Preserve the supplied Suno lyric text exactly apart from trimming surrounding whitespace and removing standalone section headings from lyric rows when the comparison matches or the user explicitly keeps that version.
- Compare against unfiltered video anchors before discarding titles or OCR noise so genuinely changed lines remain visible as conflict candidates.
- Never treat OCR as proof by itself. Visually verify candidate frames before reporting a real conflict to the user.
- Align canonical lyric lines to OCR anchors in order. Do not replace lyrics with OCR guesses.
- Interpolate a line only when OCR misses it; add `lyrics-line-interpolated-from-video`.
- Add `low-video-lyrics-similarity` when the displayed video text does not sufficiently confirm the copied lyric.
- Run whisper.cpp offline with the canonical lyric sequence as a prompt, `-mc 0`, DTW enabled, and Flash Attention disabled (`-nfa`). Flash Attention otherwise disables DTW and leaves `t_dtw == -1`.
- Order-align canonical lyric groups with recognized segments. When one segment contains multiple lyric lines, map later line boundaries to the corresponding token inside that segment instead of reusing its first timestamp.
- Treat recognized words only as timing evidence. Never replace canonical lyrics with Whisper output.
- Reject a DTW token start that is more than 500 ms before its containing Whisper segment start; that is a detectable alignment backtrack. Refine the raw segment boundary with the nearest forward vocal onset, then preserve the rejection flag.
- When a weak first-line match skips earlier Whisper segments, retain their earliest DTW evidence and accept it only when an isolated-vocal onset confirms the boundary. This recovers sung intros hidden by transcription hallucinations without trusting the hallucinated words.
- When consecutive lyric lines receive the same DTW start, treat the earlier line as omitted by Whisper and recover its ordered MP4 anchor instead of collapsing both rows to adjacent milliseconds.
- Use MP4 timing as a sequence/window prior and audit signal, not as an unconditional fixed offset. Lyrics can be pre-displayed and video transitions can lag the voice.
- Keep integer milliseconds and strictly increasing starts. The output contract is start-only; do not synthesize end times, SRT, or VTT.
- Preserve warnings for silence, large shifts, overlays, repeats, harmony, and separation artifacts.

Read [references/timeline-schema.md](references/timeline-schema.md) before consuming JSON.

## Validate the generated package

Run:

```bash
python3 /absolute/path/to/sloth-getsunolyrics-skill/scripts/validate_package.py \
  --package-dir /absolute/path/song-package
```

Confirm the lyrics, MP4, and vocal all belong to the same song; media durations are aligned; CSV/JSON/LRC contain the same text and starts; no interval export remains; starts are legal and strictly increasing; and every warning is reported. A completed package must have `lyrics_comparison.decision_pending == false`. Inspect `alignment_summary`: interpolated lines, conflict-overridden lines, missing Whisper matches, or a low confirmed ratio require human review even when structural validation passes.

## Launch human review and fine-tuning

After all requested songs finish blind generation and package validation, start the local review workbench. Pass each completed package explicitly:

```bash
python3 /absolute/path/to/sloth-getsunolyrics-skill/scripts/review_timeline.py \
  --package-dir /absolute/path/song-one-package \
  --package-dir /absolute/path/song-two-package
```

For a workspace containing several completed packages, discover them recursively:

```bash
python3 /absolute/path/to/sloth-getsunolyrics-skill/scripts/review_timeline.py \
  --packages-root /absolute/path/completed-song-packages
```

Keep the process running and give the user the printed `http://127.0.0.1:PORT/` address. The page is deliberately loopback-only and has two columns: song switching on the left, and the combined player/lyrics/fine-tuning workbench on the right.

Use the page as follows:

1. Click a lyric to select it, seek the player to its current start, and play.
2. Drag the player timeline to the audible singing onset.
3. Click **添加时间点** to assign the current playback millisecond to the selected lyric, or use the per-line ±0.1/0.5 second controls.
4. Use **时间轴整体调整** only when the whole song has a supported common bias.
5. Save progress or click **确认定版** after listening through the complete song.

Every save rejects negative, out-of-range, duplicate, or non-increasing starts and detects stale browser revisions. The first save preserves the blind automatic outputs in `review/original/`; edited cues retain `automatic_start_ms` and `automatic_timing_source`, use `human_reviewed_player_position` as the active timing source, regenerate CSV/LRC atomically, and run full package validation when all media inputs are present. Never use a reviewed or manually edited package as generation input for another song.

## Evaluate against independent gold data

If a human-reviewed answer exists, evaluate only after generation and before exposing that answer to the review UI:

```bash
python3 /absolute/path/to/sloth-getsunolyrics-skill/scripts/evaluate_timeline.py \
  --generated /path/song-package/timeline.json \
  --gold-csv /path/reviewed.csv \
  --max-start-error-ms 500
```

Use `--gold-typescript` for a TypeScript reference. Report exact text pairing, maximum/median/95th-percentile start error, signed bias, and the fraction within 500 ms. For the maintained precision regression, every paired line—not merely the median or p95—must be within 500 ms. Never claim accuracy improvement without these metrics, and never let the reviewed answer enter the generation cache or prompt.

## Developer-only fallback

`scripts/separate_vocals.py` and `requirements-demucs.txt` remain an optional developer fallback for regression work when Suno stems are unavailable. They are not part of the novice workflow and must not be presented as a normal installation requirement.
