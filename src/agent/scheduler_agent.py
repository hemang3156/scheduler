import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, time
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


def format_schedule_diff(
    plan: SchedulePlan,
    use_12hr: bool = True,
    existing_events: Optional[List[CalendarEvent]] = None,
) -> str:
    """
    Renders a clear, deterministic ASCII diff table of the proposed schedule.
    Follows human-in-the-loop safety principles.
    """
    lines = []
    lines.append("=" * 80)
    lines.append("                        PROPOSED SCHEDULE PLAN")
    lines.append("=" * 80)

    # 1. Schedule Plan (Unified timeline when calendar events exist, or placed tasks)
    events_to_show = existing_events if existing_events is not None else getattr(plan, "existing_events", [])
    if events_to_show:
        lines.append("\n[ SCHEDULE PLAN (CALENDAR & PLACED TASKS) ]")
        timeline = []
        for ev in events_to_show:
            duration_m = max(0, int((ev.end - ev.start).total_seconds() // 60))
            timeline.append({
                "start": ev.start,
                "end": ev.end,
                "duration": duration_m,
                "type": "Calendar",
                "score": "--",
                "title": ev.summary,
            })
        for p in plan.placed_tasks:
            timeline.append({
                "start": p.start,
                "end": p.end,
                "duration": dur(p),
                "type": "Placed",
                "score": f"{p.priority_score:.2f}",
                "title": p.task.title,
            })
        timeline.sort(key=lambda item: (item["start"], 0 if item["type"] == "Calendar" else 1))

        if not timeline:
            lines.append("  (No tasks or calendar events in this window)")
        else:
            if use_12hr:
                header = f"  {'Start':<21} {'End':<11} {'Dur':<6} {'Type':<10} {'Score':<7} {'Title'}"
            else:
                header = f"  {'Start':<16} {'End':<16} {'Dur':<6} {'Type':<10} {'Score':<7} {'Title'}"
            lines.append(header)
            lines.append("  " + "-" * 76)
            for item in timeline:
                dur_str = f"{item['duration']}m"
                if use_12hr:
                    s_str = item["start"].strftime("%Y-%m-%d %I:%M %p")
                    e_str = item["end"].strftime("%I:%M %p")
                    lines.append(
                        f"  {s_str:<21} {e_str:<11} {dur_str:<6} {item['type']:<10} {item['score']:<7} {item['title']}"
                    )
                else:
                    s_str = item["start"].strftime("%Y-%m-%d %H:%M")
                    e_str = item["end"].strftime("%H:%M")
                    lines.append(
                        f"  {s_str:<16} {e_str:<16} {dur_str:<6} {item['type']:<10} {item['score']:<7} {item['title']}"
                    )
    else:
        lines.append("\n[ PLACED TASKS ]")
        if not plan.placed_tasks:
            lines.append("  (No tasks could be placed)")
        else:
            if use_12hr:
                header = f"  {'Start':<21} {'End':<11} {'Dur':<6} {'Score':<6} {'Task Title'}"
            else:
                header = f"  {'Start':<16} {'End':<16} {'Dur':<6} {'Score':<6} {'Task Title'}"
            lines.append(header)
            lines.append("  " + "-" * 76)
            for p in plan.placed_tasks:
                if use_12hr:
                    s_str = p.start.strftime("%Y-%m-%d %I:%M %p")
                    e_str = p.end.strftime("%I:%M %p")
                    lines.append(
                        f"  {s_str:<21} {e_str:<11} {f'{dur(p)}m':<6} {p.priority_score:<6.2f} {p.task.title}"
                    )
                else:
                    s_str = p.start.strftime("%Y-%m-%d %H:%M")
                    e_str = p.end.strftime("%H:%M")
                    lines.append(
                        f"  {s_str:<16} {e_str:<16} {f'{dur(p)}m':<6} {p.priority_score:<6.2f} {p.task.title}"
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
            if use_12hr:
                s_str = s.start.strftime("%Y-%m-%d %I:%M %p")
                e_str = s.end.strftime("%I:%M %p")
            else:
                s_str = s.start.strftime("%Y-%m-%d %H:%M")
                e_str = s.end.strftime("%H:%M")
            lines.append(f"  - {s_str} -> {e_str} ({s.duration_minutes} mins)")

    lines.append("=" * 80)
    return "\n".join(lines)


def dur(p: PlacedTask) -> int:
    return int((p.end - p.start).total_seconds() // 60)


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
        now: Optional[datetime] = None,
        daily_start_time: Optional[time] = None,
        daily_end_time: Optional[time] = None,
        continuous_window: bool = False,
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

        if now is None:
            now = datetime.now(self.tz).replace(microsecond=0)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=self.tz)

        # 1. Fetch commitments
        if existing_events is None:
            if self.calendar_client is not None:
                existing_events = self.calendar_client.list_events(start_window, end_window)
            else:
                existing_events = []

        # 2. Find free gaps (continuous single window, single-day, or multi-day range)
        if continuous_window or start_window.date() == end_window.date():
            # For current day or continuous window, start time window only begins from now
            effective_start = start_window
            if effective_start < now:
                effective_start = now

            if effective_start >= end_window:
                free_slots = []
            else:
                free_slots = find_free_slots(
                    events=existing_events,
                    start=effective_start,
                    end=end_window,
                    duration_min=min_slot_duration,
                    buffer_minutes=0,
                )
        else:
            # Multi-day range: generate free slots for each day's working window
            free_slots = []
            current_day = start_window.date()
            final_day = end_window.date()
            d_start_time = daily_start_time or start_window.time()
            d_end_time = daily_end_time or end_window.time()

            while current_day <= final_day:
                day_start = datetime.combine(current_day, d_start_time, tzinfo=self.tz)
                if d_start_time < d_end_time:
                    day_end = datetime.combine(current_day, d_end_time, tzinfo=self.tz)
                else:
                    day_end = datetime.combine(current_day + timedelta(days=1), d_end_time, tzinfo=self.tz)

                # Clamp day boundaries to overall window [start_window, end_window]
                if day_start < start_window:
                    day_start = start_window
                if day_end > end_window:
                    day_end = end_window

                # For current day, start time window only begins from now
                if current_day == now.date() and day_start < now:
                    day_start = now

                if day_start < day_end:
                    daily_slots = find_free_slots(
                        events=existing_events,
                        start=day_start,
                        end=day_end,
                        duration_min=min_slot_duration,
                        buffer_minutes=0,
                    )
                    free_slots.extend(daily_slots)
                current_day += timedelta(days=1)

        # Filter out any slots whose end_time is before or at time_now; clip slots crossing now
        valid_slots: List[TimeSlot] = []
        for slot in free_slots:
            if slot.end <= now:
                continue
            if slot.start < now:
                clipped_duration = int((slot.end - now).total_seconds() // 60)
                if clipped_duration >= min_slot_duration:
                    valid_slots.append(
                        TimeSlot(
                            start=now,
                            end=slot.end,
                            duration_minutes=clipped_duration,
                        )
                    )
            else:
                valid_slots.append(slot)

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
            available_slots=valid_slots,
            weights=weights,
            task_buffer_minutes=buffer_minutes,
            min_slot_duration=min_slot_duration,
            profiles=resolved_profiles,
        )

        plan.existing_events = list(existing_events or [])
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
