from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

from src.core.models import CalendarEvent, TaskDraft, TimeSlot, PreferenceProfile


def test_calendar_event_defaults(base_day: datetime):
    start = base_day.replace(hour=10)
    end = base_day.replace(hour=11)

    ev = CalendarEvent(id="123", summary="Team Standup", start=start, end=end)
    assert ev.id == "123"
    assert ev.summary == "Team Standup"
    assert ev.start == start
    assert ev.end == end
    assert ev.is_all_day is False


def test_task_draft_default_factory_isolation():
    """Ensure mutable default tags are independent instances across TaskDrafts."""
    t1 = TaskDraft(title="Fix bug", importance=4, urgency=5)
    t2 = TaskDraft(title="Write docs", importance=2, urgency=2)

    t1.tags.append("backend")

    assert t1.tags == ["backend"]
    assert t2.tags == []
    assert t1.tags is not t2.tags


def test_time_slot_attributes(base_day: datetime):
    start = base_day.replace(hour=14)
    end = base_day.replace(hour=15, minute=30)

    slot = TimeSlot(start=start, end=end, duration_minutes=90)
    assert slot.start == start
    assert slot.end == end
    assert slot.duration_minutes == 90


def test_preference_profile_initialization():
    profile = PreferenceProfile(profile_name="test_profile")
    assert profile.profile_name == "test_profile"
    assert profile.updated_count == 0
    assert len(profile.hourly_weights) == 24
    assert all(w == 0.0 for w in profile.hourly_weights)
    assert profile.get_weight(0) == 0.0
    assert profile.get_weight(23) == 0.0

    with pytest.raises(ValueError):
        profile.get_weight(24)

    with pytest.raises(ValueError):
        profile.get_weight(-1)


def test_global_baseline_json_matches_schema():
    baseline_path = Path(__file__).resolve().parent.parent / "data" / "preference_profiles" / "global_baseline.json"
    assert baseline_path.exists()

    with open(baseline_path, encoding="utf-8") as f:
        data = json.load(f)

    profile = PreferenceProfile(**data)
    assert profile.profile_name == "global_baseline"
    assert profile.updated_count >= 0
    assert len(profile.hourly_weights) == 24

