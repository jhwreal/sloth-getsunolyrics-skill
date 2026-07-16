#!/usr/bin/env python3
"""Extract a mix from video and separate an aligned vocal stem with Demucs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile


SKILL_ROOT = Path(__file__).resolve().parents[1]


def require_command(name: str) -> str:
    command = shutil.which(name)
    if not command:
        raise SystemExit(f"missing required command: {name}")
    return command


def run(command: list[str], *, env: dict[str, str] | None = None) -> None:
    printable = " ".join(shlex.quote(part) for part in command)
    print(f"$ {printable}", file=sys.stderr)
    subprocess.run(command, check=True, env=env)


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            require_command("ffprobe"),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=nw=1:nk=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return float(result.stdout.strip())


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def demucs_prefix(explicit: str | None) -> list[str]:
    if explicit:
        return shlex.split(explicit)
    binary = shutil.which("demucs")
    if binary:
        return [binary]
    if importlib.util.find_spec("demucs") is not None:
        return [sys.executable, "-m", "demucs"]
    raise SystemExit(
        "Demucs is not installed. Create a Python virtual environment and run "
        "`python -m pip install demucs`, then rerun with that environment's Python."
    )


def extract_mix(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            require_command("ffmpeg"),
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-vn",
            "-ac",
            "2",
            "-ar",
            "44100",
            "-c:a",
            "pcm_s16le",
            str(destination),
            "-y",
        ]
    )


def separate(
    source: Path,
    output: Path,
    work: Path,
    *,
    model: str,
    device: str,
    demucs_command: str | None,
    model_cache: Path,
) -> dict:
    mix = work / "mix.wav"
    separated = work / "demucs"
    extract_mix(source, mix)
    prefix = demucs_prefix(demucs_command)
    command = prefix + [
        "--two-stems=vocals",
        "--name",
        model,
        "--device",
        device,
        "--out",
        str(separated),
        str(mix),
    ]
    model_cache.mkdir(parents=True, exist_ok=True)
    environment = os.environ.copy()
    environment["TORCH_HOME"] = str(model_cache)
    run(command, env=environment)
    candidates = sorted(separated.rglob("vocals.wav"))
    if len(candidates) != 1:
        raise SystemExit(f"expected one Demucs vocals.wav, found {len(candidates)} under {separated}")

    output.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidates[0], output)
    source_duration = probe_duration(source)
    vocal_duration = probe_duration(output)
    allowed_delta = max(0.5, source_duration * 0.005)
    if abs(source_duration - vocal_duration) > allowed_delta:
        raise SystemExit(
            f"separated vocal duration differs from source: {source_duration:.3f}s vs "
            f"{vocal_duration:.3f}s"
        )
    return {
        "schema_version": 2,
        "backend": "demucs",
        "model": model,
        "device": device,
        "model_cache": model_cache.name,
        "source_filename": source.name,
        "source_sha256": sha256(source),
        "source_duration_ms": round(source_duration * 1000),
        "vocals": output.name,
        "vocals_sha256": sha256(output),
        "vocals_duration_ms": round(vocal_duration * 1000),
        "command": {
            "executable": Path(command[0]).name,
            "two_stems": "vocals",
            "model": model,
            "device": device,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True, help="MP4 or audio mix")
    parser.add_argument("--output", type=Path, required=True, help="destination vocals.wav")
    parser.add_argument("--model", default="htdemucs", help="Demucs model name")
    parser.add_argument("--device", default="cpu", help="Demucs device, normally cpu")
    parser.add_argument(
        "--demucs-command",
        help="explicit Demucs command, for example /path/to/venv/bin/demucs",
    )
    parser.add_argument(
        "--model-cache",
        type=Path,
        default=SKILL_ROOT / ".cache" / "torch",
        help="persistent Torch model cache (default: Skill .cache/torch)",
    )
    parser.add_argument("--work-dir", type=Path, help="persistent intermediate directory")
    parser.add_argument("--keep-work", action="store_true")
    args = parser.parse_args()
    if not args.input.is_file():
        raise SystemExit(f"input does not exist: {args.input}")

    temporary = None
    if args.work_dir:
        work = args.work_dir.resolve()
        work.mkdir(parents=True, exist_ok=True)
    else:
        temporary = tempfile.TemporaryDirectory(prefix="sloth-vocal-separation-")
        work = Path(temporary.name)
    metadata = separate(
        args.input.resolve(),
        args.output.resolve(),
        work,
        model=args.model,
        device=args.device,
        demucs_command=args.demucs_command,
        model_cache=args.model_cache.resolve(),
    )
    metadata_path = args.output.with_suffix(".separation.json")
    temporary_metadata = metadata_path.with_name(f".{metadata_path.name}.tmp")
    temporary_metadata.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary_metadata.replace(metadata_path)
    print(f"Wrote {args.output} and {metadata_path}", file=sys.stderr)
    if args.keep_work and temporary:
        retained = args.output.with_suffix(".separation-work")
        shutil.copytree(work, retained, dirs_exist_ok=True)
        print(f"Retained separation work at {retained}", file=sys.stderr)


if __name__ == "__main__":
    main()
