"""
End-to-End Characterization Tests for Core Scheduler Loop.

Verifies observable behavior through public interfaces without depending on
private implementation details (principle-test-behavior-not-implementation):
1. Task Registration & Ingestion
2. Priority Queueing & Ranking
3. Timing, Delays & Slot Carving
4. Recurring Execution & Multi-Day Cycles
5. Error Handling & Diagnostics
"""

from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import List, Optional
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo
import pytest
from pydantic import ValidationError
from googleapiclient.errors import HttpError

from src.core.models import (
    CalendarEvent,
    DateTimeAnchor,
    EventType,
    PlacedTask,
    PreferenceProfile,
    PriorityCategory,
    SchedulePlan,
    ScoredTask,
    TaskDraft,
    TimeSlot,
    UnplacedTask,
    UpdatedCurveResponse,
)
from src.core.scoring import ScoringWeights, calculate_priority_score
from src.core.optimizer import optimize_schedule
from src.core.preference_engine import sanitize_weights
from src.core.exceptions import (
    CalendarAuthError,
    CalendarError,
    CalendarResourceNotFoundError,
)
from src.agent.scheduler_agent import AutonomousScheduler, format_schedule_diff
from src.tools.calender_client import CalendarClient

TZ = ZoneInfo("Asia/Kolkata")


def make_task(
    title: str,
    duration: int = 30,
    importance: int = 3,
    urgency: int = 3,
    is_movable: bool = True,
    start_anchor: Optional[str] = None,
    end_anchor: Optional[str] = None,
    event_type: EventType = EventType.ROUTINE,
    priority_category: PriorityCategory = PriorityCategory.ROUTINE,
) -> ScoredTask:
    """Helper to construct valid ScoredTask instances for public API testing."""
    return ScoredTask(
        title=title,
        event_type=event_type,
        start=DateTimeAnchor(dateTime=start_anchor),
        end=DateTimeAnchor(dateTime=end_anchor),
        estimated_duration=duration,
        importance=importance,
        urgency=urgency,
        is_movable=is_movable,
        priority_category=priority_category,
        scoring_rationale=f"Characterization test task: {title}",
    )


# ============================================================================
# 1. Task Registration & Ingestion
# ============================================================================

class TestTaskRegistrationAndIngestion:
    """Characterizes task schema contract and ingestion into the scheduler pipeline."""

    def test_task_schema_contract_validation(self):
        """Verifies boundary validation for task attributes at the interface level."""
        # Valid task registration
        task = make_task("Valid Task", duration=45, importance=5, urgency=4)
        assert task.title == "Valid Task"
        assert task.estimated_duration == 45
        assert task.importance == 5
        assert task.urgency == 4

        # Duration must be >= 1 minute
        with pytest.raises(ValidationError):
            make_task("Zero Duration", duration=0)

        # Importance must be between 1 and 5
        with pytest.raises(ValidationError):
            make_task("Too Important", importance=6)
        with pytest.raises(ValidationError):
            make_task("Not Important", importance=0)

        # Urgency must be between 1 and 5
        with pytest.raises(ValidationError):
            make_task("Too Urgent", urgency=6)
        with pytest.raises(ValidationError):
            make_task("Not Urgent", urgency=0)

    def test_registration_and_end_to_end_plan_generation(self):
        """Verifies that registered tasks pass through the scheduler loop into a complete plan."""
        scheduler = AutonomousScheduler(tz_name="Asia/Kolkata")
        start_w = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
        end_w = datetime(2026, 11, 10, 17, 0, tzinfo=TZ)

        tasks = [
            make_task("Sync Standup", duration=30, importance=5, urgency=5, is_movable=False,
                      start_anchor="2026-11-10T09:30", event_type=EventType.MEETING),
            make_task("Feature Engineering", duration=90, importance=4, urgency=4, is_movable=True),
            make_task("Casual Reading", duration=60, importance=2, urgency=2, is_movable=True),
        ]

        plan = scheduler.generate_plan(
            tasks=tasks,
            start_window=start_w,
            end_window=end_w,
            existing_events=[],
            now=start_w,
        )

        assert isinstance(plan, SchedulePlan)
        # Total registered tasks must equal placed + unplaced
        assert len(plan.placed_tasks) + len(plan.unplaced_tasks) == len(tasks)
        assert len(plan.placed_tasks) == 3
        assert len(plan.unplaced_tasks) == 0

        # Fixed sync standup must be anchored at exactly 09:30
        standup_placed = next(p for p in plan.placed_tasks if p.task.title == "Sync Standup")
        assert standup_placed.start == datetime(2026, 11, 10, 9, 30, tzinfo=TZ)
        assert standup_placed.end == datetime(2026, 11, 10, 10, 0, tzinfo=TZ)

    def test_task_draft_compatibility_in_optimizer(self):
        """Verifies that simpler TaskDraft instances can also be ingested by the optimizer."""
        slot = TimeSlot(
            start=datetime(2026, 11, 10, 10, 0, tzinfo=TZ),
            end=datetime(2026, 11, 10, 12, 0, tzinfo=TZ),
            duration_minutes=120,
        )
        draft = TaskDraft(title="Draft Task", importance=4, urgency=3, tags=["dev"])
        setattr(draft, "estimated_duration", 45)
        setattr(draft, "is_movable", True)

        plan = optimize_schedule(tasks=[draft], available_slots=[slot])
        assert len(plan.placed_tasks) == 1
        assert plan.placed_tasks[0].task.title == "Draft Task"
        assert plan.placed_tasks[0].end - plan.placed_tasks[0].start == timedelta(minutes=45)


# ============================================================================
# 2. Priority Queueing & Ranking
# ============================================================================

class TestPriorityQueueing:
    """Characterizes priority score computation, queue ordering, and preemption."""

    def test_priority_score_calculation(self):
        """Validates the mathematical formula: w1*imp + w2*urg + w3*(imp*urg)."""
        weights = ScoringWeights(w1=0.35, w2=0.40, w3=0.25)
        # Min score (imp=1, urg=1): 0.35 + 0.40 + 0.25 = 1.0
        assert calculate_priority_score(1, 1, weights) == pytest.approx(1.0)
        # Max score (imp=5, urg=5): 0.35*5 + 0.40*5 + 0.25*25 = 1.75 + 2.00 + 6.25 = 10.0
        assert calculate_priority_score(5, 5, weights) == pytest.approx(10.0)
        # Intermediate (imp=4, urg=2): 0.35*4 + 0.40*2 + 0.25*8 = 1.40 + 0.80 + 2.00 = 4.20
        assert calculate_priority_score(4, 2, weights) == pytest.approx(4.20)

    def test_fixed_tasks_carve_calendar_before_movable_tasks(self):
        """Fixed commitments must be scheduled before movable tasks, even if movable task has higher priority."""
        scheduler = AutonomousScheduler()
        start_w = datetime(2026, 11, 10, 10, 0, tzinfo=TZ)
        end_w = datetime(2026, 11, 10, 11, 30, tzinfo=TZ)

        # High priority movable task (score 10.0) wanting 60 minutes
        high_movable = make_task("Urgent Bug", duration=60, importance=5, urgency=5, is_movable=True)
        # Low priority fixed task (score 1.0) anchored at 10:15 -> 10:45 (30 minutes)
        low_fixed = make_task("Routine Check-in", duration=30, importance=1, urgency=1, is_movable=False,
                              start_anchor="2026-11-10T10:15")

        # In a 90m window (10:00 -> 11:30), placing fixed task at 10:15 leaves:
        # prefix 10:00 -> 10:15 (15m) and suffix 10:50 -> 11:30 (40m with 5m buffer).
        # Neither prefix (15m) nor suffix (40m) can fit the 60m movable task!
        plan = scheduler.generate_plan(
            tasks=[high_movable, low_fixed],
            start_window=start_w,
            end_window=end_w,
            existing_events=[],
            now=start_w,
        )

        # Fixed task must be placed at exactly its anchor
        assert len(plan.placed_tasks) == 1
        assert plan.placed_tasks[0].task.title == "Routine Check-in"
        assert plan.placed_tasks[0].start == datetime(2026, 11, 10, 10, 15, tzinfo=TZ)

        # Movable task cannot fit and must be unplaced
        assert len(plan.unplaced_tasks) == 1
        assert plan.unplaced_tasks[0].task.title == "Urgent Bug"

    def test_fixed_task_collision_prioritizes_higher_score(self):
        """When two fixed tasks request colliding slots, the higher-priority task wins."""
        slot = TimeSlot(
            start=datetime(2026, 11, 10, 14, 0, tzinfo=TZ),
            end=datetime(2026, 11, 10, 16, 0, tzinfo=TZ),
            duration_minutes=120,
        )
        task_high = make_task("Board Meeting", duration=60, importance=5, urgency=5, is_movable=False,
                              start_anchor="2026-11-10T14:00")
        task_low = make_task("Coffee Chat", duration=60, importance=1, urgency=1, is_movable=False,
                             start_anchor="2026-11-10T14:00")

        plan = optimize_schedule(tasks=[task_low, task_high], available_slots=[slot])
        assert len(plan.placed_tasks) == 1
        assert plan.placed_tasks[0].task.title == "Board Meeting"
        assert len(plan.unplaced_tasks) == 1
        assert plan.unplaced_tasks[0].task.title == "Coffee Chat"
        assert "overlaps" in plan.unplaced_tasks[0].reason

    def test_movable_tasks_contention_and_deadline_tiebreaker(self):
        """When scores match, task with earlier deadline has higher scheduling precedence."""
        slot = TimeSlot(
            start=datetime(2026, 11, 10, 10, 0, tzinfo=TZ),
            end=datetime(2026, 11, 10, 11, 0, tzinfo=TZ),
            duration_minutes=60,
        )
        # Both tasks have identical score (imp=4, urg=4 -> 7.2) and duration=45m.
        # Slot is only 60m, so only one can be placed.
        task_late = make_task("Task Late Deadline", duration=45, importance=4, urgency=4,
                              end_anchor="2026-11-10T18:00")
        task_early = make_task("Task Early Deadline", duration=45, importance=4, urgency=4,
                               end_anchor="2026-11-10T10:50")

        plan = optimize_schedule(tasks=[task_late, task_early], available_slots=[slot])
        assert len(plan.placed_tasks) == 1
        assert plan.placed_tasks[0].task.title == "Task Early Deadline"
        assert len(plan.unplaced_tasks) == 1
        assert plan.unplaced_tasks[0].task.title == "Task Late Deadline"


# ============================================================================
# 3. Timing, Delays & Slot Carving
# ============================================================================

class TestTimingDelaysAndCarving:
    """Characterizes timing constraints, buffers, anchor proximity, and historical clipping."""

    def test_inter_task_buffer_delay_enforcement(self):
        """Verifies that inter-task buffer delay is maintained between consecutively scheduled tasks."""
        slot_start = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
        slot_end = datetime(2026, 11, 10, 13, 0, tzinfo=TZ)
        slots = [TimeSlot(start=slot_start, end=slot_end, duration_minutes=240)]

        t1 = make_task("Task 1", duration=60, importance=5, urgency=5)
        t2 = make_task("Task 2", duration=60, importance=4, urgency=4)

        buffer_minutes = 15
        plan = optimize_schedule(tasks=[t1, t2], available_slots=slots, task_buffer_minutes=buffer_minutes)

        assert len(plan.placed_tasks) == 2
        p1, p2 = plan.placed_tasks[0], plan.placed_tasks[1]

        # P1 placed at 09:00 -> 10:00
        assert p1.start == slot_start
        assert p1.end == slot_start + timedelta(minutes=60)

        # P2 must start after P1.end + buffer_minutes (10:15)
        expected_p2_start = p1.end + timedelta(minutes=buffer_minutes)
        assert p2.start == expected_p2_start
        assert p2.end == expected_p2_start + timedelta(minutes=60)

        # Remaining slot must start after P2.end + buffer_minutes (11:30)
        assert len(plan.remaining_slots) == 1
        expected_rem_start = p2.end + timedelta(minutes=buffer_minutes)
        assert plan.remaining_slots[0].start == expected_rem_start
        assert plan.remaining_slots[0].end == slot_end

    def test_min_slot_duration_threshold_discards_small_slivers(self):
        """Fragments smaller than min_slot_duration must be pruned from remaining slots."""
        # 70-minute slot: placing a 60-minute task with 5-minute buffer leaves a 5-minute fragment
        slot = TimeSlot(
            start=datetime(2026, 11, 10, 10, 0, tzinfo=TZ),
            end=datetime(2026, 11, 10, 11, 10, tzinfo=TZ),
            duration_minutes=70,
        )
        task = make_task("Single Task", duration=60)
        plan = optimize_schedule(tasks=[task], available_slots=[slot], task_buffer_minutes=5, min_slot_duration=15)

        assert len(plan.placed_tasks) == 1
        # The remaining 5-minute suffix is < 15 minutes, so it must be discarded
        assert len(plan.remaining_slots) == 0

    def test_preferred_time_anchor_proximity(self):
        """Movable tasks with a preferred anchor are placed at or closest to their requested anchor."""
        slot = TimeSlot(
            start=datetime(2026, 11, 10, 9, 0, tzinfo=TZ),
            end=datetime(2026, 11, 10, 18, 0, tzinfo=TZ),
            duration_minutes=540,
        )
        # Task is flexible/movable, but expresses a preference for 14:00
        task = make_task(
            "Afternoon Research",
            duration=60,
            is_movable=True,
            start_anchor="2026-11-10T14:00",
        )

        plan = optimize_schedule(tasks=[task], available_slots=[slot])
        assert len(plan.placed_tasks) == 1
        placed = plan.placed_tasks[0]
        # Should be placed exactly at the preferred anchor 14:00
        assert placed.start == datetime(2026, 11, 10, 14, 0, tzinfo=TZ)
        assert placed.end == datetime(2026, 11, 10, 15, 0, tzinfo=TZ)

    def test_now_clipping_and_past_slot_filtering(self):
        """The scheduler loop prunes slots ending before 'now' and clips active slots starting before 'now'."""
        scheduler = AutonomousScheduler()
        start_w = datetime(2026, 11, 10, 8, 0, tzinfo=TZ)
        end_w = datetime(2026, 11, 10, 18, 0, tzinfo=TZ)
        now_time = datetime(2026, 11, 10, 11, 30, tzinfo=TZ)

        # Existing meeting 1: 08:00 -> 10:00 (leaves 10:00 -> 11:00 free, but strictly in the past)
        # Existing meeting 2: 11:00 -> 11:15
        # (leaves 11:15 -> 12:30 free, crossing now=11:30)
        # Existing meeting 3: 12:30 -> 18:00 (afternoon booked)
        existing = [
            CalendarEvent(
                id="past_meeting",
                summary="Old Meeting",
                start=datetime(2026, 11, 10, 8, 0, tzinfo=TZ),
                end=datetime(2026, 11, 10, 10, 0, tzinfo=TZ),
            ),
            CalendarEvent(
                id="prep_meeting",
                summary="Prep Meeting",
                start=datetime(2026, 11, 10, 11, 0, tzinfo=TZ),
                end=datetime(2026, 11, 10, 11, 15, tzinfo=TZ),
            ),
            CalendarEvent(
                id="afternoon_lockout",
                summary="Afternoon Block",
                start=datetime(2026, 11, 10, 12, 30, tzinfo=TZ),
                end=datetime(2026, 11, 10, 18, 0, tzinfo=TZ),
            ),
        ]

        task = make_task("Immediate Task", duration=45, importance=5, urgency=5)
        plan = scheduler.generate_plan(
            tasks=[task],
            start_window=start_w,
            end_window=end_w,
            existing_events=existing,
            now=now_time,
            profiles={},
        )

        assert len(plan.placed_tasks) == 1
        placed = plan.placed_tasks[0]
        # 10:00 -> 11:00 slot was in the past and ignored.
        # 11:15 -> 12:30 slot crossed now=11:30 and was clipped to 11:30 -> 12:30.
        # Placed task must start at now_time (11:30), NOT in the past.
        assert placed.start == now_time
        assert placed.end == now_time + timedelta(minutes=45)


# ============================================================================
# 4. Recurring Execution & Multi-Day Cycles
# ============================================================================

class TestRecurringExecution:
    """Characterizes recurring multi-day windows, preference evolution loops, and calendar sync."""

    def test_multi_day_recurring_daily_windows(self):
        """Verifies multi-day schedule planning respects daily hours and spills across days without scheduling overnight."""
        scheduler = AutonomousScheduler()
        # 3-day window from Nov 10 09:00 to Nov 12 18:00
        start_w = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
        end_w = datetime(2026, 11, 12, 18, 0, tzinfo=TZ)

        # Day 1 is fully booked with meetings 09:00 -> 18:00
        day1_block = CalendarEvent(
            id="day1_all",
            summary="All Day Workshop Day 1",
            start=datetime(2026, 11, 10, 9, 0, tzinfo=TZ),
            end=datetime(2026, 11, 10, 18, 0, tzinfo=TZ),
        )

        # Day 2 has only 120 minutes free (09:00 -> 11:00) before meetings occupy the rest
        day2_block = CalendarEvent(
            id="day2_rest",
            summary="Afternoon Workshop Day 2",
            start=datetime(2026, 11, 11, 11, 0, tzinfo=TZ),
            end=datetime(2026, 11, 11, 18, 0, tzinfo=TZ),
        )

        # Tasks to place:
        # Task A: 120 mins (fits Day 2 09:00 -> 11:00)
        # Task B: 120 mins (must spill over to Day 3 09:00 -> 11:00)
        task_a = make_task("Big Task A", duration=120, importance=5, urgency=5)
        task_b = make_task("Big Task B", duration=120, importance=4, urgency=4)

        plan = scheduler.generate_plan(
            tasks=[task_a, task_b],
            start_window=start_w,
            end_window=end_w,
            existing_events=[day1_block, day2_block],
            daily_start_time=time(9, 0),
            daily_end_time=time(18, 0),
            now=start_w,
            profiles={},
        )

        assert len(plan.placed_tasks) == 2
        p_a, p_b = plan.placed_tasks[0], plan.placed_tasks[1]

        # Task A placed on Day 2 in working hours
        assert p_a.start.date() == date(2026, 11, 11)
        assert p_a.start == datetime(2026, 11, 11, 9, 0, tzinfo=TZ)
        assert p_a.end == datetime(2026, 11, 11, 11, 0, tzinfo=TZ)

        # Task B spills over to Day 3 in working hours (NOT scheduled overnight!)
        assert p_b.start.date() == date(2026, 11, 12)
        assert p_b.start == datetime(2026, 11, 12, 9, 0, tzinfo=TZ)
        assert p_b.end == datetime(2026, 11, 12, 11, 0, tzinfo=TZ)

        # Verify no task was placed during non-working overnight hours (18:00 -> 09:00)
        for placed in plan.placed_tasks:
            assert placed.start.time() >= time(9, 0)
            assert placed.end.time() <= time(18, 0)

    def test_recurring_preference_feedback_loop(self, tmp_path, monkeypatch):
        """Simulates recurring placement and preference learning across two consecutive cycles."""
        test_profiles_dir = tmp_path / "preference_profiles"
        test_categories_dir = test_profiles_dir / "categories"
        test_baseline = test_profiles_dir / "global_baseline.json"

        monkeypatch.setattr("src.core.preference_engine.PROFILES_DIR", test_profiles_dir)
        monkeypatch.setattr("src.core.preference_engine.CATEGORIES_DIR", test_categories_dir)
        monkeypatch.setattr("src.core.preference_engine.GLOBAL_BASELINE_PATH", test_baseline)

        # Cycle 1: Mock LLM evolution when task is scheduled at 20:00 (8 PM)
        mock_client = MagicMock()
        mock_response = MagicMock()
        # Calibrated response giving high preference to 20:00 (index 20)
        calibrated_weights = [0.1] * 20 + [0.95] + [0.1] * 3
        mock_response.parsed = UpdatedCurveResponse(
            hourly_weights=calibrated_weights,
            update_rationale="Reinforced 8 PM preference for evening guitar practice.",
        )
        mock_client.models.generate_content.return_value = mock_response
        monkeypatch.setattr("src.agent.curve_updater.genai.Client", lambda api_key: mock_client)

        scheduler = AutonomousScheduler()

        # Day 1: Place evening guitar practice
        task_cycle1 = make_task("Guitar Practice", duration=45, importance=4, urgency=4,
                                start_anchor="2026-11-10T20:00")
        start_w1 = datetime(2026, 11, 10, 9, 0, tzinfo=TZ)
        end_w1 = datetime(2026, 11, 10, 22, 0, tzinfo=TZ)

        plan1 = scheduler.generate_plan(
            tasks=[task_cycle1],
            start_window=start_w1,
            end_window=end_w1,
            existing_events=[],
            now=start_w1,
        )

        mock_cal_client = MagicMock()
        mock_cal_client.create_event.side_effect = lambda ev: ev

        scheduler.calendar_client = mock_cal_client
        events1 = scheduler.apply_plan(plan1, dry_run=False, evolve_preferences=True)

        assert len(events1) == 1
        assert events1[0].summary == "Guitar Practice"

        # Verify disk profile was persisted with updated_count = 1
        saved_profile_path = test_categories_dir / "guitar_practice.json"
        assert saved_profile_path.exists()

        # Cycle 2: On next day, schedule without explicit anchor in a day with open morning & evening
        # The scheduler should load the updated profile and prefer 20:00 over other open hours
        start_w2 = datetime(2026, 11, 11, 9, 0, tzinfo=TZ)
        end_w2 = datetime(2026, 11, 11, 22, 0, tzinfo=TZ)
        task_cycle2 = make_task("Guitar Practice", duration=45, importance=4, urgency=4, is_movable=True)

        plan2 = scheduler.generate_plan(
            tasks=[task_cycle2],
            start_window=start_w2,
            end_window=end_w2,
            existing_events=[],
            now=start_w2,
        )

        assert len(plan2.placed_tasks) == 1
        # The optimizer, guided by W[20]=0.95, places the task at 20:00
        assert plan2.placed_tasks[0].start.hour == 20

    def test_apply_plan_dry_run_vs_sync_effects(self):
        """Characterizes apply_plan behavior: dry-run performs no network mutations; live calls client."""
        mock_client = MagicMock()
        mock_client.create_event.side_effect = lambda ev: ev
        scheduler = AutonomousScheduler(calendar_client=mock_client)

        task = make_task("Report", duration=30)
        start = datetime(2026, 11, 10, 10, 0, tzinfo=TZ)
        plan = SchedulePlan(
            placed_tasks=[PlacedTask(task=task, start=start, end=start + timedelta(minutes=30), priority_score=5.0)],
            unplaced_tasks=[],
            remaining_slots=[],
        )

        # 1. Dry run: no external mutations
        dry_events = scheduler.apply_plan(plan, dry_run=True)
        assert len(dry_events) == 1
        assert "dry_run" in dry_events[0].id
        assert not mock_client.create_event.called

        # 2. Live sync: invokes client.create_event
        live_events = scheduler.apply_plan(plan, dry_run=False, evolve_preferences=False)
        assert len(live_events) == 1
        assert mock_client.create_event.called
        assert live_events[0].summary == "Report"


# ============================================================================
# 5. Error Handling & Diagnostics
# ============================================================================

class TestErrorHandlingAndDiagnostics:
    """Characterizes failure modes, diagnostic reasons, and exception boundaries."""

    def test_no_slots_available_diagnostics(self):
        """When calendar has zero free slots, all tasks are marked unplaced with diagnostic explanation."""
        task = make_task("Any Task", duration=30)
        plan = optimize_schedule(tasks=[task], available_slots=[])

        assert len(plan.placed_tasks) == 0
        assert len(plan.unplaced_tasks) == 1
        unplaced = plan.unplaced_tasks[0]
        assert "No free time slots available" in unplaced.reason
        assert unplaced.task.title == "Any Task"

    def test_duration_exceeds_available_slots_diagnostics(self):
        """When task duration exceeds all free slots, unplaced reason reports duration insufficiency."""
        small_slot = TimeSlot(
            start=datetime(2026, 11, 10, 10, 0, tzinfo=TZ),
            end=datetime(2026, 11, 10, 10, 30, tzinfo=TZ),
            duration_minutes=30,
        )
        large_task = make_task("Big Marathon", duration=120)
        plan = optimize_schedule(tasks=[large_task], available_slots=[small_slot])

        assert len(plan.placed_tasks) == 0
        assert len(plan.unplaced_tasks) == 1
        assert "No available free slot with sufficient duration" in plan.unplaced_tasks[0].reason

    def test_deadline_exceeded_diagnostics(self):
        """When slots exist but are past task's explicit deadline, diagnostic states deadline violation."""
        slot = TimeSlot(
            start=datetime(2026, 11, 10, 14, 0, tzinfo=TZ),
            end=datetime(2026, 11, 10, 17, 0, tzinfo=TZ),
            duration_minutes=180,
        )
        task_with_deadline = make_task(
            "Morning Submission",
            duration=45,
            end_anchor="2026-11-10T12:00",  # Deadline is before slot start (14:00)
        )
        plan = optimize_schedule(tasks=[task_with_deadline], available_slots=[slot])

        assert len(plan.placed_tasks) == 0
        assert len(plan.unplaced_tasks) == 1
        assert "Cannot schedule before deadline" in plan.unplaced_tasks[0].reason
        assert "2026-11-10 12:00" in plan.unplaced_tasks[0].reason

    def test_fixed_task_overlap_diagnostics(self):
        """When a fixed task overlaps existing calendar commitments, diagnostic reports overlap."""
        slot = TimeSlot(
            start=datetime(2026, 11, 10, 14, 0, tzinfo=TZ),
            end=datetime(2026, 11, 10, 15, 0, tzinfo=TZ),
            duration_minutes=60,
        )
        # Fixed task asks for 16:00 -> 17:00, which is outside the available slot
        task_fixed = make_task(
            "Doctor Appointment",
            duration=60,
            is_movable=False,
            start_anchor="2026-11-10T16:00",
        )
        plan = optimize_schedule(tasks=[task_fixed], available_slots=[slot])

        assert len(plan.placed_tasks) == 0
        assert len(plan.unplaced_tasks) == 1
        assert "overlaps existing calendar event or falls outside free slots" in plan.unplaced_tasks[0].reason

    def test_apply_plan_missing_client_raises_runtime_error(self):
        """Calling apply_plan live without a calendar client configured raises RuntimeError."""
        scheduler = AutonomousScheduler(calendar_client=None)
        task = make_task("Unsynced Task", duration=30)
        start = datetime(2026, 11, 10, 10, 0, tzinfo=TZ)
        plan = SchedulePlan(
            placed_tasks=[PlacedTask(task=task, start=start, end=start + timedelta(minutes=30), priority_score=5.0)],
            unplaced_tasks=[],
            remaining_slots=[],
        )

        with pytest.raises(RuntimeError, match="CalendarClient is not configured"):
            scheduler.apply_plan(plan, dry_run=False)

    def test_calendar_auth_error_raised_when_credentials_missing(self, tmp_path):
        """CalendarClient raises CalendarAuthError when credentials file is missing."""
        missing_creds = tmp_path / "nonexistent_credentials.json"
        missing_token = tmp_path / "nonexistent_token.json"

        with pytest.raises(CalendarAuthError, match="Credentials file not found"):
            CalendarClient(credentials_path=missing_creds, token_path=missing_token)

    def test_calendar_error_translations(self):
        """Verifies Google API errors are cleanly translated to domain exceptions."""
        mock_service = MagicMock()
        client = CalendarClient.__new__(CalendarClient)
        client.calendar_id = "primary"
        client.tz = TZ
        client.service = mock_service

        # 1. 500 Server error during list_events translated to CalendarError
        resp_500 = MagicMock(status=500)
        mock_service.events().list.side_effect = HttpError(resp=resp_500, content=b"Server error")
        with pytest.raises(CalendarError, match="An error occurred while listing events"):
            client.list_events(datetime.now(TZ), datetime.now(TZ) + timedelta(hours=1))

        # 2. 404 Not Found during delete translated to CalendarResourceNotFoundError
        resp_404 = MagicMock(status=404)
        mock_service.events().delete.side_effect = HttpError(resp=resp_404, content=b"Not found")
        with pytest.raises(CalendarResourceNotFoundError, match="Event with ID missing_id not found"):
            client.delete_event("missing_id")

    def test_preference_profile_domain_validation(self):
        """Verifies boundary errors on preference profiles and weight sanitization."""
        profile = PreferenceProfile(profile_name="test_profile", hourly_weights=[0.5] * 24)

        # Hour must be in [0, 23]
        with pytest.raises(ValueError, match="Hour must be between 0 and 23"):
            profile.get_weight(-1)
        with pytest.raises(ValueError, match="Hour must be between 0 and 23"):
            profile.get_weight(24)

        # Hourly weights vector must have length exactly 24
        with pytest.raises(ValueError, match="Hourly weights must have exactly 24 elements"):
            sanitize_weights([0.5] * 12)

    def test_schedule_diff_formatting_output(self):
        """Verifies human-readable diff formatting handles placed, unplaced, and empty slots cleanly."""
        start = datetime(2026, 11, 10, 10, 0, tzinfo=TZ)
        end = datetime(2026, 11, 10, 11, 0, tzinfo=TZ)
        task = make_task("Placed Task", duration=60)
        unplaced_task = make_task("Unplaced Task", duration=90)

        plan = SchedulePlan(
            placed_tasks=[PlacedTask(task=task, start=start, end=end, priority_score=8.5)],
            unplaced_tasks=[UnplacedTask(task=unplaced_task, reason="No slot large enough", priority_score=4.0)],
            remaining_slots=[TimeSlot(start=end, end=end + timedelta(hours=1), duration_minutes=60)],
        )

        diff = format_schedule_diff(plan)
        assert "[ PLACED TASKS ]" in diff
        assert "Placed Task" in diff
        assert "8.50" in diff
        assert "[ UNPLACED TASKS ]" in diff
        assert "Unplaced Task" in diff
        assert "No slot large enough" in diff
        assert "[ REMAINING AVAILABLE SLOTS ]" in diff
        assert "60 mins" in diff
