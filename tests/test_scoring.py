import datetime as dt
import pytest

from src.core.models import TaskDraft, ScoredTask, DateTimeAnchor, EventType, PriorityCategory
from src.core.scoring import (
    ScoringWeights,
    calculate_priority_score,
    sort_tasks_by_priority,
)
from src.agent.scorer import sanitize_task_anchors


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


def test_sanitize_task_anchors_midnight_rollover():
    """When end time is earlier in the day than start time, end date must roll forward by 1 day."""
    task = ScoredTask(
        title="Late Study",
        event_type=EventType.ROUTINE,
        start=DateTimeAnchor(dateTime="2026-11-09T22:45"),
        end=DateTimeAnchor(dateTime="2026-11-09T00:15"),
        estimated_duration=90,
        importance=4,
        urgency=5,
        is_movable=True,
        priority_category=PriorityCategory.URGENT,
        scoring_rationale="Study session across midnight",
    )
    sanitized = sanitize_task_anchors(task, reference_now=dt.datetime(2026, 11, 9, 22, 20))
    assert sanitized.start.dateTime == "2026-11-09T22:45"
    assert sanitized.end.dateTime == "2026-11-10T00:15"


def test_sanitize_task_anchors_12am_confusion_and_pm_shift():
    """Corrects 10:45 AM -> 10:45 PM when running late at night, and 12:15 PM -> 12:15 AM next day."""
    task = ScoredTask(
        title="Late Study",
        event_type=EventType.ROUTINE,
        start=DateTimeAnchor(dateTime="2026-11-09T10:45"),
        end=DateTimeAnchor(dateTime="2026-11-09T12:15"),
        estimated_duration=90,
        importance=4,
        urgency=5,
        is_movable=True,
        priority_category=PriorityCategory.URGENT,
        scoring_rationale="Late night study with ambiguous AM/PM",
    )
    sanitized = sanitize_task_anchors(task, reference_now=dt.datetime(2026, 11, 9, 22, 20))
    assert sanitized.start.dateTime == "2026-11-09T22:45"
    assert sanitized.end.dateTime == "2026-11-10T00:15"


def test_all_day_evaluative_anchors_prompt_instructions():
    from src.agent.prompt_templates import NOTES_EXTRACTION_PROMPT_TEMPLATE, SCORING_PROMPT_TEMPLATE
    assert "00:00:00" in NOTES_EXTRACTION_PROMPT_TEMPLATE
    assert "23:59:59" in NOTES_EXTRACTION_PROMPT_TEMPLATE
    assert "00:00:00" in SCORING_PROMPT_TEMPLATE
    assert "23:59:59" in SCORING_PROMPT_TEMPLATE


def test_sanitize_task_anchors_all_day_milestone():
    task = ScoredTask(
        title="krd-quiz on 17th oct",
        event_type=EventType.EVALUATIVE,
        start=DateTimeAnchor(dateTime="2026-10-17T00:00:00"),
        end=DateTimeAnchor(dateTime="2026-10-17T23:59:59"),
        estimated_duration=60,
        importance=5,
        urgency=4,
        is_movable=True,
        priority_category=PriorityCategory.URGENT,
        scoring_rationale="Evaluative quiz for full day.",
    )
    sanitized = sanitize_task_anchors(task, reference_now=dt.datetime(2026, 10, 17, 10, 0))
    # Full day boundaries must be preserved without distortion
    assert sanitized.start.dateTime == "2026-10-17T00:00:00"
    assert sanitized.end.dateTime == "2026-10-17T23:59:59"



