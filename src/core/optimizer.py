import datetime as dt
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from src.core.models import (
    ScoredTask,
    TaskDraft,
    TimeSlot,
    PlacedTask,
    UnplacedTask,
    SchedulePlan,
    PreferenceProfile,
)
from src.core.scoring import ScoringWeights, calculate_priority_score


def _parse_anchor(anchor_str: Optional[str], tz: Optional[ZoneInfo] = None) -> Optional[datetime]:
    """Parses ISO datetime string and aligns timezone if naive."""
    if not anchor_str:
        return None
    try:
        dt = datetime.fromisoformat(anchor_str)
        if dt.tzinfo is None and tz is not None:
            return dt.replace(tzinfo=tz)
        return dt
    except (ValueError, TypeError):
        return None


def optimize_schedule(
    tasks: List[Union[ScoredTask, TaskDraft]],
    available_slots: List[TimeSlot],
    weights: ScoringWeights = ScoringWeights(),
    task_buffer_minutes: int = 5,
    min_slot_duration: int = 15,
    profiles: Optional[Dict[str, PreferenceProfile]] = None,
) -> SchedulePlan:
    """
    Deterministically fits prioritized tasks into available free time slots
    guided by 24-hour preference willingness curves and proximity anchors.

    1. Separates fixed (pinned) vs movable tasks.
    2. Places fixed commitments and carves out surrounding free slots.
    3. Sorts movable tasks by priority score descending (with deadline tie-breakers).
    4. Evaluates candidate placement hours inside free slots using preference willingness W[h].
    5. Deducts inter-task buffer and tracks unplaced tasks with diagnostic reasons.
    """
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

    # Detect reference timezone from the first available slot
    ref_tz = available_slots[0].start.tzinfo if available_slots else None
    ref_start = available_slots[0].start if available_slots else None

    # Working copy of available slots sorted chronologically
    working_slots: List[TimeSlot] = sorted(available_slots, key=lambda s: s.start)

    placed_tasks: List[PlacedTask] = []
    unplaced_tasks: List[UnplacedTask] = []

    # Helper to extract task attributes uniformly
    def get_task_attrs(task: Union[ScoredTask, TaskDraft]):
        if isinstance(task, ScoredTask):
            duration = task.estimated_duration
            is_movable = task.is_movable
            start_str = task.start.dateTime if task.start else None
            end_str = task.end.dateTime if task.end else None
        else:
            duration = getattr(task, "estimated_duration", 30) or 30
            is_movable = getattr(task, "is_movable", True)
            start_str = None
            end_str = None

        start_dt = _parse_anchor(start_str, ref_tz)
        deadline_dt = _parse_anchor(end_str, ref_tz)
        score = calculate_priority_score(task.importance, task.urgency, weights)
        return duration, is_movable, start_dt, deadline_dt, score

    # Partition tasks into fixed (immovable) vs movable
    fixed_tasks = []
    movable_tasks = []

    for task in tasks:
        dur, movable, start_dt, deadline_dt, score = get_task_attrs(task)
        is_all_day_anchor = (start_dt is not None and start_dt.time() == dt.time(0, 0))

        if not movable and start_dt is not None and not is_all_day_anchor:
            fixed_tasks.append((task, dur, start_dt, score))
        else:
            if is_all_day_anchor:
                min_start = start_dt
                pref_anchor = None
                if deadline_dt is None or deadline_dt.time() != dt.time(23, 59, 59):
                    deadline_dt = datetime.combine(start_dt.date(), dt.time(23, 59, 59), tzinfo=ref_tz)
            else:
                min_start = None
                pref_anchor = start_dt

            movable_tasks.append((task, dur, pref_anchor, deadline_dt, score, min_start))

    buffer_delta = timedelta(minutes=task_buffer_minutes)

    # -----------------------------------------------------------------------
    # Step 1: Place Fixed / Immovable Tasks (Highest Priority First)
    # -----------------------------------------------------------------------
    # Sort fixed tasks primarily by priority score descending (-t[3]), then start time (t[2])
    # This ensures high-priority fixed commitments are evaluated and placed first,
    # preventing lower-priority tasks from occupying constrained slots.
    fixed_tasks.sort(key=lambda t: (-t[3], t[2]))

    for task, duration_m, fixed_start, score in fixed_tasks:
        fixed_end = fixed_start + timedelta(minutes=duration_m)
        slot_idx_found = -1

        for i, slot in enumerate(working_slots):
            if slot.start <= fixed_start and slot.end >= fixed_end:
                slot_idx_found = i
                break

        if slot_idx_found != -1:
            target_slot = working_slots.pop(slot_idx_found)
            placed_tasks.append(
                PlacedTask(
                    task=task,
                    start=fixed_start,
                    end=fixed_end,
                    priority_score=score,
                )
            )

            # Carve out prefix (before fixed start) and suffix (after fixed end + buffer)
            new_fragments = []
            prefix_mins = int((fixed_start - target_slot.start).total_seconds() // 60)
            if prefix_mins >= min_slot_duration:
                new_fragments.append(
                    TimeSlot(start=target_slot.start, end=fixed_start, duration_minutes=prefix_mins)
                )

            # If another fixed task is scheduled adjacent to or within buffer of this fixed_end,
            # do not deduct buffer between them so consecutive fixed blocks don't collide.
            has_adjacent_fixed = any(
                f_s >= fixed_end and f_s < fixed_end + buffer_delta for _, _, f_s, _ in fixed_tasks
            )
            eff_buffer = timedelta(0) if has_adjacent_fixed else buffer_delta
            suffix_start = fixed_end + eff_buffer
            if suffix_start < target_slot.end:
                suffix_mins = int((target_slot.end - suffix_start).total_seconds() // 60)
                if suffix_mins >= min_slot_duration:
                    new_fragments.append(
                        TimeSlot(start=suffix_start, end=target_slot.end, duration_minutes=suffix_mins)
                    )

            working_slots.extend(new_fragments)
            working_slots.sort(key=lambda s: s.start)
        else:
            unplaced_tasks.append(
                UnplacedTask(
                    task=task,
                    reason=f"Fixed window {fixed_start.strftime('%Y-%m-%d %H:%M')} overlaps existing calendar event or falls outside free slots",
                    priority_score=score,
                )
            )

    # -----------------------------------------------------------------------
    # Step 2: Sort Movable Tasks by Priority & Deadlines
    # -----------------------------------------------------------------------
    def task_sort_key(item):
        # item: (task, duration_m, preferred_anchor, deadline_dt, score, min_start)
        task, duration_m, preferred_anchor, deadline_dt, score, min_start = item
        # 1. Higher score first (-score)
        # 2. Earlier deadline first (or distant future if None)
        deadline_timestamp = deadline_dt.timestamp() if deadline_dt else float("inf")
        # 3. Shorter duration first
        return (-score, deadline_timestamp, duration_m)

    movable_tasks.sort(key=task_sort_key)

    # -----------------------------------------------------------------------
    # Step 3: Gradient-Aware Slot Allocation
    # -----------------------------------------------------------------------
    for task, duration_m, preferred_anchor, deadline_dt, score, min_start in movable_tasks:
        task_duration = timedelta(minutes=duration_m)
        placed = False

        # Match preference profile if provided
        profile = None
        if profiles:
            profile = profiles.get(task.title)

        candidates = []
        for i, slot in enumerate(working_slots):
            if slot.duration_minutes < duration_m:
                continue

            possible_starts = [slot.start]

            # Preferred anchor time if it fits inside this slot
            if preferred_anchor and slot.start <= preferred_anchor:
                anchor_end = preferred_anchor + task_duration
                if anchor_end <= slot.end:
                    possible_starts.append(preferred_anchor)

            # Hour boundaries if profile has non-zero weights
            if profile and any(w > 0.0 for w in profile.hourly_weights):
                curr = slot.start.replace(minute=0, second=0, microsecond=0)
                if curr < slot.start:
                    curr += timedelta(hours=1)
                while curr + task_duration <= slot.end:
                    if curr >= slot.start:
                        possible_starts.append(curr)
                    curr += timedelta(hours=1)

            # Deduplicate possible starts
            seen_starts = set()
            unique_starts = []
            for st in possible_starts:
                if st not in seen_starts:
                    seen_starts.add(st)
                    unique_starts.append(st)

            for cand_start in unique_starts:
                cand_end = cand_start + task_duration
                if min_start and cand_start < min_start:
                    continue
                if deadline_dt and cand_end > deadline_dt:
                    continue
                if cand_end > slot.end:
                    continue

                # Willingness weight for this hour
                h = cand_start.hour
                raw_w = profile.get_weight(h) if profile else 0.0
                willingness = raw_w if raw_w > 0.0 else 0.5

                # Proximity to preferred anchor
                proximity = 1.0
                if preferred_anchor:
                    hours_diff = abs((cand_start - preferred_anchor).total_seconds()) / 3600.0
                    proximity = max(0.1, 1.0 - 0.05 * hours_diff)

                # Quality score with slight chronological tie-breaker
                time_offset_hrs = (cand_start - ref_start).total_seconds() / 3600.0 if ref_start else 0.0
                tie_breaker = 1e-4 / (1.0 + max(0.0, time_offset_hrs))
                quality = (willingness * proximity) + tie_breaker

                candidates.append((quality, i, cand_start, cand_end))

        if candidates:
            # Sort by quality score descending
            candidates.sort(key=lambda c: c[0], reverse=True)
            best_quality, slot_idx, best_start, best_end = candidates[0]

            placed_tasks.append(
                PlacedTask(
                    task=task,
                    start=best_start,
                    end=best_end,
                    priority_score=score,
                )
            )

            # Carve slot into prefix and suffix around placed task
            orig_slot = working_slots.pop(slot_idx)
            new_fragments = []

            prefix_end = best_start
            prefix_mins = int((prefix_end - orig_slot.start).total_seconds() // 60)
            if prefix_mins >= min_slot_duration:
                new_fragments.append(
                    TimeSlot(start=orig_slot.start, end=prefix_end, duration_minutes=prefix_mins)
                )

            suffix_start = best_end + buffer_delta
            if suffix_start < orig_slot.end:
                suffix_mins = int((orig_slot.end - suffix_start).total_seconds() // 60)
                if suffix_mins >= min_slot_duration:
                    new_fragments.append(
                        TimeSlot(start=suffix_start, end=orig_slot.end, duration_minutes=suffix_mins)
                    )

            working_slots.extend(new_fragments)
            working_slots.sort(key=lambda s: s.start)
            placed = True

        if not placed:
            reason = "No available free slot with sufficient duration"
            if deadline_dt:
                could_fit_without_deadline = any(
                    s.duration_minutes >= duration_m for s in working_slots
                )
                all_slots_after_deadline = bool(working_slots) and all(
                    s.start >= deadline_dt for s in working_slots
                )
                if could_fit_without_deadline or all_slots_after_deadline:
                    reason = f"Cannot schedule before deadline {deadline_dt.strftime('%Y-%m-%d %H:%M')}"
            unplaced_tasks.append(
                UnplacedTask(
                    task=task,
                    reason=reason,
                    priority_score=score,
                )
            )

    # Sort placed tasks chronologically for clean presentation
    placed_tasks.sort(key=lambda p: p.start)

    return SchedulePlan(
        placed_tasks=placed_tasks,
        unplaced_tasks=unplaced_tasks,
        remaining_slots=working_slots,
    )
