from __future__ import annotations

import unittest

from whisper_workflow.alignment import (
    SpeakerSegment,
    Word,
    assign_speakers,
    friendly_speaker_labels,
    words_to_turns,
)
from whisper_workflow.pipeline import _clean_repetitions, _speech_clips


class AlignmentTests(unittest.TestCase):
    def test_assigns_by_greatest_overlap(self) -> None:
        words = [Word(" hello", 0.8, 1.3), Word(" there", 1.3, 1.8)]
        segments = [
            SpeakerSegment("a", 0.0, 1.0),
            SpeakerSegment("b", 1.0, 3.0),
        ]
        assigned = assign_speakers(words, segments)
        self.assertEqual([word.speaker for word in assigned], ["b", "b"])

    def test_uses_nearest_speaker_for_timestamp_gap(self) -> None:
        words = [Word(" hello", 2.1, 2.2)]
        segments = [SpeakerSegment("a", 0.0, 2.0)]
        self.assertEqual(assign_speakers(words, segments)[0].speaker, "a")

    def test_labels_by_first_appearance_and_merges_turns(self) -> None:
        words = [
            Word(" Hello", 0.0, 0.4, speaker="raw-b"),
            Word(" world.", 0.4, 0.9, speaker="raw-b"),
            Word(" Hi.", 1.0, 1.3, speaker="raw-a"),
        ]
        friendly, mapping = friendly_speaker_labels(words)
        turns = words_to_turns(friendly)
        self.assertEqual(mapping, {"raw-b": "Speaker 1", "raw-a": "Speaker 2"})
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0].text, "Hello world.")
        self.assertEqual(turns[1].speaker, "Speaker 2")

    def test_speech_clips_skip_silence_and_split_at_speech_boundaries(self) -> None:
        segments = [
            SpeakerSegment("a", 58.0, 70.0),
            SpeakerSegment("b", 70.2, 90.0),
            SpeakerSegment("a", 91.0, 124.0),
        ]
        clips = _speech_clips(segments, duration_seconds=130.0)
        pairs = list(zip(clips[::2], clips[1::2]))
        self.assertGreaterEqual(pairs[0][0], 57.0)
        self.assertTrue(all(start < end for start, end in pairs))
        self.assertTrue(all(left[1] <= right[0] for left, right in zip(pairs, pairs[1:])))
        self.assertAlmostEqual(pairs[0][1], 70.1)
        self.assertGreater(pairs[-1][1] - pairs[-1][0], 30.0)

    def test_collapses_impossible_whisper_repetition_loops(self) -> None:
        words = [Word(" perspective", 1.0, 1.0)] * 12
        words.extend((Word(" is", 1.1, 1.2), Word(" useful", 1.2, 1.4)))
        cleaned = _clean_repetitions(words)
        self.assertEqual([word.text for word in cleaned], [" perspective", " is", " useful"])

    def test_removes_long_single_character_noise(self) -> None:
        cleaned = _clean_repetitions([Word(" " + "艶" * 20, 1.0, 1.1)])
        self.assertEqual(cleaned, [])

    def test_collapses_repeated_phrases(self) -> None:
        phrase = [Word(" Is", 1.0, 1.1), Word(" it", 1.1, 1.2), Word(" okay", 1.2, 1.3)]
        cleaned = _clean_repetitions(phrase * 12 + [Word(" now?", 2.0, 2.1)])
        self.assertEqual(
            [word.text for word in cleaned],
            [" Is", " it", " okay", " now?"],
        )

    def test_removes_long_unbroken_gibberish(self) -> None:
        cleaned = _clean_repetitions([Word(" " + "ochigo" * 30, 1.0, 1.1)])
        self.assertEqual(cleaned, [])


if __name__ == "__main__":
    unittest.main()
