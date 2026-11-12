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


def test_format_schedule_diff_with_existing_events():
    start = datetime(2026, 11, 6, 9, 0, tzinfo=TZ)
    end = datetime(2026, 11, 6, 10, 0, tzinfo=TZ)
    task = make_mock_task("Report Writing", 60)
    existing = [
        CalendarEvent(
            id="ev1",
            summary="Team Standup",
            start=start,
            end=end,
        )
    ]
    plan = SchedulePlan(
        placed_tasks=[
            PlacedTask(task=task, start=end, end=end + timedelta(hours=1), priority_score=7.0)
        ],
        unplaced_tasks=[],
        remaining_slots=[],
        existing_events=existing,
    )

    diff_output = format_schedule_diff(plan)
    assert "[ SCHEDULE PLAN (CALENDAR & PLACED TASKS) ]" in diff_output
    assert "Team Standup" in diff_output
    assert "Report Writing" in diff_output
    assert "Calendar" in diff_output
    assert "Placed" in diff_output
    assert "60m" in diff_output


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
        now=start_window,
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
        now=start_window,
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


def test_slots_with_end_before_now_are_ignored():
    """Verify that slots whose end_time is before or at now are not considered."""
    scheduler = AutonomousScheduler()
    start_window = datetime(2026, 11, 9, 9, 0, tzinfo=TZ)
    end_window = datetime(2026, 11, 9, 21, 0, tzinfo=TZ)
    now_time = datetime(2026, 11, 9, 16, 0, tzinfo=TZ)

    # Event from 17:00 to 21:00, leaving only 09:00 -> 16:00 (which is before or at now)
    existing_events = [
        CalendarEvent(
            id="ev_afternoon",
            summary="Late Meeting",
            start=datetime(2026, 11, 9, 17, 0, tzinfo=TZ),
            end=datetime(2026, 11, 9, 21, 0, tzinfo=TZ),
        )
    ]

    task = make_mock_task("Deep Study", duration=90, importance=5, urgency=5)

    plan = scheduler.generate_plan(
        tasks=[task],
        start_window=start_window,
        end_window=end_window,
        existing_events=existing_events,
        buffer_minutes=5,
        profiles={},
        now=now_time,
    )

    # The 90m task cannot fit in the remaining 16:00 -> 17:00 (60m) gap,
    # and all 09:00 -> 16:00 slots ended before/at now, so task must be unplaced.
    assert len(plan.placed_tasks) == 0
    assert len(plan.unplaced_tasks) == 1
    # Remaining slots should only contain future/valid slots (16:00 -> 17:00)
    for s in plan.remaining_slots:
        assert s.end > now_time
        assert s.start >= now_time


def test_slots_crossing_now_are_clipped():
    """Verify that a slot starting before now but ending after now is clipped to start at now."""
    scheduler = AutonomousScheduler()
    # Entire window 09:00 -> 21:00 with no commitments
    start_window = datetime(2026, 11, 9, 9, 0, tzinfo=TZ)
    end_window = datetime(2026, 11, 9, 21, 0, tzinfo=TZ)
    now_time = datetime(2026, 11, 9, 14, 30, tzinfo=TZ)

    task = make_mock_task("Immediate Task", duration=60, importance=5, urgency=5)

    plan = scheduler.generate_plan(
        tasks=[task],
        start_window=start_window,
        end_window=end_window,
        existing_events=[],
        buffer_minutes=5,
        profiles={},
        now=now_time,
    )

    assert len(plan.placed_tasks) == 1
    placed = plan.placed_tasks[0]
    # Placed task must start at or after now_time (14:30), not at 09:00!
    assert placed.start >= now_time
    assert placed.start == now_time
    assert placed.end == now_time + timedelta(minutes=60)


def test_continuous_24h_window_recognizes_late_evening_slots():
    """Verify that continuous_window=True preserves late-night windows across midnight (e.g. between dinner and sleep)."""
    scheduler = AutonomousScheduler()

    now_time = datetime(2026, 11, 9, 19, 45, tzinfo=TZ)
    start_window = now_time
    end_window = now_time + timedelta(hours=24)

    # Dinner: 20:30 -> 21:30 tonight
    # Sleep: 01:00 -> 08:00 tomorrow morning
    existing_events = [
        CalendarEvent(
            id="dinner",
            summary="Dinner",
            start=datetime(2026, 11, 9, 20, 30, tzinfo=TZ),
            end=datetime(2026, 11, 9, 21, 30, tzinfo=TZ),
        ),
        CalendarEvent(
            id="sleep",
            summary="Sleep",
            start=datetime(2026, 11, 10, 1, 0, tzinfo=TZ),
            end=datetime(2026, 11, 10, 8, 0, tzinfo=TZ),
        ),
    ]

    # Task: Study session from 22:00 to 23:30 (90 mins)
    task = ScoredTask(
        title="krd study",
        event_type=EventType.ROUTINE,
        start=DateTimeAnchor(dateTime="2026-11-09T22:00"),
        end=DateTimeAnchor(dateTime="2026-11-09T23:30"),
        estimated_duration=90,
        importance=4,
        urgency=5,
        is_movable=True,
        priority_category=PriorityCategory.URGENT,
        scoring_rationale="Study session tonight",
    )

    plan = scheduler.generate_plan(
        tasks=[task],
        start_window=start_window,
        end_window=end_window,
        existing_events=existing_events,
        buffer_minutes=5,
        profiles={},
        now=now_time,
        continuous_window=True,
    )

    assert len(plan.placed_tasks) == 1
    placed = plan.placed_tasks[0]
    assert placed.task.title == "krd study"
    assert placed.start == datetime(2026, 11, 9, 22, 0, tzinfo=TZ)
    assert placed.end == datetime(2026, 11, 9, 23, 30, tzinfo=TZ)

