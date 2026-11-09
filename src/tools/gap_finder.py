from datetime import datetime, timedelta
from operator import attrgetter

from src.core.models import CalendarEvent, TimeSlot


def find_free_slots(
    events: list[CalendarEvent],
    start: datetime,
    end: datetime,
    duration_min: int = 15,
    buffer_minutes: int = 0,
) -> list[TimeSlot]:
   
    if start >= end:
        return []

    buffer = timedelta(minutes=buffer_minutes)

    busy_intervals: list[list[datetime]] = []
    
    sorted_events = sorted(events, key=attrgetter("start"))

    for ev in sorted_events:
        # Simple if/else: ignore all-day events (e.g. birthdays, holidays) when finding free slots
        if getattr(ev, "is_all_day", False):
            continue
        else:
            # Ignore events completely outside the window
            if ev.end <= start or ev.start >= end:
                continue

            # Clip event boundaries to the window
            # Add buffer before and after the event
            busy_start = max(ev.start - buffer, start)
            busy_end = min(ev.end + buffer, end)

            busy_intervals.append([busy_start, busy_end])

    # 2. Merge overlapping or adjacent busy intervals
    merged_busy: list[list[datetime]] = []
    for current in busy_intervals:
        if not merged_busy or current[0] > merged_busy[-1][1]:
            # No overlap with previous block: add new block
            merged_busy.append(current)
        else:
            # Overlaps or touches previous block: extend its end time
            merged_busy[-1][1] = max(merged_busy[-1][1], current[1])

    # 3. Walk the timeline with a cursor from `start` to `end` to find open gaps
    slots: list[TimeSlot] = []
    cursor = start

    for busy_start, busy_end in merged_busy:
        gap_seconds = (busy_start - cursor).total_seconds()
        gap_minutes = int(gap_seconds // 60)

        if gap_minutes >= duration_min:
            slots.append(
                TimeSlot(
                    start=cursor,
                    end=busy_start,
                    duration_minutes=gap_minutes,
                )
            )

        # Advance cursor past the busy block
        cursor = max(cursor, busy_end)

    # 4. Final gap: between the last busy block and the end of the window
    final_gap_seconds = (end - cursor).total_seconds()
    final_gap_minutes = int(final_gap_seconds // 60)

    if final_gap_minutes >= duration_min:
        slots.append(
            TimeSlot(
                start=cursor,
                end=end,
                duration_minutes=final_gap_minutes,
            )
        )

    return slots
