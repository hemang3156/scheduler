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
DEFAULT_MODELS = ("gemini-3.5-flash", "gemini-3.8-flash", "gemini-3.7-flash")


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
            except ServerError as e:
                last_error = e
                # Transient 503 high-demand spike: short backoff before attempting next retry/model
                time.sleep(1.0 * attempt)
            except ClientError as e:
                # If model is retired or not available (404), break immediately to next model
                last_error = e
                break

    if last_error:
        raise last_error
    raise RuntimeError("Failed to generate structured tasks across all candidate models.")


def score_tasks_from_json(
    response_json_path: Path = DEFAULT_RESPONSE_PATH,
    models: Sequence[str] = DEFAULT_MODELS,
    api_key: Optional[str] = None,
) -> List[ScoredTask]:
    """
    Reads tasks from response.json, fills scoring prompt with runtime context,
    calls Gemini with ScoredTaskList schema, and returns validated ScoredTask instances.
    """
    resolved_key = api_key or _get_api_key()
    client = genai.Client(api_key=resolved_key)

    raw_json_text = Path(response_json_path).read_text(encoding="utf-8")
    now = dt.datetime.now(ZoneInfo(TIMEZONE))

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
    return parsed_output.tasks


def extract_and_score_notes(
    raw_notes_text: str,
    models: Sequence[str] = DEFAULT_MODELS,
    api_key: Optional[str] = None,
) -> List[ScoredTask]:
    """
    Parses messy informal natural language notes directly into structured ScoredTasks.
    """
    resolved_key = api_key or _get_api_key()
    client = genai.Client(api_key=resolved_key)

    now = dt.datetime.now(ZoneInfo(TIMEZONE))
    filled_prompt = build_notes_extraction_prompt(
        raw_notes_text, now.strftime("%Y-%m-%d"), TIMEZONE
    )

    parsed_output = _call_gemini_with_fallback(client, filled_prompt, models=models)
    return parsed_output.tasks
