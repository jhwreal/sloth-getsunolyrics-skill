#!/usr/bin/env python3
"""Serve a local multi-song lyric timeline review and fine-tuning workbench."""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
from pathlib import Path
import re
import shutil
import subprocess
import sys
from threading import RLock
from typing import Any
from urllib.parse import unquote, urlsplit
import webbrowser

from export_timeline import export_timeline


ROOT = Path(__file__).resolve().parents[1]
STATIC_ROOT = ROOT / "assets" / "review-ui"
VALIDATOR = ROOT / "scripts" / "validate_package.py"
HUMAN_TIMING_SOURCE = "human_reviewed_player_position"
MAX_REQUEST_BYTES = 2 * 1024 * 1024
RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class ReviewError(ValueError):
    """A safe, user-facing review error."""


class RevisionConflict(ReviewError):
    """The on-disk timeline changed after the browser loaded it."""


@dataclass(frozen=True)
class SongPackage:
    song_id: str
    root: Path
    title: str
    timeline_path: Path
    media_path: Path


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def file_revision(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def write_bytes_atomic(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_bytes(content)
    temporary.replace(path)


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    write_bytes_atomic(path, rendered)


def read_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReviewError(f"无法读取 {path.name}: {error}") from error
    if not isinstance(payload, dict):
        raise ReviewError(f"{path.name} 顶层必须是 JSON 对象")
    return payload


def validate_starts(starts: list[int], duration_ms: int) -> None:
    if not starts:
        raise ReviewError("时间轴没有歌词行")
    previous = -1
    for index, start in enumerate(starts, 1):
        if isinstance(start, bool) or not isinstance(start, int):
            raise ReviewError(f"第 {index} 行时间必须是整数毫秒")
        if not 0 <= start < duration_ms:
            raise ReviewError(f"第 {index} 行时间 {start}ms 超出媒体范围")
        if start <= previous:
            raise ReviewError(f"第 {index} 行必须晚于上一行，不能重叠或倒序")
        previous = start


def discover_package_dirs(explicit: list[Path], roots: list[Path]) -> list[Path]:
    candidates = [path for path in explicit]
    for root in roots:
        resolved = root.expanduser().resolve()
        if not resolved.is_dir():
            raise ReviewError(f"歌曲包根目录不存在: {root}")
        candidates.extend(
            path.parent
            for path in resolved.rglob("timeline.json")
            if not {"review", "work"}.intersection(path.relative_to(resolved).parts)
        )
    unique: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.expanduser().resolve()
        if resolved in seen:
            continue
        if not (resolved / "timeline.json").is_file():
            raise ReviewError(f"歌曲包缺少 timeline.json: {resolved}")
        seen.add(resolved)
        unique.append(resolved)
    if not unique:
        raise ReviewError("至少需要一个包含 timeline.json 的歌曲包")
    return sorted(unique, key=lambda path: path.name.casefold())


def build_song(package_dir: Path, position: int) -> SongPackage:
    package_dir = package_dir.expanduser().resolve()
    timeline_path = package_dir / "timeline.json"
    timeline = read_json(timeline_path)
    manifest_path = package_dir / "manifest.json"
    manifest = read_json(manifest_path) if manifest_path.is_file() else {}
    media_name = str(manifest.get("video") or timeline.get("video") or "song.mp4")
    media_path = (package_dir / media_name).resolve()
    if not media_path.is_file() or media_path.parent != package_dir:
        raise ReviewError(f"歌曲包媒体不存在或路径不安全: {package_dir / media_name}")
    title = str(manifest.get("title") or timeline.get("title") or package_dir.name)
    digest = hashlib.sha256(str(package_dir).encode("utf-8")).hexdigest()[:10]
    return SongPackage(
        song_id=f"song-{position:02d}-{digest}",
        root=package_dir,
        title=title,
        timeline_path=timeline_path,
        media_path=media_path,
    )


class TimelineStore:
    def __init__(self, package_dirs: list[Path], *, validate_on_save: bool = True) -> None:
        self._lock = RLock()
        self.validate_on_save = validate_on_save
        songs = [build_song(root, index) for index, root in enumerate(package_dirs, 1)]
        self.songs = {song.song_id: song for song in songs}

    def _song(self, song_id: str) -> SongPackage:
        try:
            return self.songs[song_id]
        except KeyError as error:
            raise ReviewError("歌曲不存在") from error

    @staticmethod
    def _review_state(song: SongPackage) -> dict[str, Any]:
        path = song.root / "review" / "review-state.json"
        if not path.is_file():
            return {}
        try:
            return read_json(path)
        except ReviewError:
            return {}

    def list_songs(self) -> list[dict[str, Any]]:
        result = []
        for song in self.songs.values():
            timeline = read_json(song.timeline_path)
            review = timeline.get("human_review") or self._review_state(song)
            cues = timeline.get("cues") or []
            result.append(
                {
                    "id": song.song_id,
                    "title": song.title,
                    "cue_count": len(cues),
                    "duration_ms": int(timeline.get("media_duration_ms", 0)),
                    "review_status": str(review.get("status") or "unreviewed"),
                    "edited_count": int(review.get("edited_count") or 0),
                }
            )
        return result

    def detail(self, song_id: str) -> dict[str, Any]:
        song = self._song(song_id)
        timeline = read_json(song.timeline_path)
        cues = timeline.get("cues") or []
        starts = [int(cue.get("start_ms", -1)) for cue in cues]
        duration_ms = int(timeline.get("media_duration_ms", 0))
        validate_starts(starts, duration_ms)
        return {
            "id": song.song_id,
            "title": song.title,
            "duration_ms": duration_ms,
            "revision": file_revision(song.timeline_path),
            "review": timeline.get("human_review") or self._review_state(song),
            "media_url": f"/api/songs/{song.song_id}/media",
            "cues": [
                {
                    "index": int(cue.get("index", index)),
                    "text": str(cue.get("text", "")),
                    "section": cue.get("section"),
                    "start_ms": int(cue["start_ms"]),
                    "automatic_start_ms": cue.get("automatic_start_ms"),
                    "timing_source": cue.get("timing_source"),
                    "confidence": cue.get("confidence"),
                    "flags": cue.get("flags") or [],
                }
                for index, cue in enumerate(cues, 1)
            ],
        }

    @staticmethod
    def _preserve_original(song: SongPackage) -> None:
        original = song.root / "review" / "original"
        original.mkdir(parents=True, exist_ok=True)
        for name in ["timeline.json", "timeline.csv", "timeline.lrc", "manifest.json"]:
            source = song.root / name
            destination = original / name
            if source.is_file() and not destination.exists():
                shutil.copy2(source, destination)

    @staticmethod
    def _original_starts(song: SongPackage, fallback: list[int]) -> list[int]:
        path = song.root / "review" / "original" / "timeline.json"
        if not path.is_file():
            return fallback
        payload = read_json(path)
        cues = payload.get("cues") or []
        if len(cues) != len(fallback):
            return fallback
        return [int(cue["start_ms"]) for cue in cues]

    @staticmethod
    def _refresh_alignment_summary(timeline: dict[str, Any]) -> None:
        cues = timeline.get("cues") or []
        summary = dict(timeline.get("alignment_summary") or {})
        sources = [str(cue.get("timing_source") or "") for cue in cues]
        summary.update(
            {
                "dtw_token_start_count": sources.count("whisper_dtw_token_start"),
                "rejected_dtw_backtrack_count": sources.count(
                    "vocal_onset_after_rejected_dtw_backtrack"
                ),
                "leading_weak_dtw_recovery_count": sources.count(
                    "vocal_onset_near_leading_weak_dtw"
                ),
                "duplicate_whisper_start_recovery_count": sources.count(
                    "video_anchor_for_duplicate_whisper_start"
                ),
                "human_reviewed_start_count": sources.count(HUMAN_TIMING_SOURCE),
            }
        )
        timeline["alignment_summary"] = summary

    @staticmethod
    def _snapshot(paths: list[Path]) -> dict[Path, bytes | None]:
        return {path: path.read_bytes() if path.is_file() else None for path in paths}

    @staticmethod
    def _restore(snapshot: dict[Path, bytes | None]) -> None:
        for path, content in snapshot.items():
            if content is None:
                path.unlink(missing_ok=True)
            else:
                write_bytes_atomic(path, content)

    def _run_validation(self, song: SongPackage) -> str:
        manifest_path = song.root / "manifest.json"
        if not self.validate_on_save or not manifest_path.is_file():
            return "not-run"
        manifest = read_json(manifest_path)
        required = [
            song.root / str(manifest.get("video") or ""),
            song.root / str(manifest.get("vocals") or ""),
            song.root / str(manifest.get("lyrics") or ""),
        ]
        if not all(path.is_file() for path in required):
            return "not-run"
        result = subprocess.run(
            [
                sys.executable,
                str(VALIDATOR),
                "--package-dir",
                str(song.root),
                "--output",
                str(song.root / "validation.json"),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "未知校验错误"
            raise ReviewError(f"保存后包校验失败: {detail}")
        return "valid"

    def save(
        self,
        song_id: str,
        *,
        starts: list[int],
        revision: str,
        finalize: bool,
    ) -> dict[str, Any]:
        song = self._song(song_id)
        with self._lock:
            if revision != file_revision(song.timeline_path):
                raise RevisionConflict("时间轴已被其他页面修改，请刷新后再保存")
            timeline = read_json(song.timeline_path)
            cues = timeline.get("cues") or []
            if len(starts) != len(cues):
                raise ReviewError("歌词行数与当前歌曲包不一致")
            duration_ms = int(timeline.get("media_duration_ms", 0))
            validate_starts(starts, duration_ms)
            previous_starts = [int(cue["start_ms"]) for cue in cues]
            self._preserve_original(song)
            original_starts = self._original_starts(song, previous_starts)
            changed_indexes: list[int] = []
            for cue, new_start in zip(cues, starts):
                old_start = int(cue["start_ms"])
                if new_start == old_start:
                    continue
                changed_indexes.append(int(cue.get("index", len(changed_indexes) + 1)))
                if "automatic_start_ms" not in cue:
                    cue["automatic_start_ms"] = old_start
                    cue["automatic_timing_source"] = cue.get("timing_source")
                cue["start_ms"] = new_start
                cue["timing_source"] = HUMAN_TIMING_SOURCE
                flags = list(cue.get("flags") or [])
                if "human-reviewed-start" not in flags:
                    flags.append("human-reviewed-start")
                cue["flags"] = flags

            edited_count = sum(left != right for left, right in zip(starts, original_starts))
            timestamp = now_iso()
            prior_review = dict(timeline.get("human_review") or {})
            human_review = {
                "status": "finalized" if finalize else "in_progress",
                "tool": "sloth-getsunolyrics-review-ui",
                "saved_at": timestamp,
                "finalized_at": timestamp if finalize else prior_review.get("finalized_at"),
                "edited_count": edited_count,
            }
            if not finalize and prior_review.get("status") == "finalized":
                human_review["finalized_at"] = None
            timeline["human_review"] = human_review
            self._refresh_alignment_summary(timeline)

            manifest_path = song.root / "manifest.json"
            manifest = read_json(manifest_path) if manifest_path.is_file() else None
            if manifest is not None:
                manifest["alignment_summary"] = timeline["alignment_summary"]
                manifest["human_review"] = human_review

            mutable_paths = [
                song.timeline_path,
                song.root / "timeline.csv",
                song.root / "timeline.lrc",
                manifest_path,
                song.root / "validation.json",
            ]
            snapshot = self._snapshot(mutable_paths)
            try:
                write_json_atomic(song.timeline_path, timeline)
                export_timeline(timeline, song.root)
                if manifest is not None:
                    write_json_atomic(manifest_path, manifest)
                validation = self._run_validation(song)
            except Exception:
                self._restore(snapshot)
                raise

            state_path = song.root / "review" / "review-state.json"
            state = self._review_state(song)
            history = list(state.get("history") or [])[-49:]
            history.append(
                {
                    "saved_at": timestamp,
                    "status": human_review["status"],
                    "changed_indexes": changed_indexes,
                    "edited_count": edited_count,
                }
            )
            write_json_atomic(state_path, {**human_review, "history": history})
            return {
                "ok": True,
                "revision": file_revision(song.timeline_path),
                "review": human_review,
                "package_validation": validation,
                "changed_indexes": changed_indexes,
            }


class ReviewServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address: tuple[str, int], store: TimelineStore) -> None:
        self.store = store
        super().__init__(address, ReviewHandler)


class ReviewHandler(BaseHTTPRequestHandler):
    server: ReviewServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"[review-ui] {self.address_string()} {format_string % args}", file=sys.stderr)

    def _headers(self, status: int, content_type: str, length: int) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'",
        )

    def _send_bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
        self._headers(status, content_type, len(content))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(content, "application/json; charset=utf-8", status)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json({"ok": False, "error": message}, status)

    def _static(self, relative: str) -> None:
        clean = unquote(relative).lstrip("/") or "index.html"
        candidate = (STATIC_ROOT / clean).resolve()
        if STATIC_ROOT not in candidate.parents and candidate != STATIC_ROOT:
            self._send_error(HTTPStatus.NOT_FOUND, "资源不存在")
            return
        if not candidate.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "资源不存在")
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "image/svg+xml",
        }:
            content_type += "; charset=utf-8"
        self._send_bytes(candidate.read_bytes(), content_type)

    def _media(self, song_id: str) -> None:
        try:
            song = self.server.store._song(song_id)
        except ReviewError as error:
            self._send_error(HTTPStatus.NOT_FOUND, str(error))
            return
        size = song.media_path.stat().st_size
        start, end = 0, size - 1
        status = HTTPStatus.OK
        requested = self.headers.get("Range")
        if requested:
            match = RANGE_RE.fullmatch(requested.strip())
            if not match or "," in requested:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            left, right = match.groups()
            if left:
                start = int(left)
                end = min(int(right), size - 1) if right else size - 1
            elif right:
                length = min(int(right), size)
                start, end = size - length, size - 1
            if start < 0 or start >= size or end < start:
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            status = HTTPStatus.PARTIAL_CONTENT
        length = end - start + 1
        content_type = mimetypes.guess_type(song.media_path.name)[0] or "video/mp4"
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-store")
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD":
            return
        with song.media_path.open("rb") as source:
            source.seek(start)
            remaining = length
            while remaining:
                block = source.read(min(64 * 1024, remaining))
                if not block:
                    break
                try:
                    self.wfile.write(block)
                except (BrokenPipeError, ConnectionResetError):
                    break
                remaining -= len(block)

    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/songs":
            self._send_json({"songs": self.server.store.list_songs()})
            return
        match = re.fullmatch(r"/api/songs/([^/]+)(/media)?", path)
        if match:
            song_id, media_suffix = match.groups()
            if media_suffix:
                self._media(song_id)
                return
            try:
                self._send_json(self.server.store.detail(song_id))
            except ReviewError as error:
                self._send_error(HTTPStatus.NOT_FOUND, str(error))
            return
        if path == "/":
            self._static("index.html")
            return
        if path.startswith("/assets/"):
            self._static(path.removeprefix("/assets/"))
            return
        self._send_error(HTTPStatus.NOT_FOUND, "页面不存在")

    def do_HEAD(self) -> None:  # noqa: N802
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        match = re.fullmatch(r"/api/songs/([^/]+)/timeline", path)
        if not match:
            self._send_error(HTTPStatus.NOT_FOUND, "接口不存在")
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_error(HTTPStatus.BAD_REQUEST, "Content-Length 无效")
            return
        if length <= 0 or length > MAX_REQUEST_BYTES:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "请求体大小无效")
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            starts = payload.get("starts")
            if not isinstance(starts, list):
                raise ReviewError("starts 必须是毫秒整数数组")
            result = self.server.store.save(
                match.group(1),
                starts=starts,
                revision=str(payload.get("revision") or ""),
                finalize=bool(payload.get("finalize", False)),
            )
        except RevisionConflict as error:
            self._send_error(HTTPStatus.CONFLICT, str(error))
            return
        except (json.JSONDecodeError, UnicodeDecodeError, ReviewError) as error:
            self._send_error(HTTPStatus.BAD_REQUEST, str(error))
            return
        except Exception as error:  # pragma: no cover - last-resort HTTP boundary
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, f"保存失败: {error}")
            return
        self._send_json(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package-dir",
        action="append",
        default=[],
        type=Path,
        help="repeat for each completed lyric package",
    )
    parser.add_argument(
        "--packages-root",
        action="append",
        default=[],
        type=Path,
        help="discover completed packages recursively beneath this directory",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--open", action="store_true", help="open the page in the default browser")
    parser.add_argument(
        "--skip-package-validation",
        action="store_true",
        help="skip full media/package validation after saves (development only)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("review UI only supports loopback hosts")
    package_dirs = discover_package_dirs(args.package_dir, args.packages_root)
    store = TimelineStore(package_dirs, validate_on_save=not args.skip_package_validation)
    server = ReviewServer((args.host, args.port), store)
    actual_host, actual_port = server.server_address[:2]
    display_host = "127.0.0.1" if actual_host in {"0.0.0.0", "::"} else actual_host
    url = f"http://{display_host}:{actual_port}/"
    print(f"Timeline review UI: {url}", flush=True)
    print(f"Loaded {len(store.songs)} song package(s). Press Ctrl+C to stop.", flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping timeline review UI.", flush=True)
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
