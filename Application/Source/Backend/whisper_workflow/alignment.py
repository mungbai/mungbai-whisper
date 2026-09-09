from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float
    probability: float | None = None
    speaker: str | None = None


@dataclass(frozen=True)
class SpeakerSegment:
    speaker: str
    start: float
    end: float
    quality: float | None = None


@dataclass(frozen=True)
class Turn:
    speaker: str
    start: float
    end: float
    text: str


def _overlap(start: float, end: float, segment: SpeakerSegment) -> float:
    return max(0.0, min(end, segment.end) - max(start, segment.start))


def _speaker_for_word(
    word: Word,
    segments: list[SpeakerSegment],
    previous: str | None,
) -> str:
    overlaps = [(_overlap(word.start, word.end, segment), segment) for segment in segments]
    best_overlap, best_segment = max(overlaps, key=lambda item: item[0], default=(0.0, None))
    if best_segment is not None and best_overlap > 0:
        return best_segment.speaker

    midpoint = (word.start + word.end) / 2
    nearest = min(
        segments,
        key=lambda segment: min(abs(midpoint - segment.start), abs(midpoint - segment.end)),
        default=None,
    )
    if nearest is not None:
        distance = min(abs(midpoint - nearest.start), abs(midpoint - nearest.end))
        if distance <= 1.5:
            return nearest.speaker
    return previous or (nearest.speaker if nearest is not None else "speaker-unknown")


def assign_speakers(
    words: Iterable[Word],
    segments: Iterable[SpeakerSegment],
) -> list[Word]:
    ordered_segments = sorted(segments, key=lambda item: (item.start, item.end))
    assigned: list[Word] = []
    previous: str | None = None
    for word in sorted(words, key=lambda item: (item.start, item.end)):
        speaker = _speaker_for_word(word, ordered_segments, previous)
        assigned_word = Word(
            text=word.text,
            start=word.start,
            end=word.end,
            probability=word.probability,
            speaker=speaker,
        )
        assigned.append(assigned_word)
        previous = speaker
    return assigned


def friendly_speaker_labels(words: Iterable[Word]) -> tuple[list[Word], dict[str, str]]:
    mapping: dict[str, str] = {}
    relabeled: list[Word] = []
    for word in words:
        raw = word.speaker or "speaker-unknown"
        if raw not in mapping:
            mapping[raw] = f"Speaker {len(mapping) + 1}"
        relabeled.append(
            Word(
                text=word.text,
                start=word.start,
                end=word.end,
                probability=word.probability,
                speaker=mapping[raw],
            )
        )
    return relabeled, mapping


def words_to_turns(
    words: Iterable[Word],
    merge_gap_seconds: float = 1.2,
    maximum_turn_seconds: float = 30.0,
) -> list[Turn]:
    turns: list[Turn] = []
    current_words: list[Word] = []

    def finish() -> None:
        if not current_words:
            return
        text = "".join(word.text for word in current_words).strip()
        if text:
            turns.append(
                Turn(
                    speaker=current_words[0].speaker or "Speaker",
                    start=current_words[0].start,
                    end=current_words[-1].end,
                    text=text,
                )
            )
        current_words.clear()

    for word in words:
        if current_words:
            same_speaker = current_words[-1].speaker == word.speaker
            close_enough = word.start - current_words[-1].end <= merge_gap_seconds
            within_limit = word.end - current_words[0].start <= maximum_turn_seconds
            if not (same_speaker and close_enough and within_limit):
                finish()
        current_words.append(word)
    finish()
    return turns
