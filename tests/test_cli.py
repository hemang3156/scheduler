from unittest.mock import patch, MagicMock
import pytest
from src.interfaces.cli import parse_args, main
from src.core.models import ScoredTask, EventType, PriorityCategory, DateTimeAnchor, SchedulePlan, PlacedTask
import datetime as dt
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Kolkata")


def test_cli_parse_args_positional_note():
    args = parse_args(["Guitar", "practice", "8pm", "--dry-run"])
    assert args.note_words == ["Guitar", "practice", "8pm"]
    assert args.dry_run is True


def test_cli_parse_args_tomorrow_flag():
    args = parse_args(["--tomorrow", "--buffer", "10"])
    assert args.tomorrow is True
    assert args.buffer == 10


def test_cli_parse_args_explicit_date():
    args = parse_args(["--date", "2026-12-25"])
    assert args.date == "2026-12-25"


@patch("src.interfaces.cli.CalendarClient")
@patch("src.interfaces.cli.extract_and_score_notes")
def test_cli_main_dry_run_positional(mock_extract, mock_calendar_client):
    mock_task = ScoredTask(
        title="Guitar Practice",
        event_type=EventType.ROUTINE,
        start=DateTimeAnchor(dateTime=None),
        end=DateTimeAnchor(dateTime=None),
        estimated_duration=30,
        importance=3,
        urgency=3,
        is_movable=True,
        priority_category=PriorityCategory.ROUTINE,
        scoring_rationale="Personal routine",
    )
    mock_extract.return_value = [mock_task]

    # Run main with dry-run
    main(["Guitar", "practice", "8pm", "--dry-run"])

    mock_extract.assert_called_once_with("Guitar practice 8pm")
    # In dry-run mode, CalendarClient should not be initialized
    assert not mock_calendar_client.called
