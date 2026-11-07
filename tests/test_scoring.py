import pytest

from src.core.models import TaskDraft
from src.core.scoring import (
    ScoringWeights,
    calculate_priority_score,
    sort_tasks_by_priority,
)


def test_priority_score_calculation_bounds():
    # Min score: imp=1, urg=1 -> 0.35(1) + 0.40(1) + 0.25(1) = 1.0
    min_score = calculate_priority_score(importance=1, urgency=1)
    assert min_score == pytest.approx(1.0)

    # Max score: imp=5, urg=5 -> 0.35(5) + 0.40(5) + 0.25(25) = 10.0
    max_score = calculate_priority_score(importance=5, urgency=5)
    assert max_score == pytest.approx(10.0)


def test_custom_weights_applied():
    custom_weights = ScoringWeights(w1=1.0, w2=0.0, w3=0.0)
    score = calculate_priority_score(importance=4, urgency=1, weights=custom_weights)
    assert score == pytest.approx(4.0)


def test_sort_tasks_by_priority():
    t_critical = TaskDraft(title="Exam Prep", importance=5, urgency=5)
    t_medium = TaskDraft(title="Grocery Run", importance=3, urgency=2)
    t_low = TaskDraft(title="Browse memes", importance=1, urgency=1)

    sorted_tasks = sort_tasks_by_priority([t_low, t_critical, t_medium])

    assert sorted_tasks[0].title == "Exam Prep"
    assert sorted_tasks[1].title == "Grocery Run"
    assert sorted_tasks[2].title == "Browse memes"
