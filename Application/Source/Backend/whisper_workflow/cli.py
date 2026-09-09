from __future__ import annotations

import argparse
import sys

from .events import emit
from .pipeline import process_source
from .worker_slots import worker_slot


def parser() -> argparse.ArgumentParser:
    argument_parser = argparse.ArgumentParser(description="Local speaker-labelled transcription")
    argument_parser.add_argument(
        "source",
        nargs="+",
        help="Audio/video files or YouTube/Bilibili video links to transcribe",
    )
    argument_parser.add_argument(
        "--speakers",
        default="auto",
        choices=("auto", "1", "2", "3"),
        help="Automatic speaker count from one to three, or an exact count",
    )
    argument_parser.add_argument(
        "--quality",
        default="recommended",
        choices=("recommended", "fast"),
    )
    argument_parser.add_argument(
        "--language",
        default="auto",
        choices=("auto", "en", "zh"),
        help="Detect the spoken language automatically, or use English or Chinese",
    )
    argument_parser.add_argument("--force", action="store_true")
    argument_parser.add_argument(
        "--submission-date",
        help="Task submission date in YYYYMMDD format (defaults to today)",
    )
    return argument_parser


def main(arguments: list[str] | None = None) -> int:
    options = parser().parse_args(arguments)
    try:
        for source in options.source:
            with worker_slot():
                process_source(
                    source,
                    speaker_setting=options.speakers,
                    quality=options.quality,
                    language=options.language,
                    force=options.force,
                    submission_date=options.submission_date,
                )
    except KeyboardInterrupt:
        emit("cancelled", message="Transcription cancelled")
        return 130
    except Exception as error:
        emit("error", message=str(error))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
