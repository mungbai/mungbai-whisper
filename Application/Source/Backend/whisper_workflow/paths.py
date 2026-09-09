from __future__ import annotations

import os
from pathlib import Path


def project_root() -> Path:
    configured = os.environ.get("PROJECT_WHISPER_ROOT")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[4]


ROOT = project_root()
CONFIG_PATH = ROOT / "Application" / "Configuration" / "settings.json"
OUTPUT_ROOT = ROOT / "02 Transcripts"
CACHE_ROOT = ROOT / "Library" / "Cache"
MODEL_ROOT = ROOT / "Library" / "Models"
HF_HOME = MODEL_ROOT / "HuggingFace"
DIARIZATION_MODELS = MODEL_ROOT / "Diarization"
DIARIZER = ROOT / "Library" / "Tools" / "ProjectWhisperDiarizer"
LOG_ROOT = ROOT / "Logs"


def prepare_directories() -> None:
    for directory in (
        OUTPUT_ROOT,
        CACHE_ROOT,
        HF_HOME,
        DIARIZATION_MODELS,
        LOG_ROOT,
    ):
        directory.mkdir(parents=True, exist_ok=True)
