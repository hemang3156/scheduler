from unittest.mock import MagicMock, patch
import pytest

from method import delete_event, delete_events
from src.tools.calender_client import CalendarClient
from src.core.exceptions import CalendarResourceNotFoundError


def test_client_delete_events():
    mock_service = MagicMock()
    mock_events = MagicMock()
    mock_service.events.return_value = mock_events
    mock_delete = MagicMock()
    mock_events.delete.return_value = mock_delete
    mock_delete.execute.return_value = {}

    client = CalendarClient.__new__(CalendarClient)
    client.calendar_id = "primary"
    client.service = mock_service

    result = client.delete_events(["id_1", "id_2", "id_3"])
    assert result == ["id_1", "id_2", "id_3"]
    assert mock_events.delete.call_count == 3


def test_method_delete_event_and_events():
    mock_client = MagicMock(spec=CalendarClient)
    mock_client.delete_events.return_value = ["ev_1", "ev_2"]

    # Test single delete
    res_single = delete_event("ev_1", client=mock_client)
    assert res_single == "ev_1"
    mock_client.delete_event.assert_called_once_with("ev_1")

    # Test multiple delete
    res_multi = delete_events(["ev_1", "ev_2"], client=mock_client)
    assert res_multi == ["ev_1", "ev_2"]
    mock_client.delete_events.assert_called_once_with(["ev_1", "ev_2"])

    # Test passing single string to delete_events
    delete_events("ev_1", client=mock_client)
    mock_client.delete_events.assert_called_with(["ev_1"])
