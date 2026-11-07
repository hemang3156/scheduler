from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from typing import Callable
import pytest

from src.core.models import CalendarEvent


def pytest_configure(config):
    config.addinivalue_line("markers", "evals: live LLM regression evaluation benchmarks")


@pytest.fixture
def tz() -> ZoneInfo:
    return ZoneInfo("Asia/Kolkata")


@pytest.fixture
def base_day(tz: ZoneInfo) -> datetime:
    """Fixed reference date for deterministic testing."""
    return datetime(2026, 11, 4, 0, 0, 0, tzinfo=tz)


@pytest.fixture
def window(base_day: datetime) -> tuple[datetime, datetime]:
    """A standard 8-hour workday window: 09:00 to 17:00 (480 minutes total)."""
    start = base_day.replace(hour=9, minute=0)
    end = base_day.replace(hour=17, minute=0)
    return start, end


@pytest.fixture
def make_event(base_day: datetime) -> Callable[..., CalendarEvent]:
    """
    Factory fixture to quickly create CalendarEvent instances 
    using relative hours/minutes from base_day.
    """
    def _factory(
        summary: str,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
        event_id: str = "evt_test",
        is_all_day: bool = False,
    ) -> CalendarEvent:
        start = base_day.replace(hour=start_hour, minute=start_minute)
        end = base_day.replace(hour=end_hour, minute=end_minute)
        return CalendarEvent(
            id=event_id,
            summary=summary,
            start=start,
            end=end,
            is_all_day=is_all_day,
        )

    return _factory
