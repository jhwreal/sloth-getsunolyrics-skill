# Suno acquisition with Computer Use

Use this procedure after the user names a song and authorizes creating its lyric timeline.

## 1. Find and identify the song

1. Load the `computer-use:computer-use` skill and initialize its Computer Use runtime.
2. Inspect Chrome's current visible state. If Suno is not open, open `https://suno.com` in Chrome.
3. Use the visible Suno Library/Search controls to search the exact song name.
4. If multiple results share the title, compare visible creator, version, creation date, artwork, and duration. Ask the user only when the interface does not provide enough evidence to choose safely.
5. Open the selected song detail page and record its visible title and `/song/<uuid>` URL.

After every click, menu change, navigation, or download action, inspect fresh application state. Prefer accessible UI elements. Use screenshot coordinates only when the target is visible but has no accessible element.

## 2. Copy the canonical lyrics

1. Locate the song's visible Lyrics panel.
2. Copy the full lyrics exactly as displayed, including repeated lines, case, punctuation, and section headings.
3. Save the untimed text as UTF-8 `lyrics.txt` in the song's working directory.
4. Do not add timestamps and do not use an existing reviewed CSV/TS to fill or reorder the lyrics.
5. Re-open or scroll the panel once to verify that the first line, last line, and repeated sections were not truncated by a collapsed panel. Do not use page-wide Select All, which can mix titles, comments, or recommendations into the lyrics.

## 3. Download the lyric MP4

1. Open the main song action menu associated with the detail header, not a recommendation or player row.
2. Open or hover over `Download` and select `Video`.
3. Record the download start time and wait for Chrome to complete the new `.mp4`. Do not reuse an older same-named file or treat a partial download as complete.
4. Move or copy the MP4 into the song working directory without overwriting unrelated files.

## 4. Create and download the vocal stem

1. From the same song's visible menu, choose `Get Stems` or the equivalent current label.
2. Select the full song. Prefer the normal or auto split that exposes a `Lead Vocal` track; do not choose a remix or cropped region.
3. If Suno displays a credit deduction, price, purchase, or upgrade requirement, pause before the final action, report the exact visible cost, and obtain user confirmation.
4. Start extraction and wait for Suno to report completion. Long processing is expected.
5. Download the isolated `Lead Vocal` stem as WAV. Do not download the instrumental or full mix by mistake.
6. Return to the original song page and confirm the title/UUID did not change during processing.

## 5. Verify the downloads

Use media inspection to confirm:

- the MP4 is decodable and contains video plus audio;
- the vocal file is decodable and predominantly vocal;
- duration difference is no more than `max(500 ms, 0.5%)`;
- both begin at the same song time zero and neither was cropped;
- the filenames and recorded source UUID identify the selected song.

Run the local media preflight before OCR. If it reports missing streams or a duration mismatch, return to the same song UUID and download again; do not hide the mismatch with a manual offset.

## 6. Handle a lyrics conflict after OCR

The local pipeline compares the copied lyrics with MP4 lyric anchors before vocal calibration. When it produces `lyrics-comparison.md` and pauses:

1. Inspect each reported MP4 timestamp and read the visible active/highlighted line. OCR evidence alone is insufficient.
2. If the visible words match the copied lyrics, classify the candidate as OCR error and resume with `--lyrics-conflict-resolution verified-ocr-error`; do not ask the user to resolve a recognition mistake.
3. If the visible MP4 really contains different words, show the user the affected timestamp, copied text, visible video text, and whether the video changed or added a line.
4. Explain that Suno generated a performance whose MP4 lyrics differ from the lyrics panel, then ask whether to use the MP4 version, keep the copied version, or use user-revised lyrics. Do not continue until the user answers.
5. For the MP4 choice, preserve the original copy and create a separate visually corrected lyric file. Do not use raw OCR as final text without checking every changed line.
6. For the copied choice, continue only after explicit confirmation with `--lyrics-conflict-resolution use-copied` and retain all conflict warnings.

If no visually confirmed difference exists, continue the original video-timing and vocal-calibration path unchanged.

If the Suno labels move or change, reason from the visible interface and accessible names instead of guessing old coordinates. Do not use undocumented Suno APIs or derive hidden media URLs.
