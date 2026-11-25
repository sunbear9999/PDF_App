"""Local transcript exports; call from a worker, never a Qt slot."""
from __future__ import annotations

import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid

from core.models.memory_models import ConsumerKind, MemoryConsumer

MUX_MEMORY_BYTES = 128 * 1024 ** 2
PROCESS_POLL_SECONDS = 0.2
ERROR_TAIL_BYTES = 4000


def _check_cancelled(cancel_check):
    if cancel_check():
        raise RuntimeError("Export cancelled.")


def _timestamp(seconds, separator):
    value = float(seconds)
    if not math.isfinite(value) or value < 0:
        raise ValueError("The transcript contains an invalid timestamp.")
    milliseconds = round(value * 1000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{separator}{milliseconds:03d}"


def write_transcript(stream, segments, format_id, cancel_check):
    """Write all stored segments, preserving Unicode and recorded timing."""
    if format_id not in {"txt", "srt", "vtt"}:
        raise ValueError("Unsupported transcript format.")
    if format_id == "vtt":
        stream.write("WEBVTT\n\n")
    count = 0
    for segment in segments:
        _check_cancelled(cancel_check)
        text = str(segment.get("text", "")).strip()
        if not text:
            continue
        count += 1
        if format_id == "txt":
            stream.write(text + "\n")
            continue
        separator = "," if format_id == "srt" else "."
        start = _timestamp(segment["start"], separator)
        end = _timestamp(segment["end"], separator)
        if float(segment["end"]) <= float(segment["start"]):
            raise ValueError("The transcript contains an invalid time interval.")
        # Blank lines separate cues; embedded blank lines must not end a cue.
        text = "\n".join(line for line in text.splitlines() if line.strip())
        stream.write(f"{count}\n{start} --> {end}\n{text}\n\n")
    if not count:
        raise ValueError("No transcript is available to save yet.")


def export_transcript(source_path, segments, destination, format_id, *,
                      cancel_check=lambda: False, memory_manager=None):
    """Atomically publish a transcript or a lossless media/subtitle copy."""
    source, target = Path(source_path).resolve(), Path(destination).resolve()
    if source == target or (target.exists() and source.exists() and os.path.samefile(source, target)):
        raise ValueError("Choose a different filename; the original source cannot be overwritten.")
    if format_id not in {"txt", "srt", "vtt", "media"}:
        raise ValueError("Unsupported export format.")
    _check_cancelled(cancel_check)
    # Staging on the destination filesystem keeps replacement atomic.
    with tempfile.TemporaryDirectory(prefix=".papyrus-export-", dir=target.parent) as folder:
        staging = Path(folder) / "export"
        transcript = Path(folder) / "captions.srt" if format_id == "media" else staging
        with transcript.open("w", encoding="utf-8", newline="\n") as stream:
            write_transcript(stream, segments, "srt" if format_id == "media" else format_id, cancel_check)
        if format_id == "media":
            _export_media(source, transcript, staging, cancel_check, memory_manager)
        _check_cancelled(cancel_check)
        os.replace(staging, target)
    return str(target)


def _export_media(source, transcript, target, cancel_check, memory_manager):
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("Saving media with subtitles requires FFmpeg. Install FFmpeg or choose a transcript-only format.")
    if not source.is_file():
        raise ValueError("The original media file is no longer available.")
    consumer_id = "transcript-export:" + uuid.uuid4().hex
    if memory_manager is not None:
        grant = memory_manager.reserve(MUX_MEMORY_BYTES, "Save media with subtitles")
        if not grant.ok:
            raise RuntimeError("Not enough memory to export media right now.")
        memory_manager.register_consumer(MemoryConsumer(
            id=consumer_id, label="Media subtitle export", kind=ConsumerKind.WORKER.value,
            usage=lambda: MUX_MEMORY_BYTES, release=lambda _level: 0, busy=lambda: True,
        ))
    try:
        command = [executable, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                   "-i", str(source), "-i", str(transcript),
                   "-map", "0:v?", "-map", "0:a?", "-map", "1:0",
                   "-c:v", "copy", "-c:a", "copy", "-c:s", "srt",
                   "-metadata:s:s:0", "title=Transcript", "-disposition:s:0", "default",
                   "-f", "matroska", str(target)]
        _run_mux(command, cancel_check)
    finally:
        if memory_manager is not None:
            memory_manager.unregister_consumer(consumer_id)


def _run_mux(command, cancel_check):
    _check_cancelled(cancel_check)
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=errors)
        try:
            while True:
                _check_cancelled(cancel_check)
                try:
                    code = process.wait(timeout=PROCESS_POLL_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    continue
            if code:
                errors.seek(0, os.SEEK_END)
                errors.seek(max(0, errors.tell() - ERROR_TAIL_BYTES))
                detail = errors.read().decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"Could not save media with subtitles: {detail}")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
