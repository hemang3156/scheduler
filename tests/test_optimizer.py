from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import pytest

from src.core.models import (
    ScoredTask,
    DateTimeAnchor,
    EventType,
    PriorityCategory,
    TimeSlot,
    PlacedTask,
    UnplacedTask,
    SchedulePlan,
)
from src.core.optimizer import optimize_schedule
from src.core.scoring import ScoringWeights

TZ = ZoneInfo("Asia/Kolkata")


def make_task(
    title: str,
    duration: int,
    importance: int = 3,
    urgency: int = 3,
    is_movable: bool = True,
    start_str: str | None = None,
    deadline_str: str | None = None,
    event_type: EventType = EventType.ROUTINE,
) -> ScoredTask:
    return ScoredTask(
        title=title,
        event_type=event_type,
        start=DateTimeAnchor(dateTime=start_str),
        end=DateTimeAnchor(dateTime=deadline_str),
        estimated_duration=duration,
        importance=importance,
        urgency=urgency,
        is_movable=is_movable,
        priority_category=PriorityCategory.ROUTINE,
        scoring_rationale="Test task",
    )


def test_empty_tasks_returns_empty_plan():
    slots = [
        TimeSlot(
            start=datetime(2026, 11, 6, 9, 0, tzinfo=TZ),
            end=datetime(2026, 11, 6, 12, 0, tzinfo=TZ),
            duration_minutes=180,
        )
    ]
    plan = optimize_schedule(tasks=[], available_slots=slots)
    assert len(plan.placed_tasks) == 0
    assert len(plan.unplaced_tasks) == 0
    assert len(plan.remaining_slots) == 1
    assert plan.remaining_slots[0].duration_minutes == 180


def test_empty_slots_marks_all_tasks_unplaced():
    task = make_task("Review Code", duration=45, importance=4, urgency=4)
    plan = optimize_schedule(tasks=[task], available_slots=[])
    assert len(plan.placed_tasks) == 0
    assert len(plan.unplaced_tasks) == 1
    assert "No free time slots available" in plan.unplaced_tasks[0].reason
    assert plan.unplaced_tasks[0].task.title == "Review Code"


def test_single_movable_task_placed_and_slot_shrunk():
    slot_start = datetime(2026, 11, 6, 10, 0, tzinfo=TZ)
    slot_end = datetime(2026, 11, 6, 12, 0, tzinfo=TZ)
    slots = [TimeSlot(start=slot_start, end=slot_end, duration_minutes=120)]

    task = make_task("Write Report", duration=60, importance=5, urgency=4)
    # Default buffer is 5 minutes, min_slot_duration is 15 minutes
    plan = optimize_schedule(tasks=[task], available_slots=slots, task_buffer_minutes=5)

    assert len(plan.placed_tasks) == 1
    assert len(plan.unplaced_tasks) == 0
    placed = plan.placed_tasks[0]
    assert placed.start == slot_start
    assert placed.end == slot_start + timedelta(minutes=60)
    assert placed.priority_score > 0

    # Remaining slot should start after task duration (60m) + buffer (5m) = 11:05
    assert len(plan.remaining_slots) == 1
    expected_remaining_start = slot_start + timedelta(minutes=65)
    assert plan.remaining_slots[0].start == expected_remaining_start
    assert plan.remaining_slots[0].end == slot_end
    assert plan.remaining_slots[0].duration_minutes == 55


def test_multiple_tasks_prioritized_placement():
    slot_start = datetime(2026, 11, 6, 9, 0, tzinfo=TZ)
    slot_end = datetime(2026, 11, 6, 11, 0, tzinfo=TZ)
    slots = [TimeSlot(start=slot_start, end=slot_end, duration_minutes=120)]

    low_prio = make_task("Clean Inbox", duration=30, importance=1, urgency=1)
    high_prio = make_task("Urgent Client Bug", duration=45, importance=5, urgency=5)

    plan = optimize_schedule(tasks=[low_prio, high_prio], available_slots=slots, task_buffer_minutes=5)

    assert len(plan.placed_tasks) == 2
    assert len(plan.unplaced_tasks) == 0

    # High priority should be placed first
    first_placed = plan.placed_tasks[0]
    assert first_placed.task.title == "Urgent Client Bug"
    assert first_placed.start == slot_start
    assert first_placed.end == slot_start + timedelta(minutes=45)

    # Low priority placed next (after 45m + 5m buffer = 9:50)
    second_placed = plan.placed_tasks[1]
    assert second_placed.task.title == "Clean Inbox"
    assert second_placed.start == slot_start + timedelta(minutes=50)
    assert second_placed.end == slot_start + timedelta(minutes=80)


def test_task_exceeding_duration_is_unplaced():
    slot_start = datetime(2026, 11, 6, 9, 0, tzinfo=TZ)
    slot_end = datetime(2026, 11, 6, 9, 30, tzinfo=TZ)
    slots = [TimeSlot(start=slot_start, end=slot_end, duration_minutes=30)]

    task = make_task("Deep Research", duration=60, importance=4, urgency=3)
    plan = optimize_schedule(tasks=[task], available_slots=slots)

    assert len(plan.placed_tasks) == 0
    assert len(plan.unplaced_tasks) == 1
    assert "No available free slot with sufficient duration" in plan.unplaced_tasks[0].reason


def test_task_with_deadline_constraint():
    slot_start = datetime(2026, 11, 6, 14, 0, tzinfo=TZ)
    slot_end = datetime(2026, 11, 6, 17, 0, tzinfo=TZ)
    slots = [TimeSlot(start=slot_start, end=slot_end, duration_minutes=180)]

    # Task requires 60 mins but deadline is 14:30 (slot starts at 14:00, so task would finish at 15:00 > 14:30)
    impossible_task = make_task(
        "Submit Proposal",
        duration=60,
        deadline_str="2026-11-06T14:30",
        importance=5,
        urgency=5,
    )

    plan = optimize_schedule(tasks=[impossible_task], available_slots=slots)
    assert len(plan.placed_tasks) == 0
    assert len(plan.unplaced_tasks) == 1
    assert "Cannot schedule before deadline" in plan.unplaced_tasks[0].reason

    # Task requiring 30 mins with deadline 14:30 CAN fit (14:00 - 14:30)
    possible_task = make_task(
        "Submit Short Form",
        duration=30,
        deadline_str="2026-11-06T14:30",
        importance=5,
        urgency=5,
    )
    plan_ok = optimize_schedule(tasks=[possible_task], available_slots=slots)
    assert len(plan_ok.placed_tasks) == 1
    assert plan_ok.placed_tasks[0].end == datetime(2026, 11, 6, 14, 30, tzinfo=TZ)


def test_fixed_unmovable_task_carves_slot():
    slot_start = datetime(2026, 11, 6, 10, 0, tzinfo=TZ)
    slot_end = datetime(2026, 11, 6, 13, 0, tzinfo=TZ)
    slots = [TimeSlot(start=slot_start, end=slot_end, duration_minutes=180)]

    # Fixed lecture from 11:00 to 12:00
    fixed_task = make_task(
        "Algorithms Lecture",
        duration=60,
        is_movable=False,
        start_str="2026-11-06T11:00",
        event_type=EventType.LECTURE,
    )

    plan = optimize_schedule(tasks=[fixed_task], available_slots=slots, task_buffer_minutes=5)
    assert len(plan.placed_tasks) == 1
    placed = plan.placed_tasks[0]
    assert placed.start == datetime(2026, 11, 6, 11, 0, tzinfo=TZ)
    assert placed.end == datetime(2026, 11, 6, 12, 0, tzinfo=TZ)

    # Prefix slot: 10:00 to 11:00 (60 mins)
    # Suffix slot: 12:05 to 13:00 (55 mins)
    assert len(plan.remaining_slots) == 2
    assert plan.remaining_slots[0].start == datetime(2026, 11, 6, 10, 0, tzinfo=TZ)
    assert plan.remaining_slots[0].end == datetime(2026, 11, 6, 11, 0, tzinfo=TZ)
    assert plan.remaining_slots[0].duration_minutes == 60

    assert plan.remaining_slots[1].start == datetime(2026, 11, 6, 12, 5, tzinfo=TZ)
    assert plan.remaining_slots[1].end == datetime(2026, 11, 6, 13, 0, tzinfo=TZ)
    assert plan.remaining_slots[1].duration_minutes == 55


def test_gradient_preference_placement_chooses_peak_hour():
    from src.core.models import PreferenceProfile

    slot_start = datetime(2026, 11, 6, 10, 0, tzinfo=TZ)
    slot_end = datetime(2026, 11, 6, 22, 0, tzinfo=TZ)
    slots = [TimeSlot(start=slot_start, end=slot_end, duration_minutes=720)]

    task = make_task("Guitar practice", duration=45, importance=3, urgency=3)

    # Profile with peak at 20:00 (1.0), low at other hours
    profile = PreferenceProfile(
        profile_name="guitar_practice",
        updated_count=1,
        hourly_weights=[0.0] * 20 + [1.0, 0.5, 0.0, 0.0],
    )

    plan = optimize_schedule(
        tasks=[task],
        available_slots=slots,
        profiles={"Guitar practice": profile},
    )

    assert len(plan.placed_tasks) == 1
    placed = plan.placed_tasks[0]
    # Should be placed at 20:00 instead of 10:00!
    assert placed.start == datetime(2026, 11, 6, 20, 0, tzinfo=TZ)
    assert placed.end == datetime(2026, 11, 6, 20, 45, tzinfo=TZ)


def test_gradient_anchor_proximity_placement():
    # Two slots: morning 10:00 -> 12:00, and evening 20:30 -> 22:00
    slots = [
        TimeSlot(
            start=datetime(2026, 11, 6, 10, 0, tzinfo=TZ),
            end=datetime(2026, 11, 6, 12, 0, tzinfo=TZ),
            duration_minutes=120,
        ),
        TimeSlot(
            start=datetime(2026, 11, 6, 20, 30, tzinfo=TZ),
            end=datetime(2026, 11, 6, 22, 0, tzinfo=TZ),
            duration_minutes=90,
        ),
    ]

    # Task has target anchor 8 PM (20:00)
    task = make_task(
        "Late Catchup",
        duration=45,
        start_str="2026-11-06T20:00",
        importance=3,
        urgency=3,
    )

    plan = optimize_schedule(tasks=[task], available_slots=slots)
    assert len(plan.placed_tasks) == 1
    placed = plan.placed_tasks[0]
    # Should pick evening slot closest to 20:00 anchor (20:30) rather than morning slot (10:00)
    assert placed.start == datetime(2026, 11, 6, 20, 30, tzinfo=TZ)

