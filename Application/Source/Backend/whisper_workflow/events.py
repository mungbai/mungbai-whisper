from __future__ import annotations

import json
import sys
from typing import Any


def emit(event_type: str, **values: Any) -> None:
    """Write one machine-readable event for the macOS application."""
    payload = {"type": event_type, **values}
    print(json.dumps(payload, ensure_ascii=False), flush=True)


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)
