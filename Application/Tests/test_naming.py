from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from whisper_workflow import pipeline


class OutputNamingTests(unittest.TestCase):
    def test_online_collision_uses_video_id_without_overwriting(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            existing = root / "20260909_Same_title"
            existing.mkdir()
            (existing / f"{existing.name}.json").write_text(
                json.dumps({"metadata": {"source_id": "first"}}),
                encoding="utf-8",
            )
            with patch.object(pipeline, "OUTPUT_ROOT", root):
                chosen = pipeline._available_output_directory(
                    "20260909_Same_title",
                    source_id="second/id",
                )
            self.assertEqual(chosen.name, "20260909_Same_title__secondid")

    def test_local_collision_uses_incrementing_number(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "20260909_recording").mkdir()
            with patch.object(pipeline, "OUTPUT_ROOT", root):
                chosen = pipeline._available_output_directory(
                    "20260909_recording",
                    source_path=Path("/tmp/new-recording.wav"),
                )
            self.assertEqual(chosen.name, "20260909_recording__2")


if __name__ == "__main__":
    unittest.main()
