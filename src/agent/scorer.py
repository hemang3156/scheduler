import datetime as dt
import logging
import os
import time
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Optional, List, Sequence

from google import genai
from google.genai import types
from google.genai.errors import ServerError, ClientError

from src.core.models import ScoredTaskList, ScoredTask
from src.agent.prompt_templates import (
    build_notes_extraction_prompt,
    build_scoring_prompt,
)

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PROMPT_PATH = PROJECT_ROOT / "redundant" / "prompt.md"
DEFAULT_RESPONSE_PATH = PROJECT_ROOT / "redundant" / "export" / "response.json"
TIMEZONE = "Asia/Kolkata"
DEFAULT_MODELS = (
    "gemini-3.8-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.7-flash",
)


def _get_api_key() -> str:
    """Retrieves Gemini API key from environment variable or gitignored key file."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if api_key:
        return api_key.strip()

    key_file = PROJECT_ROOT / "gemini_api_key.txt"
    if key_file.exists():
        file_key = key_file.read_text(encoding="utf-8").strip()
        if file_key:
            return file_key

    raise ValueError(
        "Gemini API key not found. Set GEMINI_API_KEY environment variable "
        "or create gemini_api_key.txt in project root."
    )


def _call_gemini_with_fallback(
    client: genai.Client,
    prompt: str,
    models: Sequence[str] = DEFAULT_MODELS,
    max_retries_per_model: int = 2,
) -> ScoredTaskList:
    """Calls Gemini with structured outputs, falling back across resilient models on 503/server spikes."""
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=ScoredTaskList,
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
                # If model is retired or not available (404), break immediately to next model
                last_error = e
                break
            except Exception as e:
                last_error = e
                # Transient 503 high-demand spike or network disconnect: short backoff before next attempt
                time.sleep(1.0 * attempt)

    if last_error:
        raise last_error
    raise RuntimeError("Failed to generate structured tasks across all candidate models.")


def sanitize_task_anchors(task: ScoredTask, reference_now: Optional[dt.datetime] = None) -> ScoredTask:
    """
    Sanitizes extracted task start and end anchors to prevent common LLM temporal glitches:
    1. Infer PM for start time if AM was assumed despite reference_now being late evening.
    2. Correct 12:xx midday confusion when 12:xx AM (midnight) was intended following an evening start.
    3. Roll forward midnight rollover when end datetime <= start datetime on the same date.
    4. Roll forward early morning deadline when reference_now is late at night.
    """
    ref_tz = reference_now.tzinfo if reference_now else None

    if not task.start or not task.start.dateTime:
        if task.end and task.end.dateTime and reference_now:
            try:
                e = dt.datetime.fromisoformat(task.end.dateTime)
                if ref_tz and e.tzinfo is None:
                    e = e.replace(tzinfo=ref_tz)
                if e.date() == reference_now.date() and e < reference_now and reference_now.hour >= 18:
                    if e.hour == 12:
                        e = e.replace(hour=0) + dt.timedelta(days=1)
                        task.end.dateTime = e.strftime("%Y-%m-%dT%H:%M")
                    elif e.hour < 12:
                        e = e + dt.timedelta(days=1)
                        task.end.dateTime = e.strftime("%Y-%m-%dT%H:%M")
            except (ValueError, TypeError):
                pass
        return task

    try:
        s = dt.datetime.fromisoformat(task.start.dateTime)
        if ref_tz and s.tzinfo is None:
            s = s.replace(tzinfo=ref_tz)
    except (ValueError, TypeError):
        return task

    # Preserve all-day milestone anchors (00:00:00 to 23:59:59)
    if s.time() == dt.time(0, 0) and task.end and task.end.dateTime:
        try:
            e_check = dt.datetime.fromisoformat(task.end.dateTime)
            if e_check.time() in (dt.time(23, 59, 59), dt.time(23, 59)):
                return task
        except (ValueError, TypeError):
            pass

    # If start is in the morning of today, but reference_now is evening, shift to PM if that puts it in the future
    if reference_now and s.date() == reference_now.date() and s < reference_now and s.hour < 12:
        s_pm = s + dt.timedelta(hours=12)
        if s_pm >= reference_now:
            s = s_pm
            task.start.dateTime = s.strftime("%Y-%m-%dT%H:%M")

    if not task.end or not task.end.dateTime:
        return task

    try:
        e = dt.datetime.fromisoformat(task.end.dateTime)
        if ref_tz and e.tzinfo is None:
            e = e.replace(tzinfo=ref_tz)
    except (ValueError, TypeError):
        return task

    # 12 AM vs 12 PM confusion: 12:xx midday output for midnight after evening start
    if e.date() == s.date() and e.hour == 12 and s.hour >= 18:
        e = e.replace(hour=0) + dt.timedelta(days=1)
        task.end.dateTime = e.strftime("%Y-%m-%dT%H:%M")

    # Midnight rollover: end time on same date is earlier than start time
    if e <= s and e.date() == s.date():
        e = e + dt.timedelta(days=1)
        task.end.dateTime = e.strftime("%Y-%m-%dT%H:%M")

    return task


def score_tasks_from_json(
    response_json_path: Path = DEFAULT_RESPONSE_PATH,
    models: Sequence[str] = DEFAULT_MODELS,
    api_key: Optional[str] = None,
    now: Optional[dt.datetime] = None,
) -> List[ScoredTask]:
    """
    Reads tasks from response.json, fills scoring prompt with runtime context,
    calls Gemini with ScoredTaskList schema, and returns validated ScoredTask instances.
    """
    resolved_key = api_key or _get_api_key()
    client = genai.Client(api_key=resolved_key)

    raw_json_text = Path(response_json_path).read_text(encoding="utf-8")
    if now is None:
        now = dt.datetime.now(ZoneInfo(TIMEZONE))
    elif now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(TIMEZONE))

    if PROMPT_PATH.exists():
        prompt_template = Path(PROMPT_PATH).read_text(encoding="utf-8")
        filled_prompt = (
            prompt_template
            .replace("{current_date}", now.strftime("%Y-%m-%d"))
            .replace("{timezone}", TIMEZONE)
            .replace("{response_json}", raw_json_text)
        )
    else:
        filled_prompt = build_scoring_prompt(
            raw_json_text, now.strftime("%Y-%m-%d"), TIMEZONE
        )

    parsed_output = _call_gemini_with_fallback(client, filled_prompt, models=models)
    return [sanitize_task_anchors(t, reference_now=now) for t in parsed_output.tasks]


def extract_and_score_notes(
    raw_notes_text: str,
    models: Sequence[str] = DEFAULT_MODELS,
    api_key: Optional[str] = None,
    now: Optional[dt.datetime] = None,
) -> List[ScoredTask]:
    """
    Parses messy informal natural language notes directly into structured ScoredTasks.
    """
    resolved_key = api_key or _get_api_key()
    client = genai.Client(api_key=resolved_key)

    if now is None:
        now = dt.datetime.now(ZoneInfo(TIMEZONE))
    elif now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo(TIMEZONE))

    next_date = (now + dt.timedelta(days=1)).strftime("%Y-%m-%d")
    current_time_str = now.strftime("%I:%M %p")

    filled_prompt = build_notes_extraction_prompt(
        raw_notes_text,
        now.strftime("%Y-%m-%d"),
        TIMEZONE,
        current_time=current_time_str,
        next_date=next_date,
    )

    parsed_output = _call_gemini_with_fallback(client, filled_prompt, models=models)
    return [sanitize_task_anchors(t, reference_now=now) for t in parsed_output.tasks]
