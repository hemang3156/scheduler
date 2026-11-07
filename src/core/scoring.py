from dataclasses import dataclass
from typing import List, Union

from src.core.models import ScoredTask, TaskDraft


@dataclass(frozen=True)
class ScoringWeights:
    w1: float = 0.35  # Importance weight
    w2: float = 0.40  # Urgency weight
    w3: float = 0.25  # Interaction weight (importance * urgency)


def calculate_priority_score(
    importance: int,
    urgency: int,
    weights: ScoringWeights = ScoringWeights(),
) -> float:
    """
    Calculates composite priority score:
    Score = w1 * importance + w2 * urgency + w3 * (importance * urgency)
    """
    return (
        weights.w1 * importance
        + weights.w2 * urgency
        + weights.w3 * (importance * urgency)
    )


def sort_tasks_by_priority(
    tasks: List[Union[ScoredTask, TaskDraft]],
    weights: ScoringWeights = ScoringWeights(),
) -> List[Union[ScoredTask, TaskDraft]]:
    """Sorts tasks descending by composite priority score."""
    return sorted(
        tasks,
        key=lambda t: calculate_priority_score(t.importance, t.urgency, weights),
        reverse=True,
    )
