from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from mcp.server.mcpserver import Context, MCPServer

from .paths import OUTPUT_ROOT, ROOT, prepare_directories
from .youtube import is_online_video_url, safe_submission_date


MAXIMUM_CONCURRENT_JOBS = 4
DEFAULT_TRANSCRIPT_CHARACTERS = 40_000

mcp = MCPServer(
    name="mungbai-whisper",
    title="mungbai-whisper",
    version="0.1.0",
    description="Private local transcription for audio files, YouTube, and Bilibili.",
    instructions=(
        "Use transcribe whenever the user provides local audio/video paths, YouTube links, or "
        "Bilibili links and asks for a transcript. Pass every source in one call. The server "
        "runs at most four jobs concurrently and queues the remainder. Return the transcript "
        "text to the user, not merely a download confirmation."
    ),
)


def _display_name(source: str) -> str:
    if is_online_video_url(source):
        return source
    return Path(source).expanduser().name


def _validated_sources(sources: list[str]) -> list[str]:
    clean: list[str] = []
    seen: set[str] = set()
    for raw in sources:
        value = str(raw).strip()
        if not value or value in seen:
            continue
        if value.startswith(("http://", "https://")):
            if not is_online_video_url(value):
                raise ValueError(f"Unsupported link: {value}. Use YouTube or Bilibili links.")
        else:
            path = Path(value).expanduser().resolve()
            if not path.is_file():
                raise FileNotFoundError(f"Local media file not found: {path}")
            value = str(path)
        seen.add(value)
        clean.append(value)
    if not clean:
        raise ValueError("Provide at least one YouTube link, Bilibili link, or local media path.")
    return clean


def _duration_text(seconds: float) -> str:
    total = max(0, round(seconds))
    minutes, secs = divmod(total, 60)
    if minutes >= 60:
        hours, minutes = divmod(minutes, 60)
        return f"{hours}h {minutes}m"
    return f"{minutes}m {secs}s" if minutes else f"{secs}s"


async def _read_stream(stream: asyncio.StreamReader) -> str:
    chunks: list[bytes] = []
    while True:
        chunk = await stream.read(8192)
        if not chunk:
            return b"".join(chunks).decode("utf-8", errors="replace")
        chunks.append(chunk)


@mcp.tool(
    name="transcribe",
    title="Transcribe audio or video links",
    description=(
        "Transcribe one or many local media paths, YouTube links, or Bilibili links on this Mac. "
        "Use one call for all sources; only four run at once and extra sources wait automatically. "
        "Returns transcript text by default plus saved file paths."
    ),
    structured_output=True,
)
async def transcribe(
    sources: list[str],
    ctx: Context,
    language: Literal["auto", "en", "zh"] = "auto",
    quality: Literal["recommended", "fast"] = "recommended",
    speakers: Literal["auto", "1", "2", "3"] = "auto",
    include_transcript: bool = True,
) -> dict[str, Any]:
    """Create local transcripts and return their text and paths."""
    values = _validated_sources(sources)
    submission_date = safe_submission_date()
    count = len(values)
    semaphore = asyncio.Semaphore(MAXIMUM_CONCURRENT_JOBS)
    progress_values = [0.0] * count
    stages = ["Queued"] * count
    estimated_finish: list[float | None] = [None] * count
    finished = 0
    last_progress = -1.0
    progress_lock = asyncio.Lock()
    loop = asyncio.get_running_loop()

    async def report(message: str | None = None) -> None:
        nonlocal last_progress
        async with progress_lock:
            overall = sum(progress_values) / count
            if overall <= last_progress and message is None:
                return
            overall = max(overall, last_progress)
            last_progress = overall
            running = sum(stage not in {"Queued", "Complete", "Failed"} for stage in stages)
            queued = stages.count("Queued")
            eta_values = [value for value in estimated_finish if value is not None]
            eta = max(eta_values) - loop.time() if eta_values else None
            status = message or f"{finished}/{count} complete • {running} running • {queued} queued"
            if eta is not None and eta > 0:
                status += f" • estimated {_duration_text(eta)} remaining"
            await ctx.report_progress(overall * 100.0, 100.0, status)

    await report(f"0/{count} complete • up to four run at once • {max(0, count - 4)} queued")

    async def run_one(index: int, source: str) -> dict[str, Any]:
        nonlocal finished
        async with semaphore:
            stages[index] = "Starting"
            await report()
            command = [
                sys.executable,
                "-m", "whisper_workflow",
                "--speakers", speakers,
                "--quality", quality,
                "--language", language,
                "--submission-date", submission_date,
                source,
            ]
            environment = dict(os.environ)
            environment["PROJECT_WHISPER_ROOT"] = str(ROOT)
            environment["PYTHONPATH"] = str(ROOT / "Application" / "Source" / "Backend")
            environment["PATH"] = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(ROOT),
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert process.stdout is not None
            assert process.stderr is not None
            stderr_task = asyncio.create_task(_read_stream(process.stderr))
            output_path: str | None = None
            error_message: str | None = None
            while True:
                line = await process.stdout.readline()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                event_type = event.get("type")
                if event_type in {"status", "file"}:
                    stages[index] = str(event.get("message") or event.get("name") or "Working")
                elif event_type == "download_progress":
                    progress_values[index] = max(
                        progress_values[index], float(event.get("value", 0.0)) * 0.02
                    )
                    platform = event.get("platform", "video")
                    stages[index] = f"Downloading {platform} audio"
                    if event.get("eta") is not None:
                        estimated_finish[index] = loop.time() + float(event["eta"])
                elif event_type == "media":
                    remaining = float(event.get("estimated_remaining", 0.0))
                    if remaining > 0:
                        estimated_finish[index] = loop.time() + remaining
                elif event_type == "progress":
                    progress_values[index] = max(
                        progress_values[index], float(event.get("value", 0.0))
                    )
                    stages[index] = str(event.get("message") or "Transcribing")
                elif event_type == "complete":
                    output_path = str(event.get("output") or "") or None
                elif event_type == "error":
                    error_message = str(event.get("message") or "Transcription failed")
                await report()

            exit_code = await process.wait()
            stderr = await stderr_task
            if exit_code != 0 or output_path is None:
                stages[index] = "Failed"
                progress_values[index] = 1.0
                finished += 1
                await report(f"{finished}/{count} finished • {_display_name(source)} failed")
                return {
                    "source": source,
                    "status": "failed",
                    "error": error_message or stderr.strip() or f"Worker exited with code {exit_code}",
                }

            path = Path(output_path).resolve()
            transcript = path.read_text(encoding="utf-8") if include_transcript else ""
            truncated = len(transcript) > DEFAULT_TRANSCRIPT_CHARACTERS
            if truncated:
                transcript = transcript[:DEFAULT_TRANSCRIPT_CHARACTERS]
            stages[index] = "Complete"
            progress_values[index] = 1.0
            estimated_finish[index] = None
            finished += 1
            await report(f"{finished}/{count} complete • {_display_name(source)} saved")
            result: dict[str, Any] = {
                "source": source,
                "status": "completed",
                "transcript_path": str(path),
                "output_directory": str(path.parent),
            }
            if include_transcript:
                result["transcript"] = transcript
                result["transcript_truncated"] = truncated
            return result

    results = await asyncio.gather(*(run_one(index, source) for index, source in enumerate(values)))
    succeeded = sum(result["status"] == "completed" for result in results)
    return {
        "summary": f"{succeeded}/{count} transcripts completed locally",
        "submission_date": submission_date,
        "maximum_concurrent_jobs": MAXIMUM_CONCURRENT_JOBS,
        "results": results,
    }


def _approved_transcript(path: str) -> Path:
    candidate = Path(path).expanduser().resolve()
    try:
        candidate.relative_to(OUTPUT_ROOT.resolve())
    except ValueError as error:
        raise ValueError("Only transcripts inside the Project Whisper results folder can be read.") from error
    if not candidate.is_file() or candidate.suffix.lower() not in {".md", ".txt", ".srt", ".json"}:
        raise FileNotFoundError(f"Transcript file not found: {candidate}")
    return candidate


@mcp.tool(
    name="read_transcript",
    title="Read a saved transcript",
    description="Read all or the next section of a transcript created by mungbai-whisper.",
    structured_output=True,
)
async def read_transcript(
    path: str,
    offset: int = 0,
    maximum_characters: int = DEFAULT_TRANSCRIPT_CHARACTERS,
) -> dict[str, Any]:
    candidate = _approved_transcript(path)
    text = candidate.read_text(encoding="utf-8")
    safe_offset = max(0, offset)
    safe_limit = max(1_000, min(maximum_characters, 80_000))
    end = min(len(text), safe_offset + safe_limit)
    return {
        "path": str(candidate),
        "text": text[safe_offset:end],
        "offset": safe_offset,
        "next_offset": end if end < len(text) else None,
        "complete": end >= len(text),
    }


@mcp.tool(
    name="list_recent_transcripts",
    title="List recent transcripts",
    description="List recently saved Project Whisper transcript files and modification times.",
    structured_output=True,
)
async def list_recent_transcripts(limit: int = 10) -> dict[str, Any]:
    prepare_directories()
    files = sorted(
        OUTPUT_ROOT.glob("*/*.md"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )[: max(1, min(limit, 100))]
    return {
        "transcripts": [
            {
                "path": str(path),
                "modified": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(),
            }
            for path in files
        ]
    }


def main() -> None:
    mcp.run("stdio")


if __name__ == "__main__":
    main()
