import time
from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class ProgressResult:
    typed_chars: int
    correct_chars: int
    incorrect_positions: List[int]
    accuracy: float
    wpm: float


def calculate_progress(expected: str, typed: str, started_at_ms: int) -> ProgressResult:
    """Calculate authoritative race metrics from the submitted text."""
    incorrect_positions = [
        index
        for index, actual in enumerate(typed)
        if index >= len(expected) or actual != expected[index]
    ]
    typed_chars = min(len(typed), len(expected))
    correct_chars = sum(actual == wanted for actual, wanted in zip(typed, expected))
    accuracy = 100.0 if not typed else 100.0 * correct_chars / len(typed)
    elapsed_seconds = max((time.time() * 1000 - started_at_ms) / 1000, 0.25)
    wpm = (correct_chars / 5.0) / (elapsed_seconds / 60.0)
    return ProgressResult(
        typed_chars=typed_chars,
        correct_chars=correct_chars,
        incorrect_positions=incorrect_positions,
        accuracy=accuracy,
        wpm=wpm,
    )
