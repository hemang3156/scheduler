import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Union, Dict
from zoneinfo import ZoneInfo

from src.core.models import (
    CalendarEvent,
    ScoredTask,
    SchedulePlan,
    PlacedTask,
    UnplacedTask,
    TimeSlot,
    PreferenceProfile,
)
from src.core.optimizer import optimize_schedule
from src.core.scoring import ScoringWeights
from src.core.preference_engine import load_profile
from src.tools.calender_client import CalendarClient
from src.tools.gap_finder import find_free_slots
from src.agent.curve_updater import evolve_curve_with_llm

logger = logging.getLogger(__name__)

TIMEZONE = "Asia/Kolkata"


def format_schedule_diff(plan: SchedulePlan) -> str:
    """
    Renders a clear, deterministic ASCII diff table of the proposed schedule.
    Follows human-in-the-loop safety principles.
    """
    lines = []
    lines.append("=" * 80)
    lines.append("                        PROPOSED SCHEDULE PLAN")
    lines.append("=" * 80)

    # 1. Placed Tasks
    lines.append("\n[ PLACED TASKS ]")
    if not plan.placed_tasks:
        lines.append("  (No tasks could be placed)")
    else:
        header = f"  {'Start':<16} {'End':<16} {'Dur':<6} {'Score':<6} {'Task Title'}"
        lines.append(header)
        lines.append("  " + "-" * 76)
        for p in plan.placed_tasks:
            s_str = p.start.strftime("%Y-%m-%d %H:%M")
            e_str = p.end.strftime("%H:%M")
            dur = int((p.end - p.start).total_seconds() // 60)
            lines.append(
                f"  {s_str:<16} {e_str:<16} {f'{dur}m':<6} {p.priority_score:<6.2f} {p.task.title}"
            )

    # 2. Unplaced Tasks
    lines.append("\n[ UNPLACED TASKS ]")
    if not plan.unplaced_tasks:
        lines.append("  (All tasks successfully placed)")
    else:
        header = f"  {'Score':<6} {'Task Title':<30} {'Reason'}"
        lines.append(header)
        lines.append("  " + "-" * 76)
        for u in plan.unplaced_tasks:
            title_trunc = (u.task.title[:27] + "...") if len(u.task.title) > 30 else u.task.title
            lines.append(f"  {u.priority_score:<6.2f} {title_trunc:<30} {u.reason}")

    # 3. Remaining Free Slots
    lines.append("\n[ REMAINING AVAILABLE SLOTS ]")
    if not plan.remaining_slots:
        lines.append("  (Calendar is fully packed - 0 free minutes remaining)")
    else:
        for s in plan.remaining_slots:
            s_str = s.start.strftime("%Y-%m-%d %H:%M")
            e_str = s.end.strftime("%H:%M")
            lines.append(f"  - {s_str} -> {e_str} ({s.duration_minutes} mins)")

    lines.append("=" * 80)
    return "\n".join(lines)


class AutonomousScheduler:
    """
    Coordinates the autonomous scheduling pipeline:
    1. Reads & scores task items via LLM / scorer.
    2. Queries calendar for existing commitments.
    3. Finds available free gaps using gap finder.
    4. Deterministically packs tasks using gradient optimizer & preference curves.
    5. Displays schedule diff for confirmation before syncing.
    6. Evolves 24-hour preference curves via LLM upon sync.
    """

    def __init__(
        self,
        calendar_client: Optional[CalendarClient] = None,
        tz_name: str = TIMEZONE,
    ):
        self.tz = ZoneInfo(tz_name)
        self.calendar_client = calendar_client

    def generate_plan(
        self,
        tasks: List[ScoredTask],
        start_window: datetime,
        end_window: datetime,
        existing_events: Optional[List[CalendarEvent]] = None,
        buffer_minutes: int = 5,
        min_slot_duration: int = 15,
        weights: ScoringWeights = ScoringWeights(),
        profiles: Optional[Dict[str, PreferenceProfile]] = None,
    ) -> SchedulePlan:
        """
        Generates a schedule plan without mutating external state.
        If existing_events is not provided and calendar_client exists, queries the calendar.
        """
        # Ensure window datetimes are timezone-aware
        if start_window.tzinfo is None:
            start_window = start_window.replace(tzinfo=self.tz)
        if end_window.tzinfo is None:
            end_window = end_window.replace(tzinfo=self.tz)

        # 1. Fetch commitments
        if existing_events is None:
            if self.calendar_client is not None:
                existing_events = self.calendar_client.list_events(start_window, end_window)
            else:
                existing_events = []

        # 2. Find free gaps (single-day or multi-day range)
        if start_window.date() == end_window.date():
            free_slots = find_free_slots(
                events=existing_events,
                start=start_window,
                end=end_window,
                duration_min=min_slot_duration,
                buffer_minutes=0,
            )
        else:
            # Multi-day range: generate free slots for each day's working window
            free_slots = []
            current_day = start_window.date()
            final_day = end_window.date()
            start_time = start_window.time()
            end_time = end_window.time()

            while current_day <= final_day:
                day_start = datetime.combine(current_day, start_time, tzinfo=self.tz)
                if start_time < end_time:
                    day_end = datetime.combine(current_day, end_time, tzinfo=self.tz)
                else:
                    day_end = datetime.combine(current_day + timedelta(days=1), end_time, tzinfo=self.tz)

                daily_slots = find_free_slots(
                    events=existing_events,
                    start=day_start,
                    end=day_end,
                    duration_min=min_slot_duration,
                    buffer_minutes=0,
                )
                free_slots.extend(daily_slots)
                current_day += timedelta(days=1)

        # 3. Load or use provided preference profiles
        resolved_profiles = {}
        if profiles is not None:
            resolved_profiles = profiles
        else:
            for t in tasks:
                try:
                    resolved_profiles[t.title] = load_profile(t.title)
                except Exception:
                    pass

        # 4. Optimize placement
        plan = optimize_schedule(
            tasks=tasks,
            available_slots=free_slots,
            weights=weights,
            task_buffer_minutes=buffer_minutes,
            min_slot_duration=min_slot_duration,
            profiles=resolved_profiles,
        )

        return plan

    def apply_plan(
        self,
        plan: SchedulePlan,
        dry_run: bool = False,
        evolve_preferences: bool = True,
    ) -> List[CalendarEvent]:
        """
        Creates calendar events for all placed tasks in Google Calendar.
        If dry_run is True, returns simulated events without network calls.
        If evolve_preferences is True, updates the task preference curve via LLM.
        """
        created = []
        for placed in plan.placed_tasks:
            event = CalendarEvent(
                id=f"dry_run_{placed.task.title}" if dry_run else "",
                summary=placed.task.title,
                start=placed.start,
                end=placed.end,
                is_all_day=False,
            )
            if not dry_run:
                if self.calendar_client is None:
                    raise RuntimeError("Cannot sync plan: CalendarClient is not configured.")
                res = self.calendar_client.create_event(event)
                created.append(res)

                if evolve_preferences:
                    try:
                        profile = load_profile(placed.task.title)
                        ev_type = getattr(placed.task, "event_type", "routine")
                        if hasattr(ev_type, "value"):
                            ev_type = ev_type.value
                        evolve_curve_with_llm(profile, res, event_type=str(ev_type))
                    except Exception as e:
                        logger.warning(f"Could not evolve profile for {placed.task.title}: {e}")
            else:
                created.append(event)
        return created
