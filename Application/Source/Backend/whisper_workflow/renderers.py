from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from .alignment import Turn, Word


def display_time(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def srt_time(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_outputs(
    output_directory: Path,
    source: Path,
    turns: list[Turn],
    words: list[Word],
    metadata: dict[str, Any],
) -> dict[str, Path]:
    output_directory.mkdir(parents=True, exist_ok=True)
    title = str(metadata.get("title") or source.stem.replace("_", " ").strip().title())
    source_url = metadata.get("source_url")
    source_line = (
        f"- Source: [{metadata.get('platform', 'Online video')}]({source_url})"
        if source_url
        else f"- Source: `{metadata.get('source_file', source.name)}`"
    )

    markdown_lines = [
        f"# {title}",
        "",
        source_line,
        f"- Duration: {display_time(float(metadata['duration_seconds']))}",
        f"- Speakers detected: {metadata['speaker_count']}",
        f"- Model: `{metadata['whisper_model']}`",
        f"- Language: {metadata.get('detected_language') or metadata.get('requested_language', 'auto')}",
        f"- Created: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}",
        "",
        "## Transcript",
        "",
    ]
    text_lines: list[str] = []
    for turn in turns:
        line = f"[{display_time(turn.start)}] {turn.speaker}: {turn.text}"
        markdown_lines.extend((f"**[{display_time(turn.start)}] {turn.speaker}:** {turn.text}", ""))
        text_lines.extend((line, ""))

    srt_lines: list[str] = []
    for index, turn in enumerate(turns, start=1):
        srt_lines.extend(
            (
                str(index),
                f"{srt_time(turn.start)} --> {srt_time(turn.end)}",
                f"{turn.speaker}: {turn.text}",
                "",
            )
        )

    json_payload = {
        "metadata": metadata,
        "turns": [asdict(turn) for turn in turns],
        "words": [asdict(word) for word in words],
    }

    output_stem = output_directory.name
    paths = {
        "markdown": output_directory / f"{output_stem}.md",
        "text": output_directory / f"{output_stem}.txt",
        "subtitles": output_directory / f"{output_stem}.srt",
        "json": output_directory / f"{output_stem}.json",
    }
    paths["markdown"].write_text("\n".join(markdown_lines), encoding="utf-8")
    paths["text"].write_text("\n".join(text_lines), encoding="utf-8")
    paths["subtitles"].write_text("\n".join(srt_lines), encoding="utf-8")
    paths["json"].write_text(
        json.dumps(json_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return paths
