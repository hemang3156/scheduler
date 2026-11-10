from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from src.core.models import (
    PlacedTask,
    PreferenceProfile,
    SchedulePlan,
    ScoredTask,
    TaskDraft,
    TimeSlot,
    UnplacedTask,
)
from src.core.scoring import ScoringWeights, calculate_priority_score


def _parse_anchor(anchor_str: Optional[str], tz: Optional[ZoneInfo] = None) -> Optional[datetime]:
    if not anchor_str:
        return None
    try:
        dt = datetime.fromisoformat(anchor_str)
        if tz is not None:
            return dt.replace(tzinfo=tz) if dt.tzinfo is None else dt.astimezone(tz)
        if dt.tzinfo is not None:
            return dt.replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True)
class _TaskInfo:
    task: Union[ScoredTask, TaskDraft]
    duration: int
    is_movable: bool
    start_anchor: Optional[datetime]
    deadline: Optional[datetime]
    score: float


def _carve_slot(
    slot: TimeSlot,
    busy_start: datetime,
    busy_end: datetime,
    buffer: timedelta,
    min_duration: int,
) -> List[TimeSlot]:
    fragments: List[TimeSlot] = []
    prefix_mins = int((busy_start - slot.start).total_seconds() // 60)
    if prefix_mins >= min_duration:
        fragments.append(TimeSlot(start=slot.start, end=busy_start, duration_minutes=prefix_mins))

    suffix_start = busy_end + buffer
    if suffix_start < slot.end:
        suffix_mins = int((slot.end - suffix_start).total_seconds() // 60)
        if suffix_mins >= min_duration:
            fragments.append(TimeSlot(start=suffix_start, end=slot.end, duration_minutes=suffix_mins))
    return fragments


def _candidate_starts(
    slot: TimeSlot,
    duration: timedelta,
    anchor: Optional[datetime],
    profile: Optional[PreferenceProfile],
) -> List[datetime]:
    starts = [slot.start]
    if anchor and slot.start <= anchor and (anchor + duration) <= slot.end:
        starts.append(anchor)

    if profile and any(w > 0.0 for w in profile.hourly_weights):
        curr = slot.start.replace(minute=0, second=0, microsecond=0)
        if curr < slot.start:
            curr += timedelta(hours=1)
        while curr + duration <= slot.end:
            starts.append(curr)
            curr += timedelta(hours=1)

    return list(dict.fromkeys(starts))


def _score_candidate(
    cand_start: datetime,
    anchor: Optional[datetime],
    profile: Optional[PreferenceProfile],
    ref_start: datetime,
) -> float:
    raw_w = profile.get_weight(cand_start.hour) if profile else 0.0
    willingness = raw_w if raw_w > 0.0 else 0.5

    proximity = 1.0
    if anchor:
        hours_diff = abs((cand_start - anchor).total_seconds()) / 3600.0
        proximity = max(0.1, 1.0 - 0.05 * hours_diff)

    time_offset_hrs = max(0.0, (cand_start - ref_start).total_seconds() / 3600.0)
    tie_breaker = 1e-4 / (1.0 + time_offset_hrs)
    return (willingness * proximity) + tie_breaker


def optimize_schedule(
    tasks: List[Union[ScoredTask, TaskDraft]],
    available_slots: List[TimeSlot],
    weights: ScoringWeights = ScoringWeights(),
    task_buffer_minutes: int = 5,
    min_slot_duration: int = 15,
    profiles: Optional[Dict[str, PreferenceProfile]] = None,
) -> SchedulePlan:
    """Fits prioritized tasks into free slots using preference curves and deadline constraints."""
    if not available_slots:
        return SchedulePlan(
            placed_tasks=[],
            unplaced_tasks=[
                UnplacedTask(
                    task=t,
                    reason="No free time slots available in the calendar window",
                    priority_score=calculate_priority_score(t.importance, t.urgency, weights),
                )
                for t in tasks
            ],
            remaining_slots=[],
        )

    ref_tz = available_slots[0].start.tzinfo
    ref_start = available_slots[0].start
    working_slots: List[TimeSlot] = sorted(available_slots, key=lambda s: s.start)
    buffer_delta = timedelta(minutes=task_buffer_minutes)

    fixed_tasks: List[_TaskInfo] = []
    movable_tasks: List[_TaskInfo] = []

    for t in tasks:
        if isinstance(t, ScoredTask):
            duration = t.estimated_duration
            is_movable = t.is_movable
            s_str = t.start.dateTime if t.start else None
            e_str = t.end.dateTime if t.end else None
        else:
            duration = getattr(t, "estimated_duration", 30) or 30
            is_movable = getattr(t, "is_movable", True)
            s_str, e_str = None, None

        info = _TaskInfo(
            task=t,
            duration=duration,
            is_movable=is_movable,
            start_anchor=_parse_anchor(s_str, ref_tz),
            deadline=_parse_anchor(e_str, ref_tz),
            score=calculate_priority_score(t.importance, t.urgency, weights),
        )
        if not info.is_movable and info.start_anchor is not None:
            fixed_tasks.append(info)
        else:
            movable_tasks.append(info)

    placed_tasks: List[PlacedTask] = []
    unplaced_tasks: List[UnplacedTask] = []

    fixed_tasks.sort(key=lambda t: (-t.score, t.start_anchor.timestamp() if t.start_anchor else 0.0))

    for t in fixed_tasks:
        start_dt = t.start_anchor
        assert start_dt is not None
        end_dt = start_dt + timedelta(minutes=t.duration)

        slot_idx = next(
            (i for i, s in enumerate(working_slots) if s.start <= start_dt and s.end >= end_dt),
            None,
        )

        if slot_idx is not None:
            target_slot = working_slots.pop(slot_idx)
            placed_tasks.append(PlacedTask(task=t.task, start=start_dt, end=end_dt, priority_score=t.score))

            has_adjacent_fixed = any(
                f is not t
                and f.start_anchor is not None
                and end_dt <= f.start_anchor < end_dt + buffer_delta
                for f in fixed_tasks
            )
            eff_buffer = timedelta(0) if has_adjacent_fixed else buffer_delta
            working_slots.extend(_carve_slot(target_slot, start_dt, end_dt, eff_buffer, min_slot_duration))
            working_slots.sort(key=lambda s: s.start)
        else:
            unplaced_tasks.append(
                UnplacedTask(
                    task=t.task,
                    reason=f"Fixed window {start_dt.strftime('%Y-%m-%d %H:%M')} overlaps existing calendar event or falls outside free slots",
                    priority_score=t.score,
                )
            )

    movable_tasks.sort(
        key=lambda t: (-t.score, t.deadline.timestamp() if t.deadline else float("inf"), t.duration)
    )

    for t in movable_tasks:
        task_duration = timedelta(minutes=t.duration)
        profile = profiles.get(t.task.title) if profiles else None

        candidates = []
        for i, slot in enumerate(working_slots):
            if slot.duration_minutes < t.duration:
                continue
            for cand_start in _candidate_starts(slot, task_duration, t.start_anchor, profile):
                cand_end = cand_start + task_duration
                if (t.deadline and cand_end > t.deadline) or cand_end > slot.end:
                    continue
                q = _score_candidate(cand_start, t.start_anchor, profile, ref_start)
                candidates.append((q, i, cand_start, cand_end))

        if candidates:
            candidates.sort(key=lambda c: c[0], reverse=True)
            _, slot_idx, best_start, best_end = candidates[0]

            placed_tasks.append(PlacedTask(task=t.task, start=best_start, end=best_end, priority_score=t.score))
            orig_slot = working_slots.pop(slot_idx)
            working_slots.extend(_carve_slot(orig_slot, best_start, best_end, buffer_delta, min_slot_duration))
            working_slots.sort(key=lambda s: s.start)
        else:
            reason = "No available free slot with sufficient duration"
            if t.deadline:
                could_fit = any(s.duration_minutes >= t.duration for s in working_slots)
                all_after = bool(working_slots) and all(s.start >= t.deadline for s in working_slots)
                if could_fit or all_after:
                    reason = f"Cannot schedule before deadline {t.deadline.strftime('%Y-%m-%d %H:%M')}"
            unplaced_tasks.append(UnplacedTask(task=t.task, reason=reason, priority_score=t.score))

    placed_tasks.sort(key=lambda p: p.start)
    return SchedulePlan(
        placed_tasks=placed_tasks,
        unplaced_tasks=unplaced_tasks,
        remaining_slots=working_slots,
    )
