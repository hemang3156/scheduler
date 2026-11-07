from datetime import datetime
import pytest

from src.core.models import CalendarEvent, TimeSlot
from src.tools.gap_finder import find_free_slots


def test_empty_calendar_returns_full_window(window: tuple[datetime, datetime]):
    start, end = window
    slots = find_free_slots(events=[], start=start, end=end)

    assert len(slots) == 1
    assert slots[0].start == start
    assert slots[0].end == end
    assert slots[0].duration_minutes == 480


def test_fully_booked_returns_no_slots(
    window: tuple[datetime, datetime], make_event
):
    start, end = window
    event = make_event("Full Day Meeting", 9, 0, 17, 0)
    slots = find_free_slots(events=[event], start=start, end=end)

    assert slots == []


def test_single_event_middle_splits_window(
    window: tuple[datetime, datetime], make_event
):
    start, end = window
    event = make_event("Lunch", 12, 0, 13, 0)
    slots = find_free_slots(events=[event], start=start, end=end)

    assert len(slots) == 2

    # Morning slot
    assert slots[0].start == start
    assert slots[0].end == event.start
    assert slots[0].duration_minutes == 180  # 9:00 to 12:00 = 3 hours

    # Afternoon slot
    assert slots[1].start == event.end
    assert slots[1].end == end
    assert slots[1].duration_minutes == 240  # 13:00 to 17:00 = 4 hours


def test_event_at_start_boundary(
    window: tuple[datetime, datetime], make_event
):
    start, end = window
    event = make_event("Morning Standup", 9, 0, 10, 30)
    slots = find_free_slots(events=[event], start=start, end=end)

    assert len(slots) == 1
    assert slots[0].start == event.end
    assert slots[0].end == end
    assert slots[0].duration_minutes == 390  # 10:30 to 17:00


def test_event_at_end_boundary(
    window: tuple[datetime, datetime], make_event
):
    start, end = window
    event = make_event("Wrap-up Sync", 15, 0, 17, 0)
    slots = find_free_slots(events=[event], start=start, end=end)

    assert len(slots) == 1
    assert slots[0].start == start
    assert slots[0].end == event.start
    assert slots[0].duration_minutes == 360  # 9:00 to 15:00


def test_overlapping_events_merged(
    window: tuple[datetime, datetime], make_event
):
    start, end = window
    ev1 = make_event("Review Part 1", 10, 0, 11, 30, event_id="1")
    ev2 = make_event("Review Part 2", 11, 0, 12, 30, event_id="2")

    slots = find_free_slots(events=[ev1, ev2], start=start, end=end)

    # 10:00-11:30 and 11:00-12:30 merge into a single busy block 10:00-12:30
    assert len(slots) == 2
    assert slots[0].start == start
    assert slots[0].end == ev1.start  # 09:00 to 10:00
    assert slots[0].duration_minutes == 60

    assert slots[1].start == ev2.end  # 12:30 to 17:00
    assert slots[1].end == end
    assert slots[1].duration_minutes == 270


def test_nested_events_handled(
    window: tuple[datetime, datetime], make_event
):
    start, end = window
    ev_outer = make_event("Long Workshop", 10, 0, 14, 0, event_id="1")
    ev_inner = make_event("Quick Interruption", 11, 0, 12, 0, event_id="2")

    slots = find_free_slots(events=[ev_outer, ev_inner], start=start, end=end)

    # ev_inner is entirely swallowed by ev_outer
    assert len(slots) == 2
    assert slots[0].start == start
    assert slots[0].end == ev_outer.start
    assert slots[0].duration_minutes == 60  # 09:00 to 10:00

    assert slots[1].start == ev_outer.end
    assert slots[1].end == end
    assert slots[1].duration_minutes == 180  # 14:00 to 17:00


def test_events_outside_window_ignored(
    window: tuple[datetime, datetime], make_event
):
    start, end = window
    early_event = make_event("Gym Session", 6, 0, 8, 30, event_id="1")
    late_event = make_event("Dinner", 18, 0, 20, 0, event_id="2")

    slots = find_free_slots(events=[early_event, late_event], start=start, end=end)

    # Both events are strictly outside 09:00-17:00
    assert len(slots) == 1
    assert slots[0].start == start
    assert slots[0].end == end
    assert slots[0].duration_minutes == 480


def test_events_crossing_window_boundary_clipped(
    window: tuple[datetime, datetime], make_event
):
    start, end = window
    # Starts before 09:00, ends at 10:00 inside the window
    cross_start = make_event("Early Shift", 8, 0, 10, 0, event_id="1")
    # Starts at 16:00 inside the window, ends after 17:00
    cross_end = make_event("Late Shift", 16, 0, 18, 0, event_id="2")

    slots = find_free_slots(events=[cross_start, cross_end], start=start, end=end)

    # Busy blocks: [09:00-10:00] and [16:00-17:00]
    # Free slot should only be 10:00 to 16:00
    assert len(slots) == 1
    assert slots[0].start == cross_start.end  # 10:00
    assert slots[0].end == cross_end.start    # 16:00
    assert slots[0].duration_minutes == 360   # 6 hours


def test_buffer_minutes_applied(
    window: tuple[datetime, datetime], make_event
):
    start, end = window
    event = make_event("Meeting", 12, 0, 13, 0)
    # Apply 15-minute buffer on both sides: busy becomes 11:45 to 13:15
    slots = find_free_slots(events=[event], start=start, end=end, buffer_minutes=15)

    assert len(slots) == 2
    assert slots[0].duration_minutes == 165  # 09:00 to 11:45 (2h 45m)
    assert slots[1].duration_minutes == 225  # 13:15 to 17:00 (3h 45m)


@pytest.mark.parametrize(
    "duration_min,expected_slot_count",
    [
        (15, 1),  # 20 min gap >= 15 min threshold -> kept
        (20, 1),  # 20 min gap >= 20 min threshold -> kept
        (25, 0),  # 20 min gap < 25 min threshold -> dropped
        (30, 0),  # 20 min gap < 30 min threshold -> dropped
    ],
)
def test_duration_min_filtering(
    window: tuple[datetime, datetime],
    make_event,
    duration_min: int,
    expected_slot_count: int,
):
    start, end = window
    # Event 1: 09:00 - 12:00
    # Event 2: 12:20 - 17:00
    # The only gap is 12:00 to 12:20 (20 minutes)
    ev1 = make_event("Block A", 9, 0, 12, 0, event_id="1")
    ev2 = make_event("Block B", 12, 20, 17, 0, event_id="2")

    slots = find_free_slots(
        events=[ev1, ev2],
        start=start,
        end=end,
        duration_min=duration_min,
    )
    assert len(slots) == expected_slot_count


def test_invalid_window_returns_empty(base_day: datetime):
    # Window where start >= end
    start = base_day.replace(hour=17, minute=0)
    end = base_day.replace(hour=9, minute=0)

    slots = find_free_slots(events=[], start=start, end=end)
    assert slots == []
