from datetime import date, datetime, timedelta
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
import pytest

from src.core.models import (
    CalendarEvent,
    ScoredTask,
    DateTimeAnchor,
    EventType,
    PriorityCategory,
    TimeSlot,
    SchedulePlan,
    PlacedTask,
    UnplacedTask,
)
from src.agent.scheduler_agent import AutonomousScheduler, format_schedule_diff
from src.agent.prompt_templates import (
    build_notes_extraction_prompt,
    build_scoring_prompt,
)

TZ = ZoneInfo("Asia/Kolkata")


def make_mock_task(
    title: str,
    duration: int,
    importance: int = 4,
    urgency: int = 4,
    is_movable: bool = True,
) -> ScoredTask:
    return ScoredTask(
        title=title,
        event_type=EventType.ROUTINE,
        start=DateTimeAnchor(dateTime=None),
        end=DateTimeAnchor(dateTime=None),
        estimated_duration=duration,
        importance=importance,
        urgency=urgency,
        is_movable=is_movable,
        priority_category=PriorityCategory.URGENT,
        scoring_rationale="Agent test task",
    )


def test_prompt_template_builders_avoid_keyerrors():
    json_payload = '{"dateTime": null, "title": "Test"}'
    scoring_p = build_scoring_prompt(json_payload, "2026-11-06", "Asia/Kolkata")
    assert "2026-11-06" in scoring_p
    assert "Asia/Kolkata" in scoring_p
    assert '{"dateTime": null' in scoring_p

    notes_p = build_notes_extraction_prompt("Finish CCUS report tmrw", "2026-11-06", "Asia/Kolkata")
    assert "Finish CCUS report tmrw" in notes_p
    assert "2026-11-06" in notes_p


def test_format_schedule_diff_renders_sections():
    start = datetime(2026, 11, 6, 9, 0, tzinfo=TZ)
    end = datetime(2026, 11, 6, 10, 0, tzinfo=TZ)
    task = make_mock_task("Report Writing", 60)

    plan = SchedulePlan(
        placed_tasks=[
            PlacedTask(task=task, start=start, end=end, priority_score=7.0)
        ],
        unplaced_tasks=[
            UnplacedTask(
                task=make_mock_task("Gym Workout", 90),
                reason="Slot too small",
                priority_score=4.0,
            )
        ],
        remaining_slots=[
            TimeSlot(start=end, end=end + timedelta(hours=2), duration_minutes=120)
        ],
    )

    diff_output = format_schedule_diff(plan)
    assert "[ PLACED TASKS ]" in diff_output
    assert "Report Writing" in diff_output
    assert "7.00" in diff_output
    assert "[ UNPLACED TASKS ]" in diff_output
    assert "Gym Workout" in diff_output
    assert "Slot too small" in diff_output
    assert "[ REMAINING AVAILABLE SLOTS ]" in diff_output
    assert "120 mins" in diff_output


def test_autonomous_scheduler_generate_plan_with_existing_events():
    scheduler = AutonomousScheduler()

    start_window = datetime(2026, 11, 6, 9, 0, tzinfo=TZ)
    end_window = datetime(2026, 11, 6, 18, 0, tzinfo=TZ)

    # Existing meeting 12:00 -> 13:00 (lunch)
    existing_events = [
        CalendarEvent(
            id="lunch_1",
            summary="Team Lunch",
            start=datetime(2026, 11, 6, 12, 0, tzinfo=TZ),
            end=datetime(2026, 11, 6, 13, 0, tzinfo=TZ),
        )
    ]

    tasks = [
        make_mock_task("Deep Work Session", duration=120, importance=5, urgency=5),
        make_mock_task("Quick Catchup", duration=30, importance=3, urgency=3),
    ]

    plan = scheduler.generate_plan(
        tasks=tasks,
        start_window=start_window,
        end_window=end_window,
        existing_events=existing_events,
        buffer_minutes=5,
        profiles={},
    )

    assert len(plan.placed_tasks) == 2
    assert len(plan.unplaced_tasks) == 0

    # Deep Work should be placed at 09:00 -> 11:00
    assert plan.placed_tasks[0].task.title == "Deep Work Session"
    assert plan.placed_tasks[0].start == start_window
    assert plan.placed_tasks[0].end == start_window + timedelta(minutes=120)

    # Quick Catchup placed after 5 min buffer at 11:05 -> 11:35
    assert plan.placed_tasks[1].task.title == "Quick Catchup"
    assert plan.placed_tasks[1].start == datetime(2026, 11, 6, 11, 5, tzinfo=TZ)
    assert plan.placed_tasks[1].end == datetime(2026, 11, 6, 11, 35, tzinfo=TZ)


def test_autonomous_scheduler_apply_plan_dry_run():
    scheduler = AutonomousScheduler()
    task = make_mock_task("Read Book", 30)
    start = datetime(2026, 11, 6, 10, 0, tzinfo=TZ)
    end = datetime(2026, 11, 6, 10, 30, tzinfo=TZ)

    plan = SchedulePlan(
        placed_tasks=[PlacedTask(task=task, start=start, end=end, priority_score=5.0)],
        unplaced_tasks=[],
        remaining_slots=[],
    )

    created_events = scheduler.apply_plan(plan, dry_run=True)
    assert len(created_events) == 1
    assert created_events[0].summary == "Read Book"
    assert created_events[0].start == start
    assert created_events[0].end == end


def test_autonomous_scheduler_apply_plan_with_mock_client():
    mock_client = MagicMock()
    mock_client.create_event.side_effect = lambda ev: ev

    scheduler = AutonomousScheduler(calendar_client=mock_client)
    task = make_mock_task("Review Code", 45)
    start = datetime(2026, 11, 6, 15, 0, tzinfo=TZ)
    end = datetime(2026, 11, 6, 15, 45, tzinfo=TZ)

    plan = SchedulePlan(
        placed_tasks=[PlacedTask(task=task, start=start, end=end, priority_score=6.0)],
        unplaced_tasks=[],
        remaining_slots=[],
    )

    created_events = scheduler.apply_plan(plan, dry_run=False, evolve_preferences=False)
    assert len(created_events) == 1
    assert mock_client.create_event.called
    assert created_events[0].summary == "Review Code"


def test_autonomous_scheduler_generate_plan_multi_day_range():
    """Verify that multi-day date ranges generate daily slots without leaking overnight gaps."""
    scheduler = AutonomousScheduler()

    # Range across 2 days: Nov 6 09:00 -> Nov 7 12:00
    start_window = datetime(2026, 11, 6, 9, 0, tzinfo=TZ)
    end_window = datetime(2026, 11, 7, 12, 0, tzinfo=TZ)

    # Day 1 has an event taking 10:00 -> 12:00, leaving only 09:00 -> 10:00 (60m)
    existing_events = [
        CalendarEvent(
            id="ev_day1",
            summary="Morning Meeting",
            start=datetime(2026, 11, 6, 10, 0, tzinfo=TZ),
            end=datetime(2026, 11, 6, 12, 0, tzinfo=TZ),
        )
    ]

    # Task A is 45m (fits on Day 1: 09:00 -> 09:45)
    # Task B is 90m (does NOT fit in Day 1's remaining 15m, so it must spill to Day 2: 09:00 -> 10:30)
    tasks = [
        make_mock_task("Task A", duration=45, importance=5, urgency=5),
        make_mock_task("Task B", duration=90, importance=4, urgency=4),
    ]

    plan = scheduler.generate_plan(
        tasks=tasks,
        start_window=start_window,
        end_window=end_window,
        existing_events=existing_events,
        buffer_minutes=5,
        profiles={},
    )

    assert len(plan.placed_tasks) == 2
    assert len(plan.unplaced_tasks) == 0

    # Task A placed on Day 1
    assert plan.placed_tasks[0].task.title == "Task A"
    assert plan.placed_tasks[0].start.date() == date(2026, 11, 6)
    assert plan.placed_tasks[0].start == datetime(2026, 11, 6, 9, 0, tzinfo=TZ)

    # Task B placed on Day 2 (NOT overnight!)
    assert plan.placed_tasks[1].task.title == "Task B"
    assert plan.placed_tasks[1].start.date() == date(2026, 11, 7)
    assert plan.placed_tasks[1].start == datetime(2026, 11, 7, 9, 0, tzinfo=TZ)
    assert plan.placed_tasks[1].end == datetime(2026, 11, 7, 10, 30, tzinfo=TZ)
