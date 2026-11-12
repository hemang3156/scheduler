import datetime as dt
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from src.core.models import (
    CalendarEvent,
    DateTimeAnchor,
    EventType,
    PriorityCategory,
    ScoredTask,
    ScoredTaskList,
    TimeHorizonOutput,
)
from src.tools.gap_finder import find_free_slots
from src.tools.time_finder import (
    _deterministic_fallback_window,
    _extract_dates_deterministically,
    find_time_window,
    serialize_notes_input,
    time_finder,
)
import time_finder as root_tf


@pytest.fixture
def tz():
    return ZoneInfo("Asia/Kolkata")


@pytest.fixture
def mock_tasks_user_scenario():
    """Matches the exact user prompt scenario:
    - krd-quiz on 17th oct
    - sp2 quiz on 23rd oct
    - dop lab-work-3pm
    """
    return [
        ScoredTask(
            title="krd-quiz on 17th oct",
            event_type=EventType.EVALUATIVE,
            start=DateTimeAnchor(dateTime="2026-10-17T10:00"),
            end=DateTimeAnchor(dateTime="2026-10-17T11:00"),
            estimated_duration=60,
            importance=5,
            urgency=4,
            is_movable=False,
            priority_category=PriorityCategory.URGENT,
            scoring_rationale="Academic evaluative quiz.",
        ),
        ScoredTask(
            title="sp2 quiz on 23rd oct",
            event_type=EventType.EVALUATIVE,
            start=DateTimeAnchor(dateTime="2026-10-23T14:00"),
            end=DateTimeAnchor(dateTime="2026-10-23T15:00"),
            estimated_duration=60,
            importance=5,
            urgency=3,
            is_movable=False,
            priority_category=PriorityCategory.IMPORTANT,
            scoring_rationale="Upcoming academic evaluation.",
        ),
        ScoredTask(
            title="dop lab-work-3pm",
            event_type=EventType.LECTURE,
            start=DateTimeAnchor(dateTime="2026-10-17T15:00"),
            end=DateTimeAnchor(dateTime="2026-10-17T17:00"),
            estimated_duration=120,
            importance=4,
            urgency=4,
            is_movable=False,
            priority_category=PriorityCategory.URGENT,
            scoring_rationale="Departmental lab session.",
        ),
    ]


def test_user_scenario_deterministic_date_extraction():
    sample_text = "krd-quiz on 17th oct and another event called sp2 quiz on 23rd oct and the latest task is dop lab-work-3pm"
    dates = _extract_dates_deterministically(sample_text, reference_year=2026)
    assert dt.date(2026, 10, 17) in dates
    assert dt.date(2026, 10, 23) in dates


def test_user_scenario_deterministic_fallback(mock_tasks_user_scenario, tz):
    now = dt.datetime(2026, 10, 17, 9, 0, 0, tzinfo=tz)
    start_dt, end_dt = _deterministic_fallback_window(
        serialize_notes_input(mock_tasks_user_scenario),
        now=now,
        tz=tz,
    )
    assert start_dt == now
    assert end_dt == dt.datetime(2026, 10, 23, 23, 59, 59, tzinfo=tz)


def test_time_finder_with_mocked_llm(mock_tasks_user_scenario, tz):
    now = dt.datetime(2026, 10, 17, 9, 30, 0, tzinfo=tz)

    mock_parsed_response = TimeHorizonOutput(
        start_datetime="2026-10-17T09:30:00+05:30",
        end_datetime="2026-10-23T23:59:59+05:30",
        latest_event_title="sp2 quiz on 23rd oct",
        reasoning="Latest event is sp2 quiz on 23rd oct; setting end datetime to 23rd oct EOD.",
    )

    mock_client = MagicMock()
    with patch("src.tools.time_finder._call_llm_time_finder", return_value=mock_parsed_response):
        start_dt, end_dt = time_finder(
            notes_data=mock_tasks_user_scenario,
            now=now,
            tz_name="Asia/Kolkata",
            client=mock_client,
        )

    assert start_dt == dt.datetime(2026, 10, 17, 9, 30, 0, tzinfo=tz)
    assert end_dt == dt.datetime(2026, 10, 23, 23, 59, 59, tzinfo=tz)


def test_time_finder_fallback_on_llm_failure(mock_tasks_user_scenario, tz):
    now = dt.datetime(2026, 10, 17, 9, 30, 0, tzinfo=tz)
    mock_client = MagicMock()

    with patch("src.tools.time_finder._call_llm_time_finder", side_effect=RuntimeError("API quota exceeded")):
        start_dt, end_dt = time_finder(
            notes_data=mock_tasks_user_scenario,
            now=now,
            tz_name="Asia/Kolkata",
            client=mock_client,
        )

    # Should fall back cleanly without raising
    assert start_dt == now
    assert end_dt == dt.datetime(2026, 10, 23, 23, 59, 59, tzinfo=tz)


def test_time_finder_single_day_notes(tz):
    now = dt.datetime(2026, 11, 12, 10, 0, 0, tzinfo=tz)
    single_day_tasks = [
        {"title": "Study physics at 2pm", "start": {"dateTime": "2026-11-12T14:00"}},
        {"title": "Gym workout at 6pm", "start": {"dateTime": "2026-11-12T18:00"}},
    ]

    mock_client = MagicMock()
    with patch("src.tools.time_finder._call_llm_time_finder", side_effect=Exception("Offline")):
        start_dt, end_dt = time_finder(
            notes_data=single_day_tasks,
            now=now,
            tz_name="Asia/Kolkata",
            client=mock_client,
        )

    assert start_dt == now
    assert end_dt.date() == now.date()
    assert end_dt == dt.datetime(2026, 11, 12, 23, 59, 59, tzinfo=tz)


def test_time_finder_flexible_inputs(mock_tasks_user_scenario, tz, tmp_path):
    now = dt.datetime(2026, 10, 17, 9, 0, 0, tzinfo=tz)

    # 1. Test raw JSON string
    raw_json = json.dumps([t.model_dump() for t in mock_tasks_user_scenario])
    s1, e1 = _deterministic_fallback_window(serialize_notes_input(raw_json), now, tz)
    assert e1 == dt.datetime(2026, 10, 23, 23, 59, 59, tzinfo=tz)

    # 2. Test file Path
    json_file = tmp_path / "notes.json"
    json_file.write_text(raw_json, encoding="utf-8")
    s2, e2 = _deterministic_fallback_window(serialize_notes_input(json_file), now, tz)
    assert e2 == dt.datetime(2026, 10, 23, 23, 59, 59, tzinfo=tz)

    # 3. Test ScoredTaskList model
    task_list_model = ScoredTaskList(tasks=mock_tasks_user_scenario)
    s3, e3 = _deterministic_fallback_window(serialize_notes_input(task_list_model), now, tz)
    assert e3 == dt.datetime(2026, 10, 23, 23, 59, 59, tzinfo=tz)


def test_root_facade_import():
    assert callable(root_tf.time_finder)
    assert callable(root_tf.find_time_window)
    assert root_tf.time_finder is time_finder
    assert root_tf.find_time_window is find_time_window


def test_feed_time_finder_output_to_gap_finder(mock_tasks_user_scenario, tz):
    now = dt.datetime(2026, 10, 17, 9, 0, 0, tzinfo=tz)

    mock_client = MagicMock()
    with patch("src.tools.time_finder._call_llm_time_finder", side_effect=Exception("Fallback")):
        start_window, end_window = time_finder(
            notes_data=mock_tasks_user_scenario,
            now=now,
            tz_name="Asia/Kolkata",
            client=mock_client,
        )

    # Feed directly into gap_finder
    existing_events = [
        CalendarEvent(
            id="1",
            summary="Team Sync",
            start=dt.datetime(2026, 10, 17, 10, 0, 0, tzinfo=tz),
            end=dt.datetime(2026, 10, 17, 11, 0, 0, tzinfo=tz),
        )
    ]

    slots = find_free_slots(
        events=existing_events,
        start=start_window,
        end=end_window,
        duration_min=15,
        buffer_minutes=5,
    )

    assert len(slots) >= 2
    assert slots[0].start == start_window
    assert slots[0].end == dt.datetime(2026, 10, 17, 9, 55, 0, tzinfo=tz)
    assert slots[-1].end == end_window
