from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

from .alignment import SpeakerSegment, Word, assign_speakers, friendly_speaker_labels, words_to_turns
from .events import emit, log
from .paths import (
    CACHE_ROOT,
    CONFIG_PATH,
    DIARIZATION_MODELS,
    DIARIZER,
    HF_HOME,
    LOG_ROOT,
    OUTPUT_ROOT,
    prepare_directories,
)
from .renderers import write_outputs
from .youtube import (
    download_online_video,
    inspect_online_video,
    is_online_video_url,
    safe_identifier,
    safe_output_name,
    safe_submission_date,
)

SUPPORTED_EXTENSIONS = {
    ".aac", ".aiff", ".flac", ".m4a", ".m4v", ".mov", ".mp3", ".mp4",
    ".mpeg", ".mpga", ".oga", ".ogg", ".opus", ".wav", ".webm", ".wma",
}


def _settings() -> dict[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _duration(path: Path) -> float:
    command = [
        "/opt/homebrew/bin/ffprobe",
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    return float(result.stdout.strip())


def _normalize(source: Path, target: Path) -> None:
    command = [
        "/opt/homebrew/bin/ffmpeg",
        "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
        str(target),
    ]
    subprocess.run(command, check=True)


def _transcribe(
    normalized: Path,
    model: str,
    language: str | None,
    clip_timestamps: list[float] | None = None,
) -> tuple[list[Word], dict[str, Any]]:
    os.environ["HF_HOME"] = str(HF_HOME)
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    import mlx_whisper

    result = mlx_whisper.transcribe(
        str(normalized),
        path_or_hf_repo=model,
        language=language,
        task="transcribe",
        word_timestamps=True,
        verbose=False,
        condition_on_previous_text=False,
        compression_ratio_threshold=2.0,
        hallucination_silence_threshold=2.0,
        clip_timestamps=clip_timestamps or [0.0],
    )
    words: list[Word] = []
    for segment in result.get("segments", []):
        segment_words = segment.get("words") or []
        if segment_words:
            for word in segment_words:
                text = str(word.get("word", ""))
                if text.strip():
                    words.append(
                        Word(
                            text=text,
                            start=float(word.get("start", segment.get("start", 0.0))),
                            end=float(word.get("end", segment.get("end", 0.0))),
                            probability=(
                                float(word["probability"])
                                if word.get("probability") is not None
                                else None
                            ),
                        )
                    )
        elif str(segment.get("text", "")).strip():
            words.append(
                Word(
                    text=str(segment["text"]),
                    start=float(segment.get("start", 0.0)),
                    end=float(segment.get("end", 0.0)),
                )
            )
    return _clean_repetitions(words), result


def _normalized_token(text: str) -> str:
    return "".join(character for character in text.casefold() if character.isalnum())


def _collapse_internal_repetition(text: str) -> str | None:
    """Collapse exact token loops such as a CJK phrase repeated dozens of times."""
    leading = text[: len(text) - len(text.lstrip())]
    stripped = text.strip()
    # Whisper occasionally emits a long, unbroken mixed-script noise token.
    if len(stripped) > 80 and not any(character.isspace() for character in stripped):
        return None
    if len(stripped) < 4:
        return text
    for unit_length in range(1, min(32, len(stripped) // 4) + 1):
        if len(stripped) % unit_length:
            continue
        unit = stripped[:unit_length]
        repetitions = len(stripped) // unit_length
        if repetitions >= 4 and unit * repetitions == stripped:
            # A single character repeated many times is normally non-speech noise.
            return None if unit_length == 1 and repetitions >= 8 else leading + unit
    return text


def _clean_repetitions(words: list[Word]) -> list[Word]:
    """Defensively collapse impossible word, phrase, and symbol loops."""
    internally_cleaned: list[Word] = []
    for word in words:
        text = _collapse_internal_repetition(word.text)
        if text and text.strip():
            internally_cleaned.append(
                Word(text, word.start, word.end, word.probability, word.speaker)
            )

    # Collapse one-token runs first.
    run_cleaned: list[Word] = []
    index = 0
    while index < len(internally_cleaned):
        token = _normalized_token(internally_cleaned[index].text)
        end_index = index + 1
        while (
            token
            and end_index < len(internally_cleaned)
            and _normalized_token(internally_cleaned[end_index].text) == token
        ):
            end_index += 1
        run = internally_cleaned[index:end_index]
        if len(run) >= 4:
            first = run[0]
            run_cleaned.append(
                Word(
                    first.text,
                    first.start,
                    max(item.end for item in run),
                    first.probability,
                    first.speaker,
                )
            )
        else:
            run_cleaned.extend(run)
        index = end_index

    # Then collapse repeated phrases of two to eight words. Four consecutive
    # repetitions are far beyond normal conversational emphasis.
    cleaned: list[Word] = []
    normalized = [_normalized_token(word.text) for word in run_cleaned]
    index = 0
    while index < len(run_cleaned):
        best_unit = 0
        best_repetitions = 0
        for unit_length in range(2, min(8, (len(run_cleaned) - index) // 4) + 1):
            unit = normalized[index : index + unit_length]
            if not all(unit):
                continue
            repetitions = 1
            while (
                index + (repetitions + 1) * unit_length <= len(run_cleaned)
                and normalized[
                    index + repetitions * unit_length :
                    index + (repetitions + 1) * unit_length
                ] == unit
            ):
                repetitions += 1
            if repetitions >= 4 and unit_length * repetitions > best_unit * best_repetitions:
                best_unit = unit_length
                best_repetitions = repetitions
        if best_repetitions:
            cleaned.extend(run_cleaned[index : index + best_unit])
            index += best_unit * best_repetitions
        else:
            cleaned.append(run_cleaned[index])
            index += 1
    return cleaned


def _diarize(
    normalized: Path,
    output_json: Path,
    speaker_setting: str,
    log_path: Path,
    progress_start: float = 0.08,
    progress_end: float = 0.40,
) -> tuple[list[SpeakerSegment], dict[str, Any]]:
    if not DIARIZER.is_file():
        raise RuntimeError("The speaker-detection component is not installed.")
    command = [
        str(DIARIZER),
        str(normalized),
        str(output_json),
        str(DIARIZATION_MODELS),
        speaker_setting,
    ]
    with log_path.open("a", encoding="utf-8") as error_log:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=error_log,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "progress":
                total = max(1, int(event.get("total", 1)))
                completed = int(event.get("completed", 0))
                interval = max(1, total // 100)
                if completed != total and completed % interval != 0:
                    continue
                emit(
                    "progress",
                    value=progress_start
                    + (progress_end - progress_start) * min(1.0, completed / total),
                    message="Detecting speakers",
                )
            elif event.get("type") == "status":
                emit("status", message=event.get("message", "Detecting speakers"))
        exit_code = process.wait()
    if exit_code != 0 or not output_json.is_file():
        raise RuntimeError(f"Speaker detection failed. Details are in {log_path.name}.")

    payload = json.loads(output_json.read_text(encoding="utf-8"))
    segments = [
        SpeakerSegment(
            speaker=str(segment["speaker"]),
            start=float(segment["start"]),
            end=float(segment["end"]),
            quality=float(segment["quality"]) if segment.get("quality") is not None else None,
        )
        for segment in payload.get("segments", [])
    ]
    return segments, payload


def _speech_clips(
    segments: list[SpeakerSegment],
    duration_seconds: float,
    padding_seconds: float = 0.25,
    merge_gap_seconds: float = 3.0,
    maximum_clip_seconds: float = 30.0,
) -> list[float]:
    """Build clips that end at detected speech boundaries, never arbitrary words."""
    intervals = sorted(
        (
            max(0.0, segment.start),
            min(duration_seconds, segment.end),
        )
        for segment in segments
        if segment.end > segment.start
    )
    groups: list[tuple[float, float]] = []
    for start, end in intervals:
        if not groups:
            groups.append((start, end))
            continue
        current_start, current_end = groups[-1]
        overlaps = start <= current_end
        close_enough = start - current_end <= merge_gap_seconds
        within_limit = max(current_end, end) - current_start <= maximum_clip_seconds
        if overlaps or (close_enough and within_limit):
            groups[-1] = (current_start, max(current_end, end))
        else:
            groups.append((start, end))

    padded = [
        [max(0.0, start - padding_seconds), min(duration_seconds, end + padding_seconds)]
        for start, end in groups
    ]
    # Share very short gaps instead of allowing padded clips to overlap.
    for index in range(1, len(padded)):
        if padded[index][0] < padded[index - 1][1]:
            boundary = (groups[index - 1][1] + groups[index][0]) / 2
            padded[index - 1][1] = boundary
            padded[index][0] = boundary

    return [value for pair in padded if pair[1] - pair[0] >= 0.1 for value in pair]


def _is_current(source: Path, transcript_json: Path) -> bool:
    if not transcript_json.is_file():
        return False
    try:
        metadata = json.loads(transcript_json.read_text(encoding="utf-8"))["metadata"]
        stat = source.stat()
        return (
            metadata.get("source_size") == stat.st_size
            and metadata.get("source_mtime_ns") == stat.st_mtime_ns
        )
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _matches_options(
    transcript_json: Path,
    *,
    speaker_setting: str,
    quality: str,
    language: str,
    model: str,
    youtube_id: str | None = None,
) -> bool:
    if not transcript_json.is_file():
        return False
    try:
        metadata = json.loads(transcript_json.read_text(encoding="utf-8"))["metadata"]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
    expected = {
        "speaker_setting": speaker_setting,
        "quality": quality,
        "requested_language": language,
        "whisper_model": model,
    }
    if youtube_id is not None:
        expected["youtube_id"] = youtube_id
    return all(metadata.get(key) == value for key, value in expected.items())


def _model_for_quality(settings: dict[str, Any], quality: str) -> str:
    return settings["fast_whisper_model"] if quality == "fast" else settings["whisper_model"]


def _output_file(output_directory: Path, extension: str) -> Path:
    preferred = output_directory / f"{output_directory.name}.{extension}"
    legacy = output_directory / f"transcript.{extension}"
    return preferred if preferred.is_file() or not legacy.is_file() else legacy


def _stored_metadata(output_directory: Path) -> dict[str, Any] | None:
    path = _output_file(output_directory, "json")
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))["metadata"]
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
        return None


def _available_output_directory(
    base_name: str,
    *,
    source_id: str | None = None,
    source_path: Path | None = None,
) -> Path:
    """Reuse the same source; otherwise add a stable, non-overwriting suffix."""
    candidate = OUTPUT_ROOT / base_name
    expected_path = str(source_path) if source_path is not None else None

    def is_same(directory: Path) -> bool:
        metadata = _stored_metadata(directory)
        if not metadata:
            return False
        if source_id is not None:
            return metadata.get("source_id") == source_id or metadata.get("youtube_id") == source_id
        return expected_path is not None and metadata.get("source_path") == expected_path

    if not candidate.exists() or is_same(candidate):
        return candidate

    if source_id is not None:
        candidate = OUTPUT_ROOT / f"{base_name}__{safe_identifier(source_id)}"
        if not candidate.exists() or is_same(candidate):
            return candidate

    number = 2
    while True:
        numbered = OUTPUT_ROOT / f"{base_name}__{number}"
        if not numbered.exists() or is_same(numbered):
            return numbered
        number += 1


def _process_media(
    source: Path,
    output_directory: Path,
    *,
    speaker_setting: str,
    quality: str,
    language: str,
    model: str,
    source_metadata: dict[str, Any] | None = None,
) -> Path:
    source_metadata = source_metadata or {}
    job_directory = CACHE_ROOT / f"job-{uuid.uuid4().hex}"
    job_directory.mkdir(parents=True)
    normalized = job_directory / "normalized.wav"
    diarization_json = job_directory / "diarization.json"
    log_stem = str(source_metadata.get("source_id") or source_metadata.get("youtube_id") or source.stem)
    log_path = LOG_ROOT / f"{log_stem}.log"
    started_at = time.monotonic()

    try:
        log_path.write_text(f"Processing {source}\n", encoding="utf-8")
        emit("file", name=str(source_metadata.get("source_display_name") or source.name))
        emit("progress", value=0.02, message="Preparing audio")
        duration_seconds = _duration(source)
        emit(
            "media",
            duration=duration_seconds,
            estimated_remaining=max(15.0, duration_seconds * 0.105 + 12.0),
        )
        _normalize(source, normalized)

        emit("progress", value=0.08, message="Finding speech and speakers")
        diarization_started = time.monotonic()
        speaker_segments, diarization_payload = _diarize(
            normalized,
            diarization_json,
            speaker_setting,
            log_path,
            progress_start=0.08,
            progress_end=0.40,
        )
        diarization_seconds = time.monotonic() - diarization_started
        if not speaker_segments:
            raise RuntimeError("Speaker detection did not find any speech segments.")

        clips = _speech_clips(speaker_segments, duration_seconds)
        if not clips:
            raise RuntimeError("Speaker detection did not find any usable speech regions.")
        emit("progress", value=0.42, message="Loading Whisper")
        transcription_started = time.monotonic()
        words, whisper_result = _transcribe(
            normalized,
            model,
            None if language == "auto" else language,
            clip_timestamps=clips,
        )
        transcription_seconds = time.monotonic() - transcription_started
        if not words:
            raise RuntimeError("Whisper did not detect any spoken words in this recording.")

        emit("progress", value=0.93, message="Whisper transcription complete")
        emit("progress", value=0.94, message="Building transcript")
        assigned = assign_speakers(words, speaker_segments)
        friendly_words, speaker_mapping = friendly_speaker_labels(assigned)
        settings = _settings()
        turns = words_to_turns(
            friendly_words,
            merge_gap_seconds=float(settings["merge_gap_seconds"]),
            maximum_turn_seconds=float(settings["maximum_turn_seconds"]),
        )
        stat = source.stat()
        metadata: dict[str, Any] = {
            "source_file": str(source_metadata.get("source_display_name") or source.name),
            "source_path": str(source),
            "source_size": stat.st_size,
            "source_mtime_ns": stat.st_mtime_ns,
            "duration_seconds": duration_seconds,
            "speaker_count": len(speaker_mapping),
            "speaker_mapping": speaker_mapping,
            "speaker_setting": speaker_setting,
            "quality": quality,
            "requested_language": language,
            "detected_language": whisper_result.get("language"),
            "whisper_model": model,
            "transcription_seconds": transcription_seconds,
            "diarization_seconds": diarization_seconds,
            "diarizer_reported_speakers": diarization_payload.get("speakerCount"),
            "speech_clip_count": len(clips) // 2,
            "total_processing_seconds": time.monotonic() - started_at,
            **source_metadata,
        }
        paths = write_outputs(output_directory, source, turns, friendly_words, metadata)
        emit("progress", value=1.0, message="Complete")
        emit(
            "complete",
            output=str(paths["markdown"]),
            directory=str(output_directory),
            elapsed=metadata["total_processing_seconds"],
            speakers=metadata["speaker_count"],
        )
        return paths["markdown"]
    except Exception:
        with log_path.open("a", encoding="utf-8") as error_log:
            import traceback

            traceback.print_exc(file=error_log)
        raise
    finally:
        shutil.rmtree(job_directory, ignore_errors=True)


def process_audio(
    source_path: str,
    speaker_setting: str = "auto",
    quality: str = "recommended",
    language: str = "auto",
    force: bool = False,
    submission_date: str | None = None,
) -> Path:
    prepare_directories()
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"Audio file not found: {source}")
    if source.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported audio format: {source.suffix or 'unknown'}")

    settings = _settings()
    model = _model_for_quality(settings, quality)
    task_date = safe_submission_date(submission_date)
    base_name = safe_output_name(source.stem, submission_date=task_date)
    output_directory = _available_output_directory(base_name, source_path=source)
    transcript_json = _output_file(output_directory, "json")
    if (
        not force
        and _is_current(source, transcript_json)
        and _matches_options(
            transcript_json,
            speaker_setting=speaker_setting,
            quality=quality,
            language=language,
            model=model,
        )
    ):
        emit("status", message=f"{source.name} is already transcribed")
        markdown = _output_file(output_directory, "md")
        emit("complete", output=str(markdown), skipped=True)
        return markdown

    return _process_media(
        source,
        output_directory,
        speaker_setting=speaker_setting,
        quality=quality,
        language=language,
        model=model,
        source_metadata={"submission_date": task_date},
    )


def process_online_video(
    url: str,
    speaker_setting: str = "auto",
    quality: str = "recommended",
    language: str = "auto",
    force: bool = False,
    submission_date: str | None = None,
) -> Path:
    prepare_directories()
    settings = _settings()
    model = _model_for_quality(settings, quality)
    media = inspect_online_video(url)
    task_date = safe_submission_date(submission_date)
    base_name = safe_output_name(media.title, media.video_id, task_date)
    output_directory = _available_output_directory(base_name, source_id=media.video_id)
    transcript_json = _output_file(output_directory, "json")
    if not force and _matches_options(
        transcript_json,
        speaker_setting=speaker_setting,
        quality=quality,
        language=language,
        model=model,
        youtube_id=media.video_id,
    ):
        markdown = _output_file(output_directory, "md")
        emit("status", message=f"{media.title} is already transcribed")
        emit("complete", output=str(markdown), skipped=True)
        return markdown

    download_directory = CACHE_ROOT / f"online-video-{uuid.uuid4().hex}"
    try:
        downloaded = download_online_video(url, download_directory)
        assert downloaded.path is not None
        return _process_media(
            downloaded.path,
            output_directory,
            speaker_setting=speaker_setting,
            quality=quality,
            language=language,
            model=model,
            source_metadata={
                "title": downloaded.title,
                "source_display_name": downloaded.platform,
                "source_url": downloaded.webpage_url or url.strip(),
                "source_id": downloaded.video_id,
                "platform": downloaded.platform,
                "youtube_id": downloaded.video_id,
                "submission_date": task_date,
            },
        )
    finally:
        shutil.rmtree(download_directory, ignore_errors=True)


def process_youtube(
    url: str,
    speaker_setting: str = "auto",
    quality: str = "recommended",
    language: str = "auto",
    force: bool = False,
    submission_date: str | None = None,
) -> Path:
    """Backward-compatible wrapper for callers that used the original name."""
    return process_online_video(
        url,
        speaker_setting,
        quality,
        language,
        force,
        submission_date,
    )


def process_source(
    value: str,
    speaker_setting: str = "auto",
    quality: str = "recommended",
    language: str = "auto",
    force: bool = False,
    submission_date: str | None = None,
) -> Path:
    if is_online_video_url(value):
        return process_online_video(
            value,
            speaker_setting,
            quality,
            language,
            force,
            submission_date,
        )
    if value.strip().startswith(("http://", "https://")):
        raise ValueError("Only YouTube and Bilibili links are supported. Choose a local audio/video file otherwise.")
    return process_audio(value, speaker_setting, quality, language, force, submission_date)
