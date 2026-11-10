"""Calendar helper methods module (method.py).

Provides standalone calendar manipulation methods including delete_events.
"""
from typing import List, Optional, Union
from src.tools.calender_client import CalendarClient
from src.core.models import CalendarEvent


def get_client() -> CalendarClient:
    """Instantiate and return the configured CalendarClient."""
    return CalendarClient()


def delete_event(event_id: str, client: Optional[CalendarClient] = None) -> str:
    """Delete a single event from Google Calendar by ID."""
    c = client or get_client()
    c.delete_event(event_id)
    return event_id


def delete_events(
    event_ids: Union[str, List[str]], client: Optional[CalendarClient] = None
) -> List[str]:
    """
    Delete one or more events from Google Calendar by ID.

    Args:
        event_ids: Single event ID string or list of event ID strings.
        client: Optional CalendarClient instance.

    Returns:
        List of deleted event IDs.
    """
    c = client or get_client()
    if isinstance(event_ids, str):
        event_ids = [event_ids]
    return c.delete_events(event_ids)
