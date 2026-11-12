"""Time Finder Tool (time_finder.py).

Analyzes structured notes / task JSON with an LLM to determine the global
scheduling window boundaries:
- start_datetime: Typically current reference time (tz.now).
- end_datetime: End of Day (23:59:59) of the latest date mentioned across all tasks/events.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple, Union
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types
from google.genai.errors import ClientError, ServerError
from pydantic import BaseModel

from src.agent.prompt_templates import build_time_finder_prompt
from src.agent.scorer import DEFAULT_MODELS, _get_api_key
from src.core.models import ScoredTask, ScoredTaskList, TimeHorizonOutput

logger = logging.getLogger(__name__)

TIMEZONE = "Asia/Kolkata"

MONTH_MAP = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}


def serialize_notes_input(notes_data: Union[str, list, dict, Path, BaseModel]) -> str:
    """Normalizes notes input (file path, list of models/dicts, or raw JSON string) into a JSON string."""
    if isinstance(notes_data, Path):
        content = notes_data.read_text(encoding="utf-8").strip()
        return content

    if isinstance(notes_data, str):
        content = notes_data.strip()
        # Verify if it's already JSON or needs wrapping
        try:
            parsed = json.loads(content)
            return json.dumps(parsed, indent=2, default=str)
        except Exception:
            return content

    if isinstance(notes_data, ScoredTaskList):
        return json.dumps([t.model_dump() for t in notes_data.tasks], indent=2, default=str)

    if isinstance(notes_data, list):
        normalized = []
        for item in notes_data:
            if isinstance(item, BaseModel):
                normalized.append(item.model_dump())
            elif isinstance(item, dict):
                normalized.append(item)
            else:
                normalized.append(str(item))
        return json.dumps(normalized, indent=2, default=str)

    if isinstance(notes_data, dict):
        return json.dumps(notes_data, indent=2, default=str)

    if isinstance(notes_data, BaseModel):
        return json.dumps(notes_data.model_dump(), indent=2, default=str)

    return json.dumps(str(notes_data))


def _extract_dates_deterministically(
    notes_json_str: str,
    reference_year: int,
) -> List[dt.date]:
    """Scans JSON string for ISO datetimes and natural language dates to extract all referenced dates."""
    dates: List[dt.date] = []

    # 1. ISO format dates (YYYY-MM-DD)
    iso_matches = re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", notes_json_str)
    for y, m, d in iso_matches:
        try:
            dates.append(dt.date(int(y), int(m), int(d)))
        except ValueError:
            pass

    # 2. Natural language dates: e.g. "17th oct", "23rd oct", "oct 23", "23 oct"
    # Format: day + month
    pattern1 = re.findall(
        r"\b(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\b",
        notes_json_str,
        re.IGNORECASE,
    )
    for day_str, month_str in pattern1:
        m_lower = month_str.lower()
        if m_lower in MONTH_MAP:
            try:
                dates.append(dt.date(reference_year, MONTH_MAP[m_lower], int(day_str)))
            except ValueError:
                pass

    # Format: month + day (e.g. "oct 23", "october 17th")
    pattern2 = re.findall(
        r"\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?\b",
        notes_json_str,
        re.IGNORECASE,
    )
    for month_str, day_str in pattern2:
        m_lower = month_str.lower()
        if m_lower in MONTH_MAP:
            try:
                dates.append(dt.date(reference_year, MONTH_MAP[m_lower], int(day_str)))
            except ValueError:
                pass

    return dates


def _deterministic_fallback_window(
    notes_json_str: str,
    now: dt.datetime,
    tz: ZoneInfo,
) -> Tuple[dt.datetime, dt.datetime]:
    """Deterministic fallback to determine start and end datetime without LLM."""
    found_dates = _extract_dates_deterministically(notes_json_str, reference_year=now.year)
    start_dt = now

    if found_dates:
        latest_date = max(found_dates)
        if latest_date < now.date():
            # If date was from a previous month/year, assume current year or today
            latest_date = now.date()
        end_dt = dt.datetime.combine(latest_date, dt.time(23, 59, 59), tzinfo=tz)
    else:
        # Default to today's end of day
        end_dt = dt.datetime.combine(now.date(), dt.time(23, 59, 59), tzinfo=tz)

    if end_dt <= start_dt:
        end_dt = start_dt + dt.timedelta(hours=24)

    return start_dt, end_dt


def _call_llm_time_finder(
    client: genai.Client,
    prompt: str,
    models: Sequence[str] = DEFAULT_MODELS,
    max_retries_per_model: int = 2,
) -> TimeHorizonOutput:
    """Calls Gemini with structured TimeHorizonOutput schema."""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=TimeHorizonOutput,
        temperature=0.0,
    )

    last_error: Optional[Exception] = None
    for model_name in models:
        for attempt in range(1, max_retries_per_model + 1):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config,
                )
                if response.parsed:
                    return response.parsed
                raise ValueError(f"Empty structured response received from model {model_name}")
            except ClientError as e:
                last_error = e
                logger.warning("ClientError on model %s: %s", model_name, e)
                break
            except Exception as e:
                last_error = e
                logger.warning("Transient error on model %s attempt %d: %s", model_name, attempt, e)
                import time
                time.sleep(1.0 * attempt)

    if last_error:
        raise last_error
    raise RuntimeError("Failed to compute time horizon across candidate models.")


def time_finder(
    notes_data: Union[str, list, dict, Path, BaseModel],
    now: Optional[dt.datetime] = None,
    tz_name: str = TIMEZONE,
    client: Optional[genai.Client] = None,
) -> Tuple[dt.datetime, dt.datetime]:
    """
    Ingests structured notes JSON and calls an LLM to extract:
    1. start_datetime: tz.now (or earliest boundary)
    2. end_datetime: End of Day (23:59:59) of the latest date mentioned.

    Args:
        notes_data: Structured notes JSON (path, raw json string, list of ScoredTasks, or dict).
        now: Reference datetime (defaults to current time in tz_name).
        tz_name: Target timezone string (default: Asia/Kolkata).
        client: Optional genai.Client instance.

    Returns:
        Tuple[dt.datetime, dt.datetime]: (start_datetime, end_datetime)
    """
    tz = ZoneInfo(tz_name)
    if now is None:
        now = dt.datetime.now(tz).replace(microsecond=0)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=tz)
    else:
        now = now.astimezone(tz)

    notes_json_str = serialize_notes_input(notes_data)

    # Initialize Gemini client if not supplied
    if client is None:
        try:
            api_key = _get_api_key()
            client = genai.Client(api_key=api_key)
        except Exception as e:
            logger.info("Gemini API key not available for time_finder (%s); using deterministic fallback.", e)
            return _deterministic_fallback_window(notes_json_str, now, tz)

    prompt = build_time_finder_prompt(
        structured_notes_json=notes_json_str,
        current_datetime=now.isoformat(),
        current_date=now.date().isoformat(),
        timezone=tz_name,
    )

    try:
        parsed_output: TimeHorizonOutput = _call_llm_time_finder(client, prompt)

        # Parse start_datetime
        raw_start = parsed_output.start_datetime.strip()
        start_dt = dt.datetime.fromisoformat(raw_start)
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=tz)
        else:
            start_dt = start_dt.astimezone(tz)

        # Parse end_datetime
        raw_end = parsed_output.end_datetime.strip()
        end_dt = dt.datetime.fromisoformat(raw_end)
        if end_dt.tzinfo is None:
            end_dt = end_dt.replace(tzinfo=tz)
        else:
            end_dt = end_dt.astimezone(tz)

        # Ensure end time is aligned to EOD (23:59:59) if LLM returned 00:00 or partial time
        if end_dt.time() == dt.time(0, 0):
            end_dt = dt.datetime.combine(end_dt.date(), dt.time(23, 59, 59), tzinfo=tz)

        # Ensure start_dt < end_dt
        if end_dt <= start_dt:
            end_dt = dt.datetime.combine(start_dt.date(), dt.time(23, 59, 59), tzinfo=tz)
            if end_dt <= start_dt:
                end_dt = start_dt + dt.timedelta(hours=24)

        return start_dt, end_dt

    except Exception as e:
        logger.warning("LLM time_finder failed (%s); reverting to deterministic fallback.", e)
        return _deterministic_fallback_window(notes_json_str, now, tz)


# Alias matching functional naming convention
find_time_window = time_finder
