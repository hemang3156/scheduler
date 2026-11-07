import json
import logging
import time
from typing import Optional, Sequence

from google import genai
from google.genai import types
from google.genai.errors import ServerError, ClientError

from src.core.models import (
    CalendarEvent,
    PreferenceProfile,
    UpdatedCurveResponse,
)
from src.core.preference_engine import (
    save_profile,
    recompute_global_baseline,
    sanitize_weights,
)
from src.agent.prompt_templates import build_curve_evolution_prompt
from src.agent.scorer import _get_api_key, DEFAULT_MODELS

logger = logging.getLogger(__name__)


def evolve_curve_with_llm(
    profile: PreferenceProfile,
    event: CalendarEvent,
    event_type: str = "routine",
    models: Sequence[str] = DEFAULT_MODELS,
    api_key: Optional[str] = None,
    max_retries_per_model: int = 2,
) -> PreferenceProfile:
    """
    Inputs current preference weights and event information into Gemini,
    retrieves updated calibrated weights, sanitizes the floats, and persists
    both the profile and the updated global baseline.
    """
    resolved_key = api_key or _get_api_key()
    client = genai.Client(api_key=resolved_key)

    duration_mins = int((event.end - event.start).total_seconds() // 60)
    current_weights_json = json.dumps(profile.hourly_weights, indent=2)

    prompt = build_curve_evolution_prompt(
        task_title=event.summary,
        event_type=event_type,
        start_time=event.start.strftime("%Y-%m-%d %H:%M"),
        end_time=event.end.strftime("%Y-%m-%d %H:%M"),
        duration_minutes=duration_mins,
        current_weights_json=current_weights_json,
        updated_count=profile.updated_count,
    )

    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        response_schema=UpdatedCurveResponse,
        temperature=0.0,
    )

    last_error: Optional[Exception] = None
    parsed_response: Optional[UpdatedCurveResponse] = None

    for model_name in models:
        for attempt in range(1, max_retries_per_model + 1):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config,
                )
                if response.parsed:
                    parsed_response = response.parsed
                    break
            except ServerError as e:
                last_error = e
                time.sleep(1.0 * attempt)
            except ClientError as e:
                last_error = e
                break
        if parsed_response:
            break

    if not parsed_response:
        if last_error:
            raise last_error
        raise RuntimeError("Failed to evolve preference curve across all candidate models.")

    # Sanitize and clamp weights in pure Python
    sanitized = sanitize_weights(parsed_response.hourly_weights)

    updated_profile = PreferenceProfile(
        profile_name=profile.profile_name,
        updated_count=profile.updated_count + 1,
        hourly_weights=sanitized,
        description=parsed_response.update_rationale,
    )

    # Persist updated profile to disk
    save_profile(updated_profile)

    # Recalculate global baseline aggregate across all active profiles
    recompute_global_baseline()

    return updated_profile
