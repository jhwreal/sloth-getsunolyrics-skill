#!/usr/bin/env python3
"""Compare copied Suno lyrics with lyric text observed in the downloaded MP4."""

from __future__ import annotations

import difflib
import re


SCHEMA_VERSION = 1


def normalize_text(text: str) -> str:
    text = text.casefold()
    text = re.sub(r"^[\[（(][^\]）)]+[\]）)]\s*", "", text)
    return re.sub(r"[^0-9a-z\u3400-\u9fff]+", "", text)


def text_similarity(left: str, right: str) -> float:
    a, b = normalize_text(left), normalize_text(right)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def timecode(milliseconds: int | None) -> str | None:
    if milliseconds is None:
        return None
    minutes, remainder = divmod(max(0, int(milliseconds)), 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{millis:03d}"


def _line_alignment(lyrics: list[dict], video_cues: list[dict]) -> list[tuple[str, int | None, int | None]]:
    """Globally align both line sequences while retaining genuinely different video lines."""
    lyric_count = len(lyrics)
    video_count = len(video_cues)
    gap_score = -0.62
    scores = [[0.0] * (video_count + 1) for _ in range(lyric_count + 1)]
    moves = [[""] * (video_count + 1) for _ in range(lyric_count + 1)]
    for row in range(1, lyric_count + 1):
        scores[row][0] = row * gap_score
        moves[row][0] = "copied_only"
    for column in range(1, video_count + 1):
        scores[0][column] = column * gap_score
        moves[0][column] = "video_only"
    for row in range(1, lyric_count + 1):
        for column in range(1, video_count + 1):
            similarity = text_similarity(
                lyrics[row - 1]["text"], video_cues[column - 1]["text"]
            )
            options = {
                "pair": scores[row - 1][column - 1] + (2.0 * similarity - 1.0),
                "copied_only": scores[row - 1][column] + gap_score,
                "video_only": scores[row][column - 1] + gap_score,
            }
            move = max(options, key=options.get)
            scores[row][column] = options[move]
            moves[row][column] = move

    operations: list[tuple[str, int | None, int | None]] = []
    row, column = lyric_count, video_count
    while row or column:
        move = moves[row][column]
        if move == "pair":
            operations.append((move, row - 1, column - 1))
            row -= 1
            column -= 1
        elif move == "copied_only":
            operations.append((move, row - 1, None))
            row -= 1
        else:
            operations.append(("video_only", None, column - 1))
            column -= 1
    operations.reverse()
    return operations


def _evidence(video_cue: dict) -> tuple[float, int]:
    confidence = max(0.0, min(1.0, float(video_cue.get("confidence", 0.0))))
    sample_count = max(1, int(video_cue.get("sample_count", 1)))
    return confidence, sample_count


def _pair_status(copied_text: str, video_cue: dict) -> tuple[str, float]:
    """Return same, candidate, or uncertain without treating ordinary OCR noise as a conflict."""
    copied = normalize_text(copied_text)
    video = normalize_text(video_cue["text"])
    similarity = text_similarity(copied_text, video_cue["text"])
    if not copied or not video:
        return "uncertain", similarity
    if copied == video:
        return "same", similarity
    coverage = min(len(copied), len(video)) / max(len(copied), len(video))
    if min(len(copied), len(video)) >= 4 and coverage >= 0.55 and (
        copied in video or video in copied
    ):
        return "same", similarity
    if similarity >= 0.96:
        return "same", similarity

    confidence, samples = _evidence(video_cue)
    if samples >= 2 and confidence >= 0.82 and similarity < 0.94:
        return "candidate", similarity
    if confidence >= 0.93 and similarity < 0.82:
        return "candidate", similarity
    if confidence >= 0.85 and similarity < 0.68:
        return "candidate", similarity
    return "uncertain", similarity


def _strong_video_evidence(video_cue: dict) -> bool:
    confidence, samples = _evidence(video_cue)
    return bool(normalize_text(video_cue.get("text", ""))) and (
        samples >= 2 and confidence >= 0.82
    )


def _is_local_video_duplicate(video_cues: list[dict], video_index: int) -> bool:
    """Treat a lyric UI briefly returning to a nearby line as scroll/highlight noise."""
    cue = video_cues[video_index]
    cue_time = int(cue["video_start_ms"])
    return any(
        index != video_index
        and abs(int(other["video_start_ms"]) - cue_time) <= 8_000
        and normalize_text(other.get("text", "")) == normalize_text(cue.get("text", ""))
        for index, other in enumerate(video_cues)
    )


def _tokens(text: str) -> tuple[list[str], str]:
    normalized = normalize_text(text)
    if re.search(r"[\u3400-\u9fff]", normalized):
        return list(normalized), ""
    return re.findall(r"[0-9a-z']+", text.casefold()), " "


def text_changes(copied_text: str, video_text: str) -> list[dict]:
    copied_tokens, copied_joiner = _tokens(copied_text)
    video_tokens, video_joiner = _tokens(video_text)
    matcher = difflib.SequenceMatcher(None, copied_tokens, video_tokens)
    changes = []
    for operation, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        if operation == "equal":
            continue
        changes.append(
            {
                "operation": operation,
                "copied": copied_joiner.join(copied_tokens[left_start:left_end]),
                "video": video_joiner.join(video_tokens[right_start:right_end]),
            }
        )
    return changes


def _item(
    kind: str,
    lyrics: list[dict],
    video_cues: list[dict],
    lyric_index: int | None,
    video_index: int | None,
    *,
    severity: str,
    similarity: float | None = None,
) -> dict:
    copied = lyrics[lyric_index] if lyric_index is not None else None
    video = video_cues[video_index] if video_index is not None else None
    confidence, samples = _evidence(video or {})
    copied_text = copied["text"] if copied else None
    video_text = video["text"] if video else None
    return {
        "kind": kind,
        "severity": severity,
        "copied_line": lyric_index + 1 if lyric_index is not None else None,
        "copied_text": copied_text,
        "copied_section": copied.get("section") if copied else None,
        "video_anchor": video_index + 1 if video_index is not None else None,
        "video_time_ms": int(video["video_start_ms"]) if video else None,
        "video_time": timecode(int(video["video_start_ms"])) if video else None,
        "video_text": video_text,
        "similarity": round(
            text_similarity(copied_text, video_text) if similarity is None else similarity, 4
        )
        if copied_text is not None and video_text is not None
        else None,
        "ocr_confidence": round(confidence, 4) if video else None,
        "ocr_sample_count": samples if video else None,
        "changes": text_changes(copied_text, video_text)
        if copied_text is not None and video_text is not None
        else [],
    }


def compare_lyrics_to_video(lyrics: list[dict], video_cues: list[dict]) -> dict:
    """Return high-evidence conflict candidates and lower-confidence OCR uncertainties."""
    operations = _line_alignment(lyrics, video_cues)
    differences: list[dict] = []
    uncertain: list[dict] = []
    matched_count = 0
    paired_operation_indexes = [
        index for index, (kind, _, _) in enumerate(operations) if kind == "pair"
    ]
    first_pair = min(paired_operation_indexes, default=None)
    last_pair = max(paired_operation_indexes, default=None)

    for operation_index, (kind, lyric_index, video_index) in enumerate(operations):
        if kind == "pair":
            status, similarity = _pair_status(
                lyrics[lyric_index]["text"], video_cues[video_index]
            )
            if status == "same":
                matched_count += 1
            elif status == "candidate":
                differences.append(
                    _item(
                        "text_changed",
                        lyrics,
                        video_cues,
                        lyric_index,
                        video_index,
                        severity="decision_required",
                        similarity=similarity,
                    )
                )
            else:
                uncertain.append(
                    _item(
                        "possible_ocr_error",
                        lyrics,
                        video_cues,
                        lyric_index,
                        video_index,
                        severity="ocr_uncertain",
                        similarity=similarity,
                    )
                )
        elif kind == "copied_only":
            uncertain.append(
                _item(
                    "copied_line_not_observed",
                    lyrics,
                    video_cues,
                    lyric_index,
                    None,
                    severity="ocr_uncertain",
                )
            )
        else:
            video = video_cues[video_index]
            between_pairs = bool(
                first_pair is not None
                and last_pair is not None
                and first_pair < operation_index < last_pair
            )
            adjacent_edge_video = any(
                other_kind == "video_only"
                for other_kind, _, _ in operations[
                    max(0, operation_index - 1) : operation_index
                ]
                + operations[operation_index + 1 : operation_index + 2]
            )
            if (
                _strong_video_evidence(video)
                and not _is_local_video_duplicate(video_cues, video_index)
                and (between_pairs or adjacent_edge_video)
            ):
                differences.append(
                    _item(
                        "video_only_line",
                        lyrics,
                        video_cues,
                        None,
                        video_index,
                        severity="decision_required",
                    )
                )
            else:
                uncertain.append(
                    _item(
                        "possible_video_decoration",
                        lyrics,
                        video_cues,
                        None,
                        video_index,
                        severity="ocr_uncertain",
                    )
                )

    for index, item in enumerate(differences, 1):
        item["id"] = f"difference-{index:02d}"
    for index, item in enumerate(uncertain, 1):
        item["id"] = f"uncertain-{index:02d}"
    decision_required = bool(differences)
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "decision_required" if decision_required else "matched",
        "requires_user_decision": decision_required,
        "copied_line_count": len(lyrics),
        "video_anchor_count": len(video_cues),
        "matched_line_count": matched_count,
        "difference_count": len(differences),
        "uncertain_item_count": len(uncertain),
        "differences": differences,
        "uncertain_items": uncertain,
    }


def _escape_markdown(value: object) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict) -> str:
    has_candidates = bool(report.get("requires_user_decision"))
    decision_pending = bool(report.get("decision_pending", has_candidates))
    resolution = report.get("requested_resolution")
    if has_candidates and decision_pending:
        status_zh = "发现候选冲突，需先核对 MP4 画面"
        status_en = "Candidate conflicts found; verify the MP4 frames first"
    elif has_candidates:
        status_zh = f"候选冲突已按 {resolution} 处理"
        status_en = f"Candidate conflicts resolved with {resolution}"
    else:
        status_zh = "未发现高证据歌词冲突"
        status_en = "No high-evidence lyric conflict found"
    lines = [
        "# 歌词一致性报告 / Lyrics consistency report",
        "",
        f"- 状态：{status_zh}",
        f"- Status: {status_en}",
        f"- 复制歌词行 / Copied lines: {report.get('copied_line_count', 0)}",
        f"- MP4 OCR 锚点 / MP4 OCR anchors: {report.get('video_anchor_count', 0)}",
        f"- 候选差异 / Candidate differences: {report.get('difference_count', 0)}",
        f"- 低证据 OCR 项 / Low-evidence OCR items: {report.get('uncertain_item_count', 0)}",
        "",
    ]
    if has_candidates:
        lines.extend(
            [
                "OCR 只是候选证据。Agent 必须打开下表时间点附近的 MP4，确认画面确实显示不同文字；OCR 误识别不能交给用户裁决。",
                "OCR is candidate evidence only. Verify the visible MP4 text at every listed time before asking the user.",
                "",
                "| # | 类型 / Type | MP4 时间 | 复制歌词 / Copied | MP4 OCR | 相似度 | OCR 证据 |",
                "| --- | --- | --- | --- | --- | ---: | --- |",
            ]
        )
        labels = {
            "text_changed": "改词 / changed",
            "video_only_line": "视频新增 / video only",
        }
        for item in report.get("differences", []):
            evidence = (
                f"{item.get('ocr_confidence', 0):.2f} × {item.get('ocr_sample_count', 0)}"
            )
            similarity = item.get("similarity")
            lines.append(
                "| {id} | {kind} | {time} | {copied} | {video} | {similarity} | {evidence} |".format(
                    id=_escape_markdown(item.get("id")),
                    kind=_escape_markdown(labels.get(item.get("kind"), item.get("kind"))),
                    time=_escape_markdown(item.get("video_time")),
                    copied=_escape_markdown(item.get("copied_text")),
                    video=_escape_markdown(item.get("video_text")),
                    similarity=f"{similarity:.2f}" if similarity is not None else "—",
                    evidence=_escape_markdown(evidence),
                )
            )
        lines.append("")
        if decision_pending:
            lines.extend(
                [
                    "确认是真实差异后，停止生成并让用户选择：① 以 MP4 实际歌词为准；② 保留页面复制歌词；③ 提供修订歌词。不得静默选择。",
                    "After visual confirmation, stop and ask the user to choose the MP4 lyrics, keep the copied lyrics, or provide revised lyrics. Never choose silently.",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    f"本次恢复方式 / Resolution: `{resolution}`。候选差异仍保留用于审计。",
                    "Candidate differences remain in this report for audit.",
                    "",
                ]
            )
    else:
        lines.extend(
            [
                "按原有流程继续：保留复制歌词作为正文，用 MP4 定粗时间，再用人声校准。低证据 OCR 项不得自动改写歌词。",
                "Continue the original workflow: preserve copied lyrics, use the MP4 for coarse timing, and use vocals for calibration. Low-evidence OCR must not rewrite lyrics.",
                "",
            ]
        )
    return "\n".join(lines)
